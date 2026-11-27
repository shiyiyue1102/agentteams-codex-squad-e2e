"""HTTP 层：路由分发、静态文件白名单、请求体读取、统一响应与错误封装（契约 2.3、4.8、6.1）。"""

import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit

from . import api
from .errors import ApiError, INTERNAL_ERROR, INVALID_BODY, NOT_FOUND, PAYLOAD_TOO_LARGE

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
MAX_BODY_BYTES = 1024 * 1024
MAX_DRAIN_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 65536

WEB_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

# D14：静态资源白名单，只暴露 web/ 下这三个文件；其它路径一律 404，不做目录列举。
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


class ApiRequestHandler(BaseHTTPRequestHandler):
    """单请求处理器；HTTP/1.1 keep-alive，每个响应都写 Content-Length（R1）。"""

    protocol_version = "HTTP/1.1"
    server_version = "IssueBoard/1.0"
    sys_version = ""

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_OPTIONS(self):
        self._handle("OPTIONS")

    def log_message(self, fmt, *args):
        """覆写：统一输出到 stderr，含方法、路径、状态码与耗时。"""
        sys.stderr.write("[issue-board] " + (fmt % args) + "\n")
        sys.stderr.flush()

    def log_request(self, code="-", size="-"):
        """禁掉默认的访问日志，改由 _handle 统一记录。"""

    def _handle(self, method):
        started = time.monotonic()
        send_body = method != "HEAD"
        status = 500
        try:
            try:
                status, content_type, body, headers = self._resolve(method)
            except ApiError as error:
                status, content_type, body, headers = self._error_parts(error)
                if error.code == PAYLOAD_TOO_LARGE:
                    headers["Connection"] = "close"
                    self.close_connection = True
            except Exception:
                traceback.print_exc()
                status, content_type, body, headers = self._error_parts(ApiError(INTERNAL_ERROR))
            self._write(status, content_type, body, headers, send_body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            self.log_message("%s %s -> %s (%.1f ms)", method, self.path, status, elapsed_ms)

    def _resolve(self, method):
        split = urlsplit(self.path)
        path = unquote(split.path)
        raw_body = self._read_body()
        if api.is_api_path(path):
            query_pairs = parse_qsl(split.query, keep_blank_values=True)
            response = api.dispatch(method, path, query_pairs, raw_body, self.server.db_path)
            body = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
            return response.status, JSON_CONTENT_TYPE, body, response.headers
        return self._static_response(path)

    def _static_response(self, path):
        entry = STATIC_FILES.get(path)
        if entry is None:
            raise ApiError(NOT_FOUND)
        filename, content_type = entry
        resolved = os.path.realpath(os.path.join(WEB_DIR, filename))
        if not _is_inside_web_dir(resolved):
            raise ApiError(NOT_FOUND)
        try:
            with open(resolved, "rb") as handle:
                data = handle.read()
        except OSError:
            raise ApiError(NOT_FOUND) from None
        return 200, content_type, data, {}

    def _read_body(self):
        if self.headers.get("Transfer-Encoding"):
            # 不支持分块请求体：明确失败并关闭连接，避免 keep-alive 流错位。
            self.close_connection = True
            raise ApiError(INVALID_BODY, headers={"Connection": "close"})
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(str(raw_length).strip())
        except ValueError:
            raise ApiError(INVALID_BODY) from None
        if length < 0:
            raise ApiError(INVALID_BODY)
        if length > MAX_BODY_BYTES:
            self._drain(length)
            self.close_connection = True
            raise ApiError(PAYLOAD_TOO_LARGE, headers={"Connection": "close"})
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _drain(self, length):
        """超限请求：丢弃有限字节后关闭连接，保证 413 能可靠送达客户端（D13）。"""
        remaining = min(length, MAX_DRAIN_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _error_parts(self, error):
        payload = error.to_payload()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return error.status, JSON_CONTENT_TYPE, body, dict(error.headers)

    def _write(self, status, content_type, body, extra_headers, send_body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key in sorted(extra_headers):
            self.send_header(key, extra_headers[key])
        self.end_headers()
        if send_body and body:
            self.wfile.write(body)


def _is_inside_web_dir(resolved_path):
    try:
        return os.path.commonpath([resolved_path, WEB_DIR]) == WEB_DIR
    except ValueError:
        return False


class IssueBoardServer(ThreadingHTTPServer):
    """多线程 HTTP 服务；每请求独立 SQLite 连接，因此只需持有数据库路径。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db_path, handler_class=ApiRequestHandler):
        self.db_path = os.path.abspath(db_path)
        ThreadingHTTPServer.__init__(self, address, handler_class)
