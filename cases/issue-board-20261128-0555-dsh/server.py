#!/usr/bin/env python3
"""事项管理应用后端（Issue Board）。

仅使用 Python 3 标准库：http.server + sqlite3，无任何外部运行依赖。
同时服务静态前端（static/）与 JSON API（/api/*）。

启动命令契约（README 必须一致）：
    python3 server.py [--host HOST] [--port PORT] [--db DB_PATH]

  --host  默认 127.0.0.1；独立测试阶段须以 --host 0.0.0.0 提供网络可达预览
  --port  默认 8000
  --db    默认脚本所在目录下 items.db；相对路径按脚本所在目录解析。
          临时数据库测试方式：--db "$(mktemp -d)/items_test.db"

错误响应统一信封：{"error": {"code", "message", "details"?}}
错误码：BAD_REQUEST / VALIDATION_ERROR / NOT_FOUND / METHOD_NOT_ALLOWED / INTERNAL_ERROR
校验优先级：body 解析(BAD_REQUEST) -> 字段/参数校验(VALIDATION_ERROR) -> 存在性(NOT_FOUND)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

VALID_STATUS = ("todo", "in_progress", "done")
VALID_PRIORITY = ("high", "medium", "low")
MAX_TITLE_LEN = 80
MAX_BODY_BYTES = 1 * 1024 * 1024
UINT_RE = re.compile(r"[0-9]+")
ITEM_PATH_RE = re.compile(r"/api/items/([^/]+)")

# 在 main() 中设置；单进程内全局共享该路径，连接按请求打开。
DB_PATH = None


class ApiError(Exception):
    """携带 HTTP 状态码、机器可读错误码与中文说明的 API 错误。"""

    def __init__(self, status, code, message, details=None, allow=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.allow = allow


def now_iso():
    """ISO 8601 UTC 秒级时间戳：YYYY-MM-DDTHH:MM:SSZ"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'todo'
                           CHECK (status IN ('todo', 'in_progress', 'done')),
                priority   TEXT NOT NULL DEFAULT 'medium'
                           CHECK (priority IN ('high', 'medium', 'low')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_item(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def parse_item_id(raw):
    """路径 {id} 必须是正整数，否则 400 VALIDATION_ERROR。"""
    if not raw or not UINT_RE.fullmatch(raw):
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "id 必须是正整数",
            [{"field": "id", "message": "id 必须是正整数"}],
        )
    value = int(raw)
    if value <= 0:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "id 必须是正整数",
            [{"field": "id", "message": "id 必须是正整数"}],
        )
    return value


def validate_title(raw):
    if not isinstance(raw, str):
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "标题必须是字符串",
            [{"field": "title", "message": "标题必须是字符串"}],
        )
    title = raw.strip()
    if not title:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "标题去除空白后不能为空",
            [{"field": "title", "message": "标题去除空白后不能为空"}],
        )
    if len(title) > MAX_TITLE_LEN:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "标题不能超过 %d 个字符" % MAX_TITLE_LEN,
            [{"field": "title", "message": "标题不能超过 %d 个字符" % MAX_TITLE_LEN}],
        )
    return title


def validate_status(raw):
    if not isinstance(raw, str) or raw not in VALID_STATUS:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "status 必须是 todo / in_progress / done 之一",
            [{"field": "status", "message": "status 必须是 todo / in_progress / done 之一"}],
        )
    return raw


def validate_priority(raw):
    if not isinstance(raw, str) or raw not in VALID_PRIORITY:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "priority 必须是 high / medium / low 之一",
            [{"field": "priority", "message": "priority 必须是 high / medium / low 之一"}],
        )
    return raw


