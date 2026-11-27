#!/usr/bin/env python3
"""事项管理应用入口：Python 标准库 HTTP 服务 + SQLite，零外部运行依赖。

用法：
    python3 server.py [--host 127.0.0.1] [--port 8000] [--db data/items.db]

参数：
    --host HOST  监听地址，默认 127.0.0.1；需要网络可达的预览地址时用 0.0.0.0
    --port PORT  监听端口，默认 8000；0 表示随机可用端口（实际端口打印到 stdout）
    --db PATH    SQLite 数据库文件路径，默认 <本目录>/data/items.db；
                 指向临时路径（如 /tmp/items-test.db）即可做一次性测试，
                 删除该文件即清空全部数据

停止服务：Ctrl+C。
"""

import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import api
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "items.db")

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
}


def resolve_static_path(url_path):
    """把已解码的 URL 路径映射到 static 目录内的真实文件；越界或不存在返回 None。"""
    if url_path in ("", "/"):
        url_path = "/index.html"
    if url_path.startswith("/static/"):
        url_path = url_path[len("/static"):]
    relative = url_path.lstrip("/")
    candidate = os.path.normpath(os.path.join(STATIC_DIR, relative))
    if not candidate.startswith(STATIC_DIR + os.sep):
        return None  # 防目录穿越
    if not os.path.isfile(candidate):
        return None
    return candidate


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "ItemsBoard/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive；所有响应都带 Content-Length

    # ---------- 基础响应工具 ----------

    def _send_json(self, status, payload, head_only=False):
        body = (
            b""
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and not head_only:
            self.wfile.write(body)

    def _safe_send_json(self, status, payload, head_only=False):
        try:
            self._send_json(status, payload, head_only)
        except OSError:
            pass  # 客户端已断开，忽略

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    # ---------- 路由 ----------

    def _route(self, method):
        head_only = method == "HEAD"
        effective = "GET" if head_only else method
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/"):
                self._handle_api(effective, path, parsed, head_only)
            else:
                self._handle_static(effective, path, head_only)
        except api.ApiError as err:
            self._safe_send_json(err.status, err.to_dict(), head_only)
        except Exception:
            traceback.print_exc()
            self._safe_send_json(
                500,
                {"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}},
                head_only,
            )

    def _handle_api(self, method, path, parsed, head_only):
        params = {
            key: values[-1]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        body = self._read_body()
        conn = db.connect(self.server.db_path)
        try:
            status, payload = api.route_api(conn, method, path, params, body)
        finally:
            conn.close()
        self._send_json(status, payload, head_only)

    def _handle_static(self, method, path, head_only):
        if method != "GET":
            raise api.ApiError(405, "METHOD_NOT_ALLOWED", "静态资源仅支持 GET / HEAD")
        file_path = resolve_static_path(path)
        if file_path is None:
            raise api.ApiError(404, "NOT_FOUND", "页面或资源不存在：%s" % path)
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "rb") as fh:
            content = fh.read()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            STATIC_CONTENT_TYPES.get(ext, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if content and not head_only:
            self.wfile.write(content)

    # ---------- HTTP 方法入口 ----------

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


class ItemsHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db_path):
        super().__init__(address, RequestHandler)
        self.db_path = db_path


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="事项管理应用（Python 标准库 HTTP + SQLite，零外部依赖）"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认 127.0.0.1；对外预览可用 0.0.0.0）",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="监听端口（默认 8000；0 = 随机端口）"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite 数据库文件路径（默认 <本目录>/data/items.db）",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    db_path = os.path.abspath(args.db)
    conn = db.connect(db_path)  # 预热：建目录、建表，尽早暴露路径错误
    conn.close()
    httpd = ItemsHTTPServer((args.host, args.port), db_path)
    print(
        "事项管理应用已启动: http://%s:%d/  (db: %s)"
        % (args.host, httpd.server_port, db_path),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("已停止。", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
