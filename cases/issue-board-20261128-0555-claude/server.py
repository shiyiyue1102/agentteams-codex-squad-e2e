"""事项管理应用 HTTP 服务入口（仅 Python 标准库）。

契约来源：Issue #106 分析报告第 1.3、2、4 节。

启动：
    python3 server.py [--host HOST] [--port PORT] [--db DB_PATH]

默认绑定 0.0.0.0:8000，数据库默认为脚本同目录 items.db
（相对路径按脚本所在目录解析，与 cwd 无关）。
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qsl

import store

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")

KNOWN_FIELDS = ("title", "status", "priority")
ID_PATTERN = re.compile(r"^\d+$")

ERROR_MESSAGES = {
    "invalid_json": "请求体必须为合法 JSON 对象",
    "invalid_title": "标题去除两端空白后必填，且不超过 80 字",
    "invalid_status": "status 必须为 todo/in_progress/done 之一",
    "invalid_priority": "priority 必须为 high/medium/low 之一",
    "no_fields": "至少提供一个待更新字段（title/status/priority）",
    "not_found": "请求的资源不存在",
    "method_not_allowed": "该方法不被允许",
    "internal_error": "服务器内部错误",
}

ALLOWED_METHODS = {
    "/api/health": ("GET", "HEAD"),
    "/api/items": ("GET", "POST", "HEAD"),
    "/api/items/{id}": ("GET", "PUT", "DELETE", "HEAD"),
}


class ApiError(Exception):
    def __init__(self, http_status, code, message=None, allow=None):
        super().__init__(message or ERROR_MESSAGES.get(code, code))
        self.http_status = http_status
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, code)
        self.allow = allow


def _validate_title(value, required):
    """返回 trim 后的标题；非法时抛 ApiError(invalid_title)。"""
    if value is None:
        if required:
            raise ApiError(400, "invalid_title")
        return None
    if not isinstance(value, str):
        raise ApiError(400, "invalid_title")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > 80:
        raise ApiError(400, "invalid_title")
    return trimmed


def _validate_enum(value, allowed, code):
    """严格校验：显式 null 或非法值一律报错（省略由调用方处理）。"""
    if value is None or not isinstance(value, str) or value not in allowed:
        raise ApiError(400, code)
    return value


def _parse_object_body(raw):
    """解析请求体为 JSON 对象；非法 JSON 或非对象 → invalid_json。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json")
    return data