class Handler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"

    # ---- HTTP 方法分发 -------------------------------------------------

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("HEAD")

    def do_PATCH(self):
        self._route("PATCH")

    def do_OPTIONS(self):
        self._route("OPTIONS")

    # ---- 路由 ----------------------------------------------------------

    def _route(self, method):
        head_only = method == "HEAD"
        effective = "GET" if head_only else method
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                if effective != "GET":
                    raise ApiError(
                        405, "METHOD_NOT_ALLOWED",
                        "该路径不支持 %s 方法" % method, allow="GET, HEAD",
                    )
                self._serve_static("index.html", "text/html; charset=utf-8", head_only)
            elif path == "/style.css":
                if effective != "GET":
                    raise ApiError(
                        405, "METHOD_NOT_ALLOWED",
                        "该路径不支持 %s 方法" % method, allow="GET, HEAD",
                    )
                self._serve_static("style.css", "text/css; charset=utf-8", head_only)
            elif path == "/app.js":
                if effective != "GET":
                    raise ApiError(
                        405, "METHOD_NOT_ALLOWED",
                        "该路径不支持 %s 方法" % method, allow="GET, HEAD",
                    )
                self._serve_static("app.js", "application/javascript; charset=utf-8", head_only)
            elif path in ("/api/items", "/api/items/"):
                if effective == "GET":
                    self._api_list(parse_qs(parsed.query, keep_blank_values=True), head_only)
                elif effective == "POST":
                    self._api_create(head_only)
                else:
                    raise ApiError(
                        405, "METHOD_NOT_ALLOWED",
                        "该路径不支持 %s 方法" % method, allow="GET, POST, HEAD",
                    )
            else:
                match = ITEM_PATH_RE.fullmatch(path)
                if match:
                    raw_id = match.group(1)
                    if effective == "GET":
                        self._api_get_one(raw_id, head_only)
                    elif effective == "PUT":
                        self._api_update(raw_id, head_only)
                    elif effective == "DELETE":
                        self._api_delete(raw_id, head_only)
                    else:
                        raise ApiError(
                            405, "METHOD_NOT_ALLOWED",
                            "该路径不支持 %s 方法" % method, allow="GET, PUT, DELETE, HEAD",
                        )
                else:
                    raise ApiError(404, "NOT_FOUND", "资源不存在：%s" % path)
        except ApiError as err:
            self._send_error(err)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._send_error(ApiError(500, "INTERNAL_ERROR", "服务器内部错误"))

    # ---- 静态文件 -------------------------------------------------------

    def _serve_static(self, name, content_type, head_only):
        file_path = os.path.join(STATIC_DIR, name)
        try:
            with open(file_path, "rb") as handle:
                data = handle.read()
        except OSError:
            self._send_error(ApiError(404, "NOT_FOUND", "资源不存在：%s" % name))
            return
        self._send_bytes(200, data, content_type, head_only)

    # ---- API 实现 -------------------------------------------------------

    def _api_list(self, query, head_only):
        where, params = [], []

        q_values = query.get("q")
        q = q_values[0] if q_values else ""
        if q:  # 空字符串等同未提供
            escaped = (
                q.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            where.append("title LIKE ? ESCAPE '\\'")
            params.append("%" + escaped + "%")

        status_values = query.get("status")
        if status_values is not None:
            status = validate_status(status_values[0])
            where.append("status = ?")
            params.append(status)

        page = self._int_param(query, "page", 1, 1, None)
        page_size = self._int_param(query, "page_size", 10, 1, 100)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        conn = get_conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM items" + where_sql, params
            ).fetchone()[0]
            offset = (page - 1) * page_size
            rows = conn.execute(
                "SELECT * FROM items" + where_sql + " ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        finally:
            conn.close()

        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        self._send_json(
            200,
            {
                "items": [row_to_item(row) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            head_only,
        )

    def _api_get_one(self, raw_id, head_only):
        item_id = parse_item_id(raw_id)
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "事项不存在：id=%d" % item_id)
        self._send_json(200, row_to_item(row), head_only)

    def _api_create(self, head_only):
        body = self._read_json_object()
        if "title" not in body:
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "缺少必填字段 title",
                [{"field": "title", "message": "缺少必填字段 title"}],
            )
        title = validate_title(body["title"])
        status = validate_status(body["status"]) if "status" in body else "todo"
        priority = validate_priority(body["priority"]) if "priority" in body else "medium"
        stamp = now_iso()
        conn = get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO items (title, status, priority, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (title, status, priority, stamp, stamp),
            )
            conn.commit()  # 写操作即时提交，保证服务重启后数据保留
            item = {
                "id": cursor.lastrowid,
                "title": title,
                "status": status,
                "priority": priority,
                "created_at": stamp,
                "updated_at": stamp,
            }
        finally:
            conn.close()
        self._send_json(201, item, head_only)

    def _api_update(self, raw_id, head_only):
        # 校验优先级约定：body 解析 -> 字段校验 -> 路径 id 校验 -> 存在性
        body = self._read_json_object()
        updates = {}
        if "title" in body:
            updates["title"] = validate_title(body["title"])
        if "status" in body:
            updates["status"] = validate_status(body["status"])
        if "priority" in body:
            updates["priority"] = validate_priority(body["priority"])
        # id / created_at / updated_at / 未知字段一律忽略、不报错
        if not updates:
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "至少需要一个可更新字段（title / status / priority）",
            )
        item_id = parse_item_id(raw_id)
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise ApiError(404, "NOT_FOUND", "事项不存在：id=%d" % item_id)
            set_sql = ", ".join("%s = ?" % key for key in updates)  # 键来自白名单，安全
            conn.execute(
                "UPDATE items SET " + set_sql + ", updated_at = ? WHERE id = ?",
                list(updates.values()) + [now_iso(), item_id],
            )
            conn.commit()
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            item = row_to_item(row)
        finally:
            conn.close()
        self._send_json(200, item, head_only)

    def _api_delete(self, raw_id, head_only):
        item_id = parse_item_id(raw_id)
        conn = get_conn()
        try:
            row = conn.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise ApiError(404, "NOT_FOUND", "事项不存在：id=%d" % item_id)
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {"id": item_id, "deleted": True}, head_only)

    # ---- 参数与请求体解析 -------------------------------------------------

    def _int_param(self, query, name, default, low, high):
        values = query.get(name)
        if values is None:
            return default
        raw = values[0]
        if not UINT_RE.fullmatch(raw):
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "%s 必须是整数" % name,
                [{"field": name, "message": "%s 必须是整数" % name}],
            )
        value = int(raw)
        if value < low or (high is not None and value > high):
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "%s 超出允许范围" % name,
                [{"field": name, "message": "%s 超出允许范围" % name}],
            )
        return value

    def _read_json_object(self):
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError(400, "BAD_REQUEST", "请求体必须是合法的 JSON 对象")
        if length > MAX_BODY_BYTES:
            raise ApiError(400, "BAD_REQUEST", "请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError(400, "BAD_REQUEST", "请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "BAD_REQUEST", "请求体必须是 JSON 对象")
        return data

    # ---- 响应发送 --------------------------------------------------------

    def _send_json(self, status, payload, head_only=False, allow=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", head_only, allow)

    def _send_error(self, err):
        payload = {"error": {"code": err.code, "message": err.message}}
        if err.details is not None:
            payload["error"]["details"] = err.details
        self._send_json(err.status, payload, False, err.allow)

    def _send_bytes(self, status, body, content_type, head_only=False, allow=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only and body:
            self.wfile.write(body)


def main(argv=None):
    global DB_PATH

    parser = argparse.ArgumentParser(
        description="事项管理应用：Python 标准库 http.server + sqlite3"
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite 文件路径，默认脚本所在目录下 items.db；相对路径按脚本所在目录解析",
    )
    args = parser.parse_args(argv)

    db_arg = args.db if args.db else "items.db"
    db_path = db_arg if os.path.isabs(db_arg) else os.path.join(BASE_DIR, db_arg)
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    DB_PATH = db_path
    init_db()

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print("无法监听 %s:%d —— %s" % (args.host, args.port, exc), file=sys.stderr)
        return 1

    print(
        "Issue Board 已启动：http://%s:%d/  (db: %s)" % (args.host, args.port, DB_PATH),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
