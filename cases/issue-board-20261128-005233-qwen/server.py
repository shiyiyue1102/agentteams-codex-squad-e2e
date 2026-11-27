#!/usr/bin/env python3
"""事项管理应用（Issue Board）HTTP 服务入口。

同一端口同时提供 REST API 与静态前端页面，仅使用 Python 标准库
（http.server + sqlite3），无任何第三方运行依赖。

用法：
    python3 server.py                                # 默认 127.0.0.1:8000，DB 文件 ./issue_board.db
    python3 server.py --host 0.0.0.0 --port 8123 --db /tmp/issue_board_test.db

等价环境变量：ISSUE_BOARD_HOST / ISSUE_BOARD_PORT / ISSUE_BOARD_DB；
CLI 参数优先于环境变量。测试可用 ISSUE_BOARD_QUIET=1 关闭请求日志。
"""

import argparse
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

ID_PATTERN = re.compile(r"^[1-9][0-9]*$")           # 合法的路径 {id}
UINT_PATTERN = re.compile(r"^[0-9]+$")              # 合法的非负整数查询参数
ITEM_PATH_PATTERN = re.compile(r"^/api/items/([^/]+)$")
MAX_TITLE_LENGTH = 80                               # Unicode 码点数
MAX_BODY_BYTES = 1024 * 1024                        # 请求体上限 1MB，防止滥用

# 错误码 -> 人类可读中文 message（契约只断言 code 与 HTTP 状态）
ERROR_MESSAGES = {
    "invalid_json": "请求体必须是合法的 JSON 对象",
    "invalid_id": "事项 id 必须是正整数",
    "title_required": "标题去除两端空白后必填",
    "title_too_long": "标题去除两端空白后不能超过 80 字",
    "invalid_status": "状态必须是 todo（待办）、doing（进行中）或 done（完成）",
    "invalid_priority": "优先级必须是 high（高）、medium（中）或 low（低）",
    "invalid_param": "查询参数非法：page 须为 ≥1 的整数，page_size 须为 1–100 的整数，status 须为合法枚举值",
    "empty_update": "请求体必须包含至少一个可更新字段（title/status/priority）",
    "item_not_found": "指定的事项不存在",
    "not_found": "请求的路径不存在",
    "method_not_allowed": "该路径不允许此 HTTP 方法",
    "internal_error": "服务器内部错误",
}


class ApiError(Exception):
    """带 HTTP 状态码与契约错误码的业务错误。"""

    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# 请求体字段校验（POST 与 PUT 共用，错误码与契约一致）
# ---------------------------------------------------------------------------

def clean_title(value):
    """标题：必须是字符串，trim 后非空且 ≤80 字（Unicode 码点）。"""
    if not isinstance(value, str):
        raise ApiError(400, "title_required")
    title = value.strip()
    if not title:
        raise ApiError(400, "title_required")
    if len(title) > MAX_TITLE_LENGTH:
        raise ApiError(400, "title_too_long")
    return title


def clean_status(value):
    if not isinstance(value, str) or value not in db.VALID_STATUS:
        raise ApiError(400, "invalid_status")
    return value


def clean_priority(value):
    if not isinstance(value, str) or value not in db.VALID_PRIORITY:
        raise ApiError(400, "invalid_priority")
    return value


def cleaned_updatable_fields(body):
    """校验请求体中出现的可更新字段，返回清洗后的字段字典。

    未知字段一律忽略；类型错误按对应枚举/必填错误处理
    （如 status: 1 -> invalid_status，title: null -> title_required）。
    """
    fields = {}
    if "title" in body:
        fields["title"] = clean_title(body["title"])
    if "status" in body:
        fields["status"] = clean_status(body["status"])
    if "priority" in body:
        fields["priority"] = clean_priority(body["priority"])
    return fields


