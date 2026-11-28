"""HTTP 路由、JSON 编解码与统一错误响应（分析报告 §5 / §6）。"""

import json
import os
import posixpath
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import service

STATIC_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

ITEMS_PREFIX = "/api/items/"


def normalize_path(raw_path):
    """URL 解码 + 规范化，供路由与静态穿越防护使用。"""
    path = posixpath.normpath(urllib.parse.unquote(raw_path))
    while path.startswith("//"):
        path = path[1:]
    if not path.startswith("/"):
        path = "/" + path
    return path


def classify_path(path):
    """返回 (kind, raw_id)，kind ∈ meta / collection / item / unknown_api。"""
    if path == "/api/meta":
        return "meta", None
    if path in ("/api/items", "/api/items/"):
        return "collection", None
    if path.startswith(ITEMS_PREFIX):
        rest = path[len(ITEMS_PREFIX):]
        if rest and "/" not in rest:
            return "item", rest
    return "unknown_api", None


def resolve_static(path):
    """把静态请求解析为 STATIC_ROOT 内的真实文件，越界或不存在返回 None。"""
    relative = path.lstrip("/")
    if relative == "":
        relative = "index.html"
    candidate = os.path.realpath(os.path.join(STATIC_ROOT, relative))
    if candidate != STATIC_ROOT and not candidate.startswith(STATIC_ROOT + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def parse_query(query):
    """保留空值参数（``?page=`` 与缺省 ``page`` 语义不同），同名参数取第一个。"""
    parsed = urllib.parse.parse_qs(query, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def make_handler(database):
    """为指定 Database 生成请求处理器类（每请求新建并关闭一个 SQLite 连接）。"""

    class ApiRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "IssueBoard/1.0"
        sys_version = ""

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_HEAD(self):
            self._dispatch("HEAD")

        def do_OPTIONS(self):
            self._dispatch("OPTIONS")

        def _dispatch(self, method):
            try:
                parsed = urllib.parse.urlsplit(self.path)
                path = normalize_path(parsed.path)
                if path == "/api" or path.startswith("/api/"):
                    self._handle_api(method, path, parsed.query)
                else:
                    self._handle_static(method, path)
            except service.ApiError as error:
                self._send_error(error)
            except (BrokenPipeError, ConnectionResetError) as error:
                self.log_message("client went away: %s", error)
            except Exception:
                traceback.print_exc()
                self._send_error(service.internal_error())

        def _handle_api(self, method, path, query):
            kind, raw_id = classify_path(path)
            if kind == "unknown_api":
                self._drain_body()
                raise service.not_found()
            if kind == "meta":
                self._require_method(method, ("GET",))
                self._send_json(200, service.meta_payload())
                return
            if kind == "collection":
                self._require_method(method, ("GET", "POST"))
                if method == "GET":
                    with database.session() as connection:
                        payload = service.list_items(connection, parse_query(query))
                    self._send_json(200, payload)
                    return
                payload = service.parse_json_body(self._read_body())
                with database.session() as connection:
                    item = service.create_item(connection, payload)
                location = "/api/items/{0}".format(item["id"])
                self._send_json(201, item, {"Location": location})
                return
            self._handle_item(method, raw_id)

        def _handle_item(self, method, raw_id):
            allowed = ("GET", "PUT", "DELETE")
            if method not in allowed:
                self._drain_body()
                raise service.method_not_allowed(allowed)
            # §3.1：请求体大小检查优先于 id 格式检查
            body = self._read_body()
            item_id = service.parse_item_id(raw_id)
            if method == "PUT":
                payload = service.parse_json_body(body)
                with database.session() as connection:
                    item = service.update_item(connection, item_id, payload)
                self._send_json(200, item)
                return
            with database.session() as connection:
                if method == "GET":
                    item = service.get_item(connection, item_id)
                else:
                    item = service.delete_item(connection, item_id)
            self._send_json(200, item)

        def _handle_static(self, method, path):
            if method != "GET":
                self._drain_body()
                raise service.method_not_allowed(("GET",))
            target = resolve_static(path)
            if target is None:
                self._send_text(404, "404 Not Found")
                return
            with open(target, "rb") as handle:
                data = handle.read()
            extension = os.path.splitext(target)[1].lower()
            content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _require_method(self, method, allowed):
            if method in allowed:
                return
            self._drain_body()
            raise service.method_not_allowed(allowed)

        def _content_length(self):
            raw = self.headers.get("Content-Length")
            if raw is None:
                return 0
            try:
                return max(0, int(raw))
            except ValueError:
                return 0

        def _read_exact(self, length):
            chunks = []
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        def _drain_body(self):
            length = self._content_length()
            if length:
                self._read_exact(length)

        def _read_body(self):
            length = self._content_length()
            if length > service.MAX_BODY_BYTES:
                self._read_exact(length)
                raise service.payload_too_large()
            return self._read_exact(length)

        def _send_json(self, status, payload, extra_headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status, text):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, error):
            headers = {}
            allow = error.details.get("allow")
            if allow:
                headers["Allow"] = ", ".join(allow)
            self._send_json(error.status, error.to_body(), headers)

    return ApiRequestHandler
