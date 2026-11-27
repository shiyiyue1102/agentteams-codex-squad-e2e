#!/usr/bin/env python3
"""事项管理应用（Issue Board）后端入口。

仅使用 Python 标准库：http.server + sqlite3。
启动：python3 server.py --port 8000 --db items.db
"""

import argparse
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from db import init_db, row_to_item

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

VALID_STATUS = ("todo", "in_progress", "done")
VALID_PRIORITY = ("low", "medium", "high")
DEFAULT_STATUS = "todo"
DEFAULT_PRIORITY = "medium"
TITLE_MAX_LEN = 80

# SQLite 连接：每请求独立连接 + 全局写锁，保证并发请求下无 "database is locked" / 崩溃。
_DB_LOCK = threading.Lock()
_DB_PATH = None  # 由 main() 设置


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn():
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class ApiError(Exception):
    def __init__(self, http_status, code, message):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


def err(http_status, code, message):
    return ApiError(http_status, code, message)


# ---------------------------------------------------------------- 校验工具

def validate_title(raw):
    """strip 后必填且 ≤80 个 Unicode 字符；返回 strip 后的标题。"""
    if not isinstance(raw, str):
        raise err(400, "INVALID_INPUT", "title 必须是字符串")
    title = raw.strip()
    if not title:
        raise err(400, "TITLE_REQUIRED", "标题去除空白后不能为空")
    if len(title) > TITLE_MAX_LEN:
        raise err(400, "TITLE_TOO_LONG", f"标题去除空白后不能超过 {TITLE_MAX_LEN} 个字符")
    return title


def validate_status(raw):
    if raw is None:
        return DEFAULT_STATUS
    if not isinstance(raw, str) or raw not in VALID_STATUS:
        raise err(400, "INVALID_STATUS", "status 必须是 todo / in_progress / done 之一")
    return raw


def validate_priority(raw):
    if raw is None:
        return DEFAULT_PRIORITY
    if not isinstance(raw, str) or raw not in VALID_PRIORITY:
        raise err(400, "INVALID_PRIORITY", "priority 必须是 low / medium / high 之一")
    return raw


def parse_item_id(raw):
    try:
        item_id = int(raw)
    except (TypeError, ValueError):
        raise err(400, "INVALID_INPUT", "id 必须是正整数")
    if item_id <= 0 or str(item_id) != str(raw).strip():
        raise err(400, "INVALID_INPUT", "id 必须是正整数")
    return item_id


def parse_int_param(values, name, lo, hi, default):
    if not values:
        return default
    raw = values[-1]
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise err(400, "INVALID_INPUT", f"{name} 必须是整数")
    if n < lo or n > hi:
        raise err(400, "INVALID_INPUT", f"{name} 必须在 {lo}-{hi} 之间")
    return n


# ---------------------------------------------------------------- 业务处理

