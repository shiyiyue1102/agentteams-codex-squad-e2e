#!/usr/bin/env python3
"""事项管理应用（Issue Board）HTTP 服务入口 — 仅 Python 标准库。

路由（契约见 README 与分析报告）：
    GET    /                        → static/index.html
    GET    /style.css /app.js /static/* → 静态资源
    GET    /api/items               → 列表（keyword/status/page/page_size）
    POST   /api/items               → 新增
    GET    /api/items/{id}          → 单条
    PUT    /api/items/{id}          → 编辑（部分更新语义，空对象合法）
    DELETE /api/items/{id}          → 删除，200 {"success": true, "id": N}

所有 4xx/5xx 统一返回 {"error": {"code": "...", "message": "..."}}。

配置（CLI 参数优先于环境变量）：
    --host / HOST             监听地址，默认 0.0.0.0（供局域网/预览访问）
    --port / PORT             监听端口，默认 8000
    --db   / ISSUE_BOARD_DB   SQLite 文件，默认 <case 目录>/data/items.db

启动：python3 server.py    然后浏览器打开 http://127.0.0.1:8000/
"""

import argparse
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

CASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(CASE_DIR, "static")

MAX_BODY_BYTES = 1_000_000
TITLE_MAX_LEN = 80
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100

POSITIVE_INT_RE = re.compile(r"^[0-9]+$")
ITEM_PATH_RE = re.compile(r"^/api/items/([^/]+)$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

MESSAGES = {
    "TITLE_REQUIRED": "标题去除空白后不能为空",
    "TITLE_TOO_LONG": "标题不能超过80个字符",
    "INVALID_STATUS": "状态必须是 todo/in_progress/done 之一",
    "INVALID_PRIORITY": "优先级必须是 low/medium/high 之一",
    "INVALID_INPUT": "请求体必须是 JSON 对象",
    "INVALID_ID": "id 必须是正整数",
    "INVALID_PAGE": "page 必须是正整数，page_size 必须是 1-100 的整数",
    "METHOD_NOT_ALLOWED": "该路径不支持此 HTTP 方法",
}
POST_INVALID_INPUT_MESSAGE = "请求体必须是包含字符串 title 的 JSON 对象"


class ApiError(Exception):
    """业务错误：HTTP 状态 + 契约错误码 code + 人类可读 message。"""

    def __init__(self, http_status, code, message=None):
        super().__init__(code)
        self.http_status = http_status
        self.code = code
        self.message = message if message is not None else MESSAGES.get(code, code)


def _parse_item_id(raw):
    if not POSITIVE_INT_RE.match(raw or ""):
        raise ApiError(400, "INVALID_ID")
    value = int(raw)
    if value < 1:
        raise ApiError(400, "INVALID_ID")
    return value


def _validate_title(value, type_error_message):
    """title 必须是字符串；strip 后非空且 ≤ 80 字符（Unicode 字符数）。"""
    if not isinstance(value, str):
        raise ApiError(400, "INVALID_INPUT", type_error_message)
    title = value.strip()
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        raise ApiError(400, "INVALID_INPUT", type_error_message)
    if not title:
        raise ApiError(400, "TITLE_REQUIRED")
    if len(title) > TITLE_MAX_LEN:
        raise ApiError(400, "TITLE_TOO_LONG")
    return title


def _validate_status(value):
    if not isinstance(value, str) or value not in db.STATUS_VALUES:
        raise ApiError(400, "INVALID_STATUS")
    return value


def _validate_priority(value):
    if not isinstance(value, str) or value not in db.PRIORITY_VALUES:
        raise ApiError(400, "INVALID_PRIORITY")
    return value


def _parse_paging_int(raw):
    if not POSITIVE_INT_RE.match(raw or ""):
        raise ApiError(400, "INVALID_PAGE")
    value = int(raw)
    if value < 1:
        raise ApiError(400, "INVALID_PAGE")
    return value


class RequestHandler(BaseHTTPRequestHandler):
    """API 路由 + 静态文件服务。每个响应都带 Content-Length。"""

    server_version = "IssueBoard/1.0"
    # 保持默认 HTTP/1.0（每请求短连接），对流式响应最稳妥。

    # ---------- 基础响应 ----------

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_json_object(self, invalid_message):
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        try:
            length = int(length_raw)
        except ValueError:
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_INPUT", invalid_message)
        return payload

    # ---------- 请求分发 ----------

    def do_GET(self):
        self._handle_request()

    def do_HEAD(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def do_PATCH(self):
        self._handle_request()

    def do_DELETE(self):
        self._handle_request()

    def do_OPTIONS(self):
        self._handle_request()

    def _handle_request(self):
        try:
            self._route()
        except ApiError as exc:
            try:
                self._send_json(
                    exc.http_status,
                    {"error": {"code": exc.code, "message": exc.message}},
                )
            except OSError:
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.log_error("Unhandled exception:\n%s", traceback.format_exc())
            try:
                self._send_json(
                    500,
                    {"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}},
                )
            except OSError:
                self.close_connection = True

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if len(path) > 1:
            path = path.rstrip("/") or "/"

        if path == "/api/items":
            if self.command in ("GET", "HEAD"):
                self._handle_list(parsed.query)
            elif self.command == "POST":
                self._handle_create()
            else:
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            return

        match = ITEM_PATH_RE.match(path)
        if match:
            item_id = _parse_item_id(match.group(1))
            if self.command in ("GET", "HEAD"):
                self._handle_get_item(item_id)
            elif self.command == "PUT":
                self._handle_update(item_id)
            elif self.command == "DELETE":
                self._handle_delete(item_id)
            else:
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            return

        if path.startswith("/api"):
            raise ApiError(404, "NOT_FOUND", "未知的 API 路径: %s" % path)

        # 静态资源：先解析路径（决定 404），再检查方法（决定 405）
        target = _resolve_static(path)
        if target is None:
            raise ApiError(404, "NOT_FOUND", "资源不存在: %s" % path)
        if self.command not in ("GET", "HEAD"):
            raise ApiError(405, "METHOD_NOT_ALLOWED")
        self._serve_file(target)

    # ---------- API 处理 ----------

    def _handle_list(self, query):
        params = parse_qs(query, keep_blank_values=True)

        def first(name, default=""):
            values = params.get(name)
            return values[0] if values else default

        keyword = first("keyword")
        status = first("status")
        if status and status not in db.STATUS_VALUES:
            raise ApiError(400, "INVALID_STATUS")
        page = _parse_paging_int(first("page", "1"))
        page_size = _parse_paging_int(first("page_size", str(DEFAULT_PAGE_SIZE)))
        if page_size > MAX_PAGE_SIZE:
            raise ApiError(400, "INVALID_PAGE")
        result = db.list_items(
            self.server.db_path,
            keyword=keyword or None,
            status=status or None,
            page=page,
            page_size=page_size,
        )
        self._send_json(200, result)

    def _handle_create(self):
        payload = self._read_json_object(POST_INVALID_INPUT_MESSAGE)
        if "title" not in payload:
            raise ApiError(400, "INVALID_INPUT", POST_INVALID_INPUT_MESSAGE)
        title = _validate_title(payload["title"], POST_INVALID_INPUT_MESSAGE)
        status = _validate_status(payload.get("status", "todo"))
        priority = _validate_priority(payload.get("priority", "medium"))
        item = db.create_item(self.server.db_path, title, status, priority)
        self._send_json(201, item)

    def _handle_get_item(self, item_id):
        item = db.get_item(self.server.db_path, item_id)
        if item is None:
            raise ApiError(404, "ITEM_NOT_FOUND", "事项不存在: %d" % item_id)
        self._send_json(200, item)

    def _handle_update(self, item_id):
        # 先确认记录存在（404 优先），再解析/校验请求体（与 POST 校验规则一致）
        existing = db.get_item(self.server.db_path, item_id)
        if existing is None:
            raise ApiError(404, "ITEM_NOT_FOUND", "事项不存在: %d" % item_id)
        payload = self._read_json_object("请求体必须是 JSON 对象")
        fields = {}
        if "title" in payload:
            fields["title"] = _validate_title(payload["title"], "title 必须是字符串")
        if "status" in payload:
            fields["status"] = _validate_status(payload["status"])
        if "priority" in payload:
            fields["priority"] = _validate_priority(payload["priority"])
        item = db.update_item(self.server.db_path, item_id, fields)
        if item is None:  # 理论上不可达：存在性已确认
            raise ApiError(404, "ITEM_NOT_FOUND", "事项不存在: %d" % item_id)
        self._send_json(200, item)

    def _handle_delete(self, item_id):
        deleted = db.delete_item(self.server.db_path, item_id)
        if not deleted:
            raise ApiError(404, "ITEM_NOT_FOUND", "事项不存在: %d" % item_id)
        self._send_json(200, {"success": True, "id": item_id})

    # ---------- 静态文件 ----------

    def _serve_file(self, full_path):
        ext = os.path.splitext(full_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full_path, "rb") as fh:
            body = fh.read()
        self._send_bytes(200, body, content_type)


def _resolve_static(path):
    """把 URL 路径解析为 STATIC_DIR 内的真实文件路径；非法/不存在返回 None。"""
    if path == "/":
        rel = "index.html"
    elif path.startswith("/static/"):
        rel = path[len("/static/"):]
    elif path.startswith("/"):
        rel = path[1:]
    else:
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    full = os.path.join(STATIC_DIR, *parts)
    if not full.startswith(STATIC_DIR + os.sep):
        return None
    if not os.path.isfile(full):
        return None
    return full


class IssueBoardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db_path):
        self.db_path = db_path
        super().__init__(address, RequestHandler)


def resolve_config(argv=None):
    default_db = os.path.join(CASE_DIR, "data", "items.db")
    parser = argparse.ArgumentParser(
        description="事项管理应用（Issue Board）服务器 — 仅 Python 标准库"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="监听地址（环境变量 HOST，默认 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="监听端口（环境变量 PORT，默认 8000）",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("ISSUE_BOARD_DB", default_db),
        help="SQLite 数据库文件路径（环境变量 ISSUE_BOARD_DB，默认 ./data/items.db）",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = resolve_config(argv)
    db_path = os.path.abspath(args.db)
    db.init_db(db_path)
    server = IssueBoardServer((args.host, args.port), db_path)
    bound_host, bound_port = server.server_address[0], server.server_address[1]
    print(
        "事项管理应用已启动: http://%s:%d/  (数据库: %s)"
        % (bound_host, bound_port, db_path),
        flush=True,
    )
    if bound_host in ("0.0.0.0", "::"):
        print("本机访问: http://127.0.0.1:%d/" % bound_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务…", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
