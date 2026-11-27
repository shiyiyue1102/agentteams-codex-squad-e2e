#!/usr/bin/env python3
"""事项管理应用（Issue Board）后端服务。

仅使用 Python 标准库：http.server + sqlite3 + json + argparse。
启动示例：
    python3 server.py --port 8000
    python3 server.py --port 8000 --db /tmp/board-test.db --host 0.0.0.0
"""

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VALID_STATUS = ("todo", "doing", "done")
VALID_PRIORITY = ("low", "medium", "high")
TITLE_MAX = 80
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

DB_PATH = os.path.join(BASE_DIR, "board.db")  # 由 main() 按 --db 覆盖

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 80),
    status      TEXT NOT NULL DEFAULT 'todo'
                CHECK (status IN ('todo', 'doing', 'done')),
    priority    TEXT NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low', 'medium', 'high')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC, id DESC);
"""


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def row_to_item(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ApiError(Exception):
    def __init__(self, http_status, code, message):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


# ---------- 校验 ----------

def validate_title(value):
    if not isinstance(value, str):
        raise ApiError(400, "INVALID_TITLE", "title 必须是字符串")
    title = value.strip()
    if not title:
        raise ApiError(400, "INVALID_TITLE", "title 去除两端空白后不能为空")
    if len(title) > TITLE_MAX:
        raise ApiError(400, "INVALID_TITLE", f"title 长度不能超过 {TITLE_MAX} 字符（当前 {len(title)}）")
    return title


def validate_status(value, field="status"):
    if value not in VALID_STATUS:
        raise ApiError(400, "INVALID_STATUS",
                       f"{field} 必须是 {'/'.join(VALID_STATUS)} 之一，收到 {value!r}")
    return value


def validate_priority(value, field="priority"):
    if value not in VALID_PRIORITY:
        raise ApiError(400, "INVALID_PRIORITY",
                       f"{field} 必须是 {'/'.join(VALID_PRIORITY)} 之一，收到 {value!r}")
    return value


def parse_json_body(raw):
    if not raw or not raw.strip():
        raise ApiError(400, "INVALID_JSON", "请求体不能为空，必须是合法 JSON")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "INVALID_JSON", "请求体不是合法 JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "INVALID_JSON", "请求体必须是 JSON 对象")
    return data


def parse_positive_int(values, name, default, maximum=None):
    if name not in values:
        return default
    raw = values[name][0]
    if not re.fullmatch(r"\d+", raw):
        raise ApiError(400, "INVALID_REQUEST", f"{name} 必须是正整数，收到 {raw!r}")
    num = int(raw)
    if num < 1:
        raise ApiError(400, "INVALID_REQUEST", f"{name} 必须是正整数，收到 {num}")
    if maximum is not None and num > maximum:
        raise ApiError(400, "INVALID_REQUEST", f"{name} 不能超过 {maximum}，收到 {num}")
    return num


# ---------- API 处理 ----------

def handle_list_items(query):
    page = parse_positive_int(query, "page", 1)
    page_size = parse_positive_int(query, "page_size", DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)

    where = []
    params = []
    if "q" in query:
        q = query["q"][0]
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("lower(title) LIKE lower(?) ESCAPE '\\'")
        params.append(f"%{escaped}%")
    if "status" in query:
        status = query["status"][0]
        validate_status(status, field="查询参数 status")
        where.append("status = ?")
        params.append(status)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    try:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM items {where_sql}", params).fetchone()["c"]
        total_pages = max(1, -(-total // page_size))
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM items {where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    finally:
        conn.close()
    return 200, {
        "items": [row_to_item(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def handle_create_item(body):
    data = parse_json_body(body)
    if "title" not in data:
        raise ApiError(400, "INVALID_TITLE", "缺少必填字段 title")
    title = validate_title(data["title"])
    status = validate_status(data.get("status", "todo"))
    priority = validate_priority(data.get("priority", "medium"))
    now = utc_now_iso()
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO items (title, status, priority, created_at, updated_at) VALUES (?,?,?,?,?)",
            (title, status, priority, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return 201, row_to_item(row)


def handle_get_item(item_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ApiError(404, "ITEM_NOT_FOUND", f"事项 {item_id} 不存在")
    return 200, row_to_item(row)


def handle_update_item(item_id, body):
    data = parse_json_body(body)
    for field in ("title", "status", "priority"):
        if field not in data:
            if field == "title":
                raise ApiError(400, "INVALID_TITLE", "编辑时缺少必填字段 title")
            raise ApiError(400, "INVALID_REQUEST", f"编辑时字段 {field} 必填")
    title = validate_title(data["title"])
    status = validate_status(data["status"])
    priority = validate_priority(data["priority"])
    now = utc_now_iso()
    conn = get_conn()
    try:
        exists = conn.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if exists is None:
            raise ApiError(404, "ITEM_NOT_FOUND", f"事项 {item_id} 不存在")
        conn.execute(
            "UPDATE items SET title = ?, status = ?, priority = ?, updated_at = ? WHERE id = ?",
            (title, status, priority, now, item_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()
    return 200, row_to_item(row)


def handle_delete_item(item_id):
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    if deleted == 0:
        raise ApiError(404, "ITEM_NOT_FOUND", f"事项 {item_id} 不存在")
    return 204, None


ITEM_PATH_RE = re.compile(r"^/api/items/([^/]+)/?$")

SAFE_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"

    def log_message(self, fmt, *args):  # 安静模式
        pass

    # ----- 响应工具 -----
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_api_error(self, status, code, message):
        self.send_json(status, {"error": {"code": code, "message": message}})

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", errors="replace")

    # ----- 路由 -----
    def route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                self.route_api(method, path, parsed)
            else:
                self.route_static(method, path)
        except ApiError as e:
            self.send_api_error(e.http_status, e.code, e.message)
        except BrokenPipeError:
            pass
        except Exception as e:  # 兜底：未预期异常
            self.send_api_error(500, "INTERNAL_ERROR", f"服务端内部错误：{e}")

    def route_api(self, method, path, parsed):
        query = parse_qs(parsed.query)
        if path.rstrip("/") == "/api/items" or path == "/api/items":
            if method == "GET":
                status, payload = handle_list_items(query)
                return self.send_json(status, payload)
            if method == "POST":
                status, payload = handle_create_item(self.read_body())
                return self.send_json(status, payload)
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"/api/items 不支持 {method} 方法")
        m = ITEM_PATH_RE.match(path)
        if m:
            raw_id = m.group(1)
            if not re.fullmatch(r"\d+", raw_id) or int(raw_id) < 1:
                raise ApiError(404, "ITEM_NOT_FOUND", f"事项 {raw_id!r} 不存在（id 必须是正整数）")
            item_id = int(raw_id)
            if method == "GET":
                status, payload = handle_get_item(item_id)
                return self.send_json(status, payload)
            if method == "PUT":
                status, payload = handle_update_item(item_id, self.read_body())
                return self.send_json(status, payload)
            if method == "DELETE":
                status, _ = handle_delete_item(item_id)
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"/api/items/{{id}} 不支持 {method} 方法")
        raise ApiError(404, "ITEM_NOT_FOUND", f"未知 API 路径 {path}")

    def route_static(self, method, path):
        if method != "GET" and method != "HEAD":
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"静态资源不支持 {method} 方法")
        if path in SAFE_STATIC:
            filename, ctype = SAFE_STATIC[path]
        elif path.startswith("/static/"):
            filename = path[len("/static/"):]
            if "/" in filename or filename not in ("index.html", "style.css", "app.js"):
                return self.send_404_html()
            ctype = {
                "index.html": "text/html; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
                "app.js": "application/javascript; charset=utf-8",
            }[filename]
        else:
            return self.send_404_html()
        filepath = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(filepath):
            return self.send_404_html()
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if method == "GET":
            self.wfile.write(body)

    def send_404_html(self):
        body = b"<!doctype html><meta charset='utf-8'><h1>404 Not Found</h1>"
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def do_PATCH(self):
        self.route("PATCH")


def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="事项管理应用（Issue Board）服务")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0，便于网络访问）")
    parser.add_argument("--db", default=None, help="SQLite 数据库文件路径（默认 server.py 同目录 board.db）")
    args = parser.parse_args()
    DB_PATH = args.db if args.db else os.path.join(BASE_DIR, "board.db")
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), BoardHandler)
    print(f"Issue Board 服务已启动: http://{args.host}:{args.port}/  (db={DB_PATH})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