class IssueBoardHandler(BaseHTTPRequestHandler):
    """统一处理 API 与静态文件的请求 Handler。

    所有响应都带精确 Content-Length（204 除外），支持 HTTP/1.1 keep-alive。
    """

    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"

    # 由 make_server() 为每个服务实例覆盖为实际数据库路径
    db_path = os.path.join(BASE_DIR, "issue_board.db")

    # ------------------------------------------------------------------
    # 所有 HTTP 方法进入统一入口
    # ------------------------------------------------------------------

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def do_OPTIONS(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def handle_request(self):
        self._response_started = False
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self.route_api(self.command, path, parsed.query)
            else:
                self.route_static(self.command, path)
        except ApiError as exc:
            if not self._response_started:
                self.send_json_error(exc.status, exc.code)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前断开连接，无需处理
        except Exception:
            # 契约 3.7：服务端未捕获异常 -> 500 internal_error
            if not self._response_started:
                try:
                    self.send_json_error(500, "internal_error")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def route_api(self, method, path, query):
        if path in ("/api/items", "/api/items/"):
            if method == "GET":
                self.api_list_items(query)
            elif method == "POST":
                self.api_create_item()
            else:
                raise ApiError(405, "method_not_allowed")
            return

        match = ITEM_PATH_PATTERN.match(path)
        if match is None:
            raise ApiError(404, "not_found")

        raw_id = match.group(1)
        if method not in ("GET", "PUT", "DELETE"):
            raise ApiError(405, "method_not_allowed")
        if not ID_PATTERN.match(raw_id):
            raise ApiError(400, "invalid_id")
        item_id = int(raw_id)

        if method == "GET":
            self.api_get_item(item_id)
        elif method == "PUT":
            self.api_update_item(item_id)
        else:
            self.api_delete_item(item_id)

    def route_static(self, method, path):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = os.path.normpath(os.path.join(STATIC_DIR, relative))
        inside_static = candidate == STATIC_DIR or candidate.startswith(
            STATIC_DIR + os.sep
        )
        if not inside_static or not os.path.isfile(candidate):
            # 未知路径（含路径穿越尝试）一律 404，只允许 static/ 内文件
            raise ApiError(404, "not_found")
        if method not in ("GET", "HEAD"):
            raise ApiError(405, "method_not_allowed")

        content_type = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        with open(candidate, "rb") as handle:
            body = handle.read()

        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if method == "GET":
            self.wfile.write(body)

    # ------------------------------------------------------------------
    # API 处理器
    # ------------------------------------------------------------------

    def api_list_items(self, query):
        params = parse_qs(query, keep_blank_values=True)

        q = params.get("q", [""])[0]        # 空串等同不传
        status = None
        if "status" in params:
            status = params["status"][0]
            if status not in db.VALID_STATUS:
                raise ApiError(400, "invalid_param")

        page = 1
        if "page" in params:
            page = self._parse_int_param(params["page"][0], minimum=1)
        page_size = 10
        if "page_size" in params:
            page_size = self._parse_int_param(
                params["page_size"][0], minimum=1, maximum=100
            )

        result = db.list_items(
            self.db_path, q=q or None, status=status, page=page, page_size=page_size
        )
        self.send_json(200, result)

    @staticmethod
    def _parse_int_param(raw, minimum, maximum=None):
        if not UINT_PATTERN.match(raw):
            raise ApiError(400, "invalid_param")
        value = int(raw)
        if value < minimum:
            raise ApiError(400, "invalid_param")
        if maximum is not None and value > maximum:
            raise ApiError(400, "invalid_param")
        return value

    def api_create_item(self):
        body = self.read_json_object()
        if "title" not in body:
            raise ApiError(400, "title_required")
        fields = cleaned_updatable_fields(body)
        item = db.create_item(
            self.db_path,
            fields["title"],
            fields.get("status", "todo"),
            fields.get("priority", "medium"),
        )
        self.send_json(
            201, item, extra_headers={"Location": "/api/items/%d" % item["id"]}
        )

    def api_get_item(self, item_id):
        item = db.get_item(self.db_path, item_id)
        if item is None:
            raise ApiError(404, "item_not_found")
        self.send_json(200, item)

    def api_update_item(self, item_id):
        body = self.read_json_object()
        fields = cleaned_updatable_fields(body)
        if not fields:
            raise ApiError(400, "empty_update")
        updated = db.update_item(self.db_path, item_id, fields)
        if updated is None:
            raise ApiError(404, "item_not_found")
        self.send_json(200, updated)

    def api_delete_item(self, item_id):
        if not db.delete_item(self.db_path, item_id):
            raise ApiError(404, "item_not_found")
        self._response_started = True
        self.send_response(204)
        self.end_headers()

    # ------------------------------------------------------------------
    # 请求体解析与响应输出
    # ------------------------------------------------------------------

    def read_json_object(self):
        """读取并解析请求体；必须是 JSON 对象，否则 400 invalid_json。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_json")
        if length <= 0:
            raise ApiError(400, "invalid_json")
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError(400, "invalid_json")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid_json")
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json")
        return body

    def send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json_error(self, status, code):
        message = ERROR_MESSAGES.get(code, code)
        self.send_json(status, {"error": {"code": code, "message": message}})

    def log_message(self, fmt, *args):
        if os.environ.get("ISSUE_BOARD_QUIET") == "1":
            return
        super().log_message(fmt, *args)


class IssueBoardServer(ThreadingHTTPServer):
    """多线程 HTTP 服务器：daemon 线程，关闭时不阻塞等待请求线程。"""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True


def make_server(host, port, db_path):
    """初始化数据库并创建绑定 db_path 的服务器实例。

    port 传 0 时由系统分配临时端口（供测试使用）。
    """
    absolute_db = os.path.abspath(db_path)
    parent = os.path.dirname(absolute_db)
    if parent:
        os.makedirs(parent, exist_ok=True)
    db.init_db(absolute_db)
    handler_class = type(
        "BoundIssueBoardHandler", (IssueBoardHandler,), {"db_path": absolute_db}
    )
    return IssueBoardServer((host, port), handler_class)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="事项管理应用（Issue Board）：Python 标准库 HTTP 服务 + SQLite"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ISSUE_BOARD_HOST", "127.0.0.1"),
        help="监听地址，默认 127.0.0.1；远程浏览器预览可用 0.0.0.0",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ISSUE_BOARD_PORT", "8000")),
        help="监听端口，默认 8000",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("ISSUE_BOARD_DB", "issue_board.db"),
        help="SQLite 数据库文件路径，默认 ./issue_board.db（相对启动时工作目录）",
    )
    args = parser.parse_args(argv)

    server = make_server(args.host, args.port, args.db)
    host, port = server.server_address[0], server.server_address[1]
    print("Issue Board 已启动: http://%s:%d/" % (host, port), flush=True)
    print("数据库文件: %s" % os.path.abspath(args.db), flush=True)
    print("按 Ctrl+C 停止服务", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务…", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