def handle_list_items(query):
    keyword_raw = query.get("keyword", [""])[-1]
    keyword = keyword_raw.strip() if keyword_raw else ""
    status_values = query.get("status", [])
    status_filter = None
    if status_values and status_values[-1] != "":
        status_filter = status_values[-1]
        if status_filter not in VALID_STATUS:
            raise err(400, "INVALID_STATUS", "status 必须是 todo / in_progress / done 之一")

    page = parse_int_param(query.get("page"), "page", 1, sys.maxsize, 1)
    page_size = parse_int_param(query.get("page_size"), "page_size", 1, 100, 10)

    where, params = [], []
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + escaped + "%")
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    with _DB_LOCK, get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM items{where_sql}", params
        ).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM items{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    total_pages = (total + page_size - 1) // page_size
    return {
        "items": [row_to_item(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def read_json_body(handler):
    length_header = handler.headers.get("Content-Length")
    try:
        length = int(length_header) if length_header else 0
    except ValueError:
        length = 0
    raw = handler.rfile.read(length) if length > 0 else b""
    if not raw:
        raise err(400, "JSON_PARSE_ERROR", "请求体必须是合法 JSON（不能为空）")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise err(400, "JSON_PARSE_ERROR", "请求体必须是合法 JSON")
    if not isinstance(data, dict):
        raise err(400, "INVALID_INPUT", "请求体必须是 JSON 对象")
    return data


def handle_create_item(handler):
    data = read_json_body(handler)
    if "title" not in data or data["title"] is None:
        raise err(400, "TITLE_REQUIRED", "标题去除空白后不能为空")
    title = validate_title(data["title"])
    status = validate_status(data.get("status"))
    priority = validate_priority(data.get("priority"))
    now = utc_now_iso()
    with _DB_LOCK, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO items (title, status, priority, created_at, updated_at) VALUES (?,?,?,?,?)",
            (title, status, priority, now, now),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM items WHERE id = ?", (new_id,)).fetchone()
    return 201, row_to_item(row)


def fetch_item_or_404(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise err(404, "ITEM_NOT_FOUND", f"事项 {item_id} 不存在")
    return row


def handle_get_item(item_id):
    with _DB_LOCK, get_conn() as conn:
        row = fetch_item_or_404(conn, item_id)
    return 200, row_to_item(row)


def handle_update_item(handler, item_id):
    data = read_json_body(handler)
    updates = {}
    if "title" in data:
        if data["title"] is None:
            raise err(400, "TITLE_REQUIRED", "标题去除空白后不能为空")
        updates["title"] = validate_title(data["title"])
    if "status" in data:
        updates["status"] = validate_status(data["status"])
    if "priority" in data:
        updates["priority"] = validate_priority(data["priority"])
    if not updates:
        raise err(400, "INVALID_INPUT", "至少需要提供一个可更新字段（title/status/priority）")

    with _DB_LOCK, get_conn() as conn:
        fetch_item_or_404(conn, item_id)
        updates["updated_at"] = utc_now_iso()
        set_sql = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE items SET {set_sql} WHERE id = ?",
            list(updates.values()) + [item_id],
        )
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return 200, row_to_item(row)


def handle_delete_item(item_id):
    with _DB_LOCK, get_conn() as conn:
        fetch_item_or_404(conn, item_id)
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return 204, None


# ---------------------------------------------------------------- HTTP 层

class Handler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础响应工具（覆盖默认 HTML 错误页：所有错误均为 JSON） ----

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_no_content(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_error(self, code, message=None, explain=None):
        """覆盖默认实现：/api 下 404/405 等必须是 JSON 错误体。"""
        mapping = {404: ("NOT_FOUND", "路径不存在"), 405: ("METHOD_NOT_ALLOWED", "方法不被允许")}
        err_code, err_msg = mapping.get(code, ("INTERNAL_ERROR", message or "服务端错误"))
        try:
            self._send_json(code, {"error": {"code": err_code, "message": err_msg}})
        except Exception:
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] %s\n" % (fmt % args))

    # ---- 路由 ----

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                self._route_api(method, path, parse_qs(parsed.query, keep_blank_values=True))
            else:
                if method != "GET":
                    raise err(405, "METHOD_NOT_ALLOWED", "静态资源仅支持 GET")
                self._serve_static(path)
        except ApiError as e:
            self._send_json(e.http_status, {"error": {"code": e.code, "message": e.message}})
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 —— 未预期异常兜底为 500 JSON
            self._send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": f"服务端内部错误: {e}"}})

    def _route_api(self, method, path, query):
        if path == "/api/items" or path == "/api/items/":
            if method == "GET":
                status, payload = 200, handle_list_items(query)
            elif method == "POST":
                status, payload = handle_create_item(self)
            else:
                raise err(405, "METHOD_NOT_ALLOWED", f"/api/items 不支持 {method} 方法")
            self._send_json(status, payload)
            return

        prefix = "/api/items/"
        if path.startswith(prefix) and path[len(prefix):].strip("/"):
            remainder = path[len(prefix):].strip("/")
            if "/" in remainder:
                raise err(404, "NOT_FOUND", "路径不存在")
            item_id = parse_item_id(remainder)
            if method == "GET":
                status, payload = handle_get_item(item_id)
            elif method == "PUT":
                status, payload = handle_update_item(self, item_id)
            elif method == "DELETE":
                status, payload = handle_delete_item(item_id)
            else:
                raise err(405, "METHOD_NOT_ALLOWED", f"/api/items/{{id}} 不支持 {method} 方法")
            if status == 204:
                self._send_no_content()
            else:
                self._send_json(status, payload)
            return

        raise err(404, "NOT_FOUND", "路径不存在")

    # ---- 静态文件 ----

    def _serve_static(self, path):
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.normpath(STATIC_DIR) + os.sep) and full != os.path.normpath(STATIC_DIR):
            self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "路径不存在"}})
            return
        if not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "index.html")  # SPA 回退
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("GET")


def main(argv=None):
    global _DB_PATH
    parser = argparse.ArgumentParser(description="事项管理应用后端（Python 标准库 + SQLite）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--db", default="items.db", help="SQLite 数据库文件路径，默认 items.db")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    args = parser.parse_args(argv)

    _DB_PATH = os.path.abspath(args.db)
    db_dir = os.path.dirname(_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    init_db(_DB_PATH)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Issue Board 已启动: http://{args.host}:{args.port}  (db={_DB_PATH})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
