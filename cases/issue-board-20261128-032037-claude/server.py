#!/usr/bin/env python3
"""事项管理应用 HTTP 服务（仅 Python 标准库 + SQLite，零外部运行依赖）。

用法:
    python3 server.py [--host 127.0.0.1] [--port 8000] [--db data/issue_board.sqlite3]

- 同一进程同时提供 JSON API（/api/...）与前端静态页面（/、/static/...）。
- 启动时自动创建 SQLite 库文件（含父目录）与 items 表。
- 契约基线: Issue #93 分析报告（REST/错误码/分页/持久化）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VALID_STATUS = ("todo", "in_progress", "done")
VALID_PRIORITY = ("low", "medium", "high")
UPDATABLE_FIELDS = ("title", "status", "priority")
MAX_TITLE_CODEPOINTS = 80
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'todo'
             CHECK (status IN ('todo','in_progress','done')),
  priority   TEXT NOT NULL DEFAULT 'medium'
             CHECK (priority IN ('low','medium','high')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


class ApiError(Exception):
    """业务错误：status/code/message 与统一错误响应格式一一对应。"""

    def __init__(self, status, code, message, fields=None, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields
        self.headers = headers or {}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def error_payload(code, message, fields=None):
    err = {"code": code, "message": message}
    if fields:
        err["fields"] = fields
    return {"error": err}


# ---------------------------------------------------------------- 数据库

def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute(SCHEMA)
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


# ---------------------------------------------------------------- 校验

def clean_title(value, errors):
    if not isinstance(value, str):
        errors["title"] = "标题必须是字符串"
        return None
    title = value.strip()
    if not title:
        errors["title"] = "标题去除两端空白后不能为空"
        return None
    if len(title) > MAX_TITLE_CODEPOINTS:
        errors["title"] = (
            f"标题不能超过 {MAX_TITLE_CODEPOINTS} 字"
            f"（去除空白后当前 {len(title)} 字）"
        )
        return None
    return title


def clean_enum(field, value, allowed, label, errors):
    if not isinstance(value, str):
        errors[field] = f"{label}必须是字符串"
        return None
    if value not in allowed:
        errors[field] = f"{label}非法，合法值: {' / '.join(allowed)}（大小写敏感）"
        return None
    return value


def parse_json_object(body):
    if body is None or not body.strip():
        raise ApiError(400, "INVALID_JSON", "请求体不能为空，必须是 JSON 对象")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "INVALID_JSON", "请求体不是合法 JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "INVALID_JSON", "请求体必须是 JSON 对象")
    return data


def validate_payload(data, partial):
    """校验新增/更新请求体，返回可更新字段字典；不合法时抛 ApiError。"""
    unknown = [k for k in data if k not in UPDATABLE_FIELDS]
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            f"未知字段: {names}（仅允许 title / status / priority）",
            fields={str(k): "未知字段，不允许提交" for k in sorted(map(str, unknown))},
        )
    errors = {}
    values = {}
    if "title" in data:
        cleaned = clean_title(data["title"], errors)
        if cleaned is not None:
            values["title"] = cleaned
    elif not partial:
        errors["title"] = "title 为必填字段"
    if "status" in data:
        cleaned = clean_enum("status", data["status"], VALID_STATUS, "状态", errors)
        if cleaned is not None:
            values["status"] = cleaned
    if "priority" in data:
        cleaned = clean_enum("priority", data["priority"], VALID_PRIORITY, "优先级", errors)
        if cleaned is not None:
            values["priority"] = cleaned
    if partial and not values and not errors:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "至少需要一个可更新字段（title / status / priority）",
        )
    if errors:
        first = next(iter(errors.values()))
        raise ApiError(400, "VALIDATION_ERROR", f"参数校验失败: {first}", fields=errors)
    return values


def parse_list_params(query):
    params = urllib.parse.parse_qs(query, keep_blank_values=True)

    def first(name):
        vals = params.get(name)
        return vals[0] if vals else None

    q = first("q")
    if q is not None:
        q = q.strip()
        if q == "":
            q = None  # 宽松语义：strip 后为空视为未传

    status = first("status")
    if status is not None and status not in VALID_STATUS:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            f"status 非法，合法值: {' / '.join(VALID_STATUS)}（大小写敏感）",
            fields={"status": "必须为 todo / in_progress / done 之一"},
        )

    page = 1
    raw_page = first("page")
    if raw_page is not None:
        page = _parse_positive_int(raw_page, "page", 1, None)

    page_size = DEFAULT_PAGE_SIZE
    raw_size = first("page_size")
    if raw_size is not None:
        page_size = _parse_positive_int(raw_size, "page_size", 1, MAX_PAGE_SIZE)

    return q, status, page, page_size


def _parse_positive_int(raw, name, minimum, maximum):
    bounds = f"{minimum}–{maximum}" if maximum else f"≥{minimum}"
    try:
        value = int(raw)
    except (ValueError, TypeError):
        raise ApiError(
            400, "VALIDATION_ERROR", f"{name} 必须是整数（{bounds}）",
            fields={name: f"必须是整数，范围 {bounds}"},
        )
    if value < minimum or (maximum is not None and value > maximum):
        raise ApiError(
            400, "VALIDATION_ERROR", f"{name} 超出合法范围（{bounds}）",
            fields={name: f"必须是整数，范围 {bounds}"},
        )
    return value


def like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"
    db_path = None      # 由 main() 注入
    static_dir = None   # 由 main() 注入

    # ---- 入口 ----

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("HEAD")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def do_OPTIONS(self):
        self._route("OPTIONS")

    def _route(self, method):
        self._suppress_body = method == "HEAD"
        split = urllib.parse.urlsplit(self.path)
        try:
            body = self._read_body(method)
            self._dispatch(method, split.path, split.query, body)
        except ApiError as exc:
            self._send_json(
                exc.status,
                error_payload(exc.code, exc.message, exc.fields),
                extra_headers=exc.headers,
            )
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self._send_json(
                    500, error_payload("INTERNAL_ERROR", "服务器内部错误，请稍后重试")
                )
            except Exception:
                pass

    def _read_body(self, method):
        raw_len = self.headers.get("Content-Length")
        if raw_len is not None:
            try:
                length = int(raw_len)
            except ValueError:
                raise ApiError(400, "INVALID_JSON", "Content-Length 非法")
            if length < 0 or length > 10 * 1024 * 1024:
                raise ApiError(400, "INVALID_JSON", "请求体长度非法")
            data = b""
            while len(data) < length:
                chunk = self.rfile.read(length - len(data))
                if not chunk:
                    break
                data += chunk
            return data
        if method in ("POST", "PUT", "PATCH") and self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "INVALID_JSON", "不支持的请求体传输编码，请使用 Content-Length")
        return b""

    # ---- 路由 ----

    def _dispatch(self, method, path, query, body):
        if path == "/api/items":
            if method in ("GET", "HEAD"):
                return self._list_items(query)
            if method == "POST":
                return self._create_item(body)
            raise ApiError(
                405, "METHOD_NOT_ALLOWED",
                f"方法 {method} 不被允许，/api/items 仅支持 GET / POST",
                headers={"Allow": "GET, HEAD, POST"},
            )

        match = re.fullmatch(r"/api/items/([^/]+)", path)
        if match:
            if method not in ("GET", "HEAD", "PUT", "DELETE"):
                raise ApiError(
                    405, "METHOD_NOT_ALLOWED",
                    f"方法 {method} 不被允许，/api/items/{{id}} 仅支持 GET / PUT / DELETE",
                    headers={"Allow": "GET, HEAD, PUT, DELETE"},
                )
            item_id = self._parse_item_id(match.group(1))
            if method in ("GET", "HEAD"):
                return self._get_item(item_id)
            if method == "PUT":
                return self._update_item(item_id, body)
            return self._delete_item(item_id)

        if path == "/" or path.startswith("/static/"):
            if method not in ("GET", "HEAD"):
                raise ApiError(
                    405, "METHOD_NOT_ALLOWED",
                    f"方法 {method} 不被允许，静态资源仅支持 GET",
                    headers={"Allow": "GET, HEAD"},
                )
            return self._serve_static(path)

        raise ApiError(404, "NOT_FOUND", f"路径不存在: {path}")

    def _parse_item_id(self, raw):
        if not re.fullmatch(r"[0-9]+", raw):
            raise ApiError(404, "NOT_FOUND", f"事项不存在: {raw}")
        item_id = int(raw)
        if item_id < 1:
            raise ApiError(404, "NOT_FOUND", f"事项不存在: {raw}")
        return item_id

    # ---- 响应原语 ----

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not self._suppress_body:
            self.wfile.write(body)

    def _send_204(self):
        self.send_response(204)
        self.end_headers()

    def _send_file(self, full_path, content_type):
        with open(full_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not self._suppress_body:
            self.wfile.write(body)

    # ---- API ----

    def _list_items(self, query):
        q, status, page, page_size = parse_list_params(query)
        where = []
        args = []
        if q is not None:
            where.append("title LIKE ? ESCAPE '\\'")
            args.append(f"%{like_escape(q)}%")
        if status is not None:
            where.append("status = ?")
            args.append(status)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        conn = connect(self.db_path)
        try:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM items{where_sql}", args
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM items{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                args + [page_size, (page - 1) * page_size],
            ).fetchall()
        finally:
            conn.close()
        total_pages = max(1, (total + page_size - 1) // page_size)
        self._send_json(200, {
            "items": [row_to_item(r) for r in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })

    def _create_item(self, body):
        data = parse_json_object(body)
        values = validate_payload(data, partial=False)
        now = utcnow()
        conn = connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO items (title, status, priority, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    values["title"],
                    values.get("status", "todo"),
                    values.get("priority", "medium"),
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        finally:
            conn.close()
        self._send_json(201, row_to_item(row))

    def _get_item(self, item_id):
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ApiError(404, "NOT_FOUND", f"事项不存在: id={item_id}")
        self._send_json(200, row_to_item(row))

    def _update_item(self, item_id, body):
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:  # 存在性优先于字段校验
                raise ApiError(404, "NOT_FOUND", f"事项不存在: id={item_id}")
            data = parse_json_object(body)
            values = validate_payload(data, partial=True)
            now = utcnow()
            conn.execute(
                "UPDATE items SET title = ?, status = ?, priority = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    values.get("title", row["title"]),
                    values.get("status", row["status"]),
                    values.get("priority", row["priority"]),
                    now,
                    item_id,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        finally:
            conn.close()
        self._send_json(200, row_to_item(row))

    def _delete_item(self, item_id):
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT id FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise ApiError(404, "NOT_FOUND", f"事项不存在: id={item_id}")
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.commit()
        finally:
            conn.close()
        self._send_204()

    # ---- 静态文件 ----

    def _serve_static(self, path):
        rel = "index.html" if path == "/" else path[len("/static/"):]
        rel = urllib.parse.unquote(rel)
        base = os.path.realpath(self.static_dir)
        candidate = os.path.realpath(os.path.join(base, rel))
        if candidate != base and not candidate.startswith(base + os.sep):
            raise ApiError(404, "NOT_FOUND", f"文件不存在: {path}")  # 路径穿越防护
        if not os.path.isfile(candidate):
            raise ApiError(404, "NOT_FOUND", f"文件不存在: {path}")
        ext = os.path.splitext(candidate)[1].lower()
        self._send_file(candidate, CONTENT_TYPES.get(ext, "application/octet-stream"))


# ---------------------------------------------------------------- 入口

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="事项管理应用（Python 标准库 HTTP + SQLite，零外部运行依赖）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument(
        "--db",
        default=os.path.join("data", "issue_board.sqlite3"),
        help="SQLite 数据库文件路径（默认 data/issue_board.sqlite3，自动创建父目录）",
    )
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    init_db(db_path)
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    Handler.db_path = db_path
    Handler.static_dir = static_dir

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"无法监听 {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    server.daemon_threads = True
    print(
        f"Issue Board 已启动: http://{args.host}:{args.port}/  (db: {db_path})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务…", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