def _parse_int_param(params, name, lo, hi, default):
    if name not in params:
        return default
    raw = params[name]
    range_text = ">={} 的整数".format(lo) if hi is None else "{}..{} 的整数".format(lo, hi)
    if not raw.isdigit():
        raise ApiError(400, "invalid_param", "参数 {} 必须为 {}".format(name, range_text))
    value = int(raw)
    if value < lo or (hi is not None and value > hi):
        raise ApiError(400, "invalid_param", "参数 {} 必须为 {}".format(name, range_text))
    return value


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础设施 ----------

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] %s - %s\n" % (self.address_string(), fmt % args))

    @property
    def item_store(self):
        return self.server.item_store

    def _send_json(self, status, payload, allow=None, body_override=None):
        if body_override is not None:
            body = body_override
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_no_body(self, status, allow=None):
        self.send_response(status)
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_error_json(self, status, code, message=None, allow=None):
        payload = {"error": {"code": code, "message": message or ERROR_MESSAGES.get(code, code)}}
        self._send_json(status, payload, allow=allow)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            n = int(length)
        except ValueError:
            return b""
        if n <= 0:
            return b""
        return self.rfile.read(n)

    # ---------- 路由 ----------

    def _route(self):
        split = urlsplit(self.path)
        path = unquote(split.path)
        # 归一化重复斜杠不需要；契约路径均为单斜杠
        if path.startswith("/api/"):
            self._route_api(path, split)
        elif path == "/api":
            self._send_error_json(404, "not_found", "接口不存在")
        else:
            self._route_static(path)

    def _route_api(self, path, split):
        if path == "/api/health":
            self._check_method(path, "/api/health")
            if self.command == "HEAD":
                self._send_no_body(200, allow="GET, HEAD")
                return
            self._send_json(200, {"status": "ok"})
            return
        if path == "/api/items":
            self._check_method(path, "/api/items")
            if self.command == "GET":
                self._handle_list(split)
            elif self.command == "HEAD":
                self._send_no_body(200, allow="GET, POST, HEAD")
            else:
                self._handle_create()
            return
        m = re.match(r"^/api/items/([^/]+)$", path)
        if m:
            self._check_method(path, "/api/items/{id}")
            raw_id = m.group(1)
            if not ID_PATTERN.match(raw_id):
                raise ApiError(
                    400,
                    "invalid_param",
                    "路径参数 id 必须为正整数",
                )
            item_id = int(raw_id)
            if self.command == "GET":
                self._handle_get_one(item_id)
            elif self.command == "HEAD":
                self._send_no_body(200, allow="GET, PUT, DELETE, HEAD")
            elif self.command == "PUT":
                self._handle_update(item_id)
            else:
                self._handle_delete(item_id)
            return
        self._send_error_json(404, "not_found", "接口不存在")

    def _check_method(self, path, route_key):
        allowed = ALLOWED_METHODS[route_key]
        if self.command not in allowed:
            allow_str = ", ".join(m for m in allowed if m != "HEAD")
            raise ApiError(
                405,
                "method_not_allowed",
                "路径 {} 仅支持方法：{}".format(path, allow_str),
                allow=allow_str,
            )

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _dispatch(self):
        try:
            self._route()
        except ApiError as e:
            try:
                self._send_error_json(e.http_status, e.code, e.message, allow=e.allow)
            except OSError:
                pass
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()  # 仅记录到服务端日志，不进入响应
            try:
                self._send_error_json(500, "internal_error")
            except OSError:
                pass

    # ---------- API 处理器 ----------

    def _handle_list(self, split):
        params = {}
        for k, v in parse_qsl(split.query, keep_blank_values=True):
            if k not in params:
                params[k] = v
        page = _parse_int_param(params, "page", 1, None, 1)
        page_size = _parse_int_param(params, "page_size", 1, 100, 10)
        q = params.get("q") or None
        status = params.get("status") or None
        if status is not None and status not in store.STATUS_VALUES:
            raise ApiError(
                400,
                "invalid_param",
                "参数 status 必须为 todo/in_progress/done 之一",
            )
        items, total = self.item_store.list_items(
            q=q, status=status, page=page, page_size=page_size
        )
        total_pages = (total + page_size - 1) // page_size if total else 0
        self._send_json(
            200,
            {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        )

    def _handle_create(self):
        data = _parse_object_body(self._read_body())
        # 校验顺序：title → status → priority；显式 null 视为非法值
        title = _validate_title(data.get("title"), required=True)
        status = (
            _validate_enum(data["status"], store.STATUS_VALUES, "invalid_status")
            if "status" in data
            else "todo"
        )
        priority = (
            _validate_enum(data["priority"], store.PRIORITY_VALUES, "invalid_priority")
            if "priority" in data
            else "medium"
        )
        item = self.item_store.create_item(title, status, priority)
        self._send_json(201, item)

    def _handle_get_one(self, item_id):
        item = self.item_store.get_item(item_id)
        if item is None:
            raise ApiError(404, "not_found", "事项不存在")
        self._send_json(200, item)

    def _handle_update(self, item_id):
        data = _parse_object_body(self._read_body())
        fields = {}
        # 校验顺序：title → status → priority；校验先于存在性检查；显式 null 报 400
        if "title" in data:
            fields["title"] = _validate_title(data["title"], required=True)
        if "status" in data:
            fields["status"] = _validate_enum(
                data["status"], store.STATUS_VALUES, "invalid_status"
            )
        if "priority" in data:
            fields["priority"] = _validate_enum(
                data["priority"], store.PRIORITY_VALUES, "invalid_priority"
            )
        if not fields:
            raise ApiError(400, "no_fields")
        item = self.item_store.update_item(item_id, fields)
        if item is None:
            raise ApiError(404, "not_found", "事项不存在")
        self._send_json(200, item)

    def _handle_delete(self, item_id):
        if not self.item_store.delete_item(item_id):
            raise ApiError(404, "not_found", "事项不存在")
        self._send_no_body(204)

    # ---------- 静态文件 ----------

    def _route_static(self, path):
        if self.command not in ("GET", "HEAD"):
            self._send_error_json(
                405,
                "method_not_allowed",
                "静态资源仅支持方法：GET",
                allow="GET",
            )
            return
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        candidate = os.path.realpath(os.path.join(STATIC_DIR, rel))
        static_root = os.path.realpath(STATIC_DIR)
        if candidate != static_root and not candidate.startswith(static_root + os.sep):
            self._send_text_404()
            return
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "index.html")
        if not os.path.isfile(candidate):
            self._send_text_404()
            return
        ctype = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in (
            "application/javascript",
            "application/json",
        ):
            ctype += "; charset=utf-8"
        with open(candidate, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text_404(self):
        body = b"404 Not Found\n"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="事项管理应用服务（Issue #106）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument(
        "--db",
        default="items.db",
        help="SQLite 数据库文件路径；相对路径按脚本所在目录解析，默认脚本同目录 items.db",
    )
    args = parser.parse_args(argv)

    db_path = store.resolve_db_path(args.db)
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.isdir(db_dir):
        sys.stderr.write(
            "错误：数据库父目录不存在：{}\n（请先创建目录，或换一个 --db 路径）\n".format(db_dir)
        )
        return 2

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    server.daemon_threads = True
    try:
        server.item_store = store.ItemStore(db_path)
    except OSError as e:
        sys.stderr.write("错误：无法初始化数据库 {}：{}\n".format(db_path, e))
        return 2

    sys.stderr.write(
        "事项管理服务已启动：http://{}:{}  数据库：{}\n".format(
            args.host, args.port, db_path
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
