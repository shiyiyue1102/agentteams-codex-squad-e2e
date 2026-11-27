#!/usr/bin/env python3
"""事项管理应用 HTTP 服务（仅 Python 标准库）。

启动:
    python3 server.py                          # 默认 0.0.0.0:8000, db=./issues.db
    python3 server.py --host 0.0.0.0 --port 8000 --db /tmp/issues-test.db

契约（docs/SPEC.md）要点:
- /api/issues          GET(列表/搜索/筛选/分页) POST(新增)
- /api/issues/{id}     GET(单条) PUT(全量更新) DELETE(删除)
- 统一错误信封 {"error": {"code", "message", "field"?}}
- 校验顺序: PUT/DELETE 先 id 解析 -> 404 存在性 -> body 校验;
  POST body: JSON 解析 -> title -> status -> priority;
  列表查询参数: status -> page -> page_size。
"""

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import db as dbmod

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

TITLE_MAX_LEN = 80
DECIMAL_RE = re.compile(r"^\d+$")
API_ITEM_RE = re.compile(r"^/api/issues/([^/]+)$")
API_LIST_PATH = "/api/issues"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


class ApiError(Exception):
    """带 HTTP 状态码与稳定错误码的 API 校验/业务错误。"""

    def __init__(self, status, code, message, field=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field


def error_payload(code, message, field=None):
    err = {"code": code, "message": message}
    if field is not None:
        err["field"] = field
    return {"error": err}


# ---------- body 校验（顺序: title -> status -> priority，见 D9/D10） ----------

def validate_title(value):
    if not isinstance(value, str):
        raise ApiError(400, "TITLE_REQUIRED", "标题去除两端空白后不能为空", "title")
    title = value.strip()
    if not title:
        raise ApiError(400, "TITLE_REQUIRED", "标题去除两端空白后不能为空", "title")
    if len(title) > TITLE_MAX_LEN:
        raise ApiError(
            400, "TITLE_TOO_LONG", "标题去除两端空白后不能超过 80 字", "title"
        )
    return title


def validate_enum(value, allowed, code, message, field):
    if not isinstance(value, str) or value not in allowed:
        raise ApiError(400, code, message, field)
    return value


def validate_create_body(data):
    """POST：title 必填；status/priority 缺失或 null 走默认值。"""
    raw_title = data.get("title")
    if raw_title is None:
        raise ApiError(400, "TITLE_REQUIRED", "标题去除两端空白后不能为空", "title")
    title = validate_title(raw_title)

    status = data.get("status")
    if status is None:
        status = "todo"
    else:
        validate_enum(
            status, dbmod.STATUS_VALUES, "INVALID_STATUS",
            "状态必须是 todo、in_progress 或 done 之一", "status",
        )

    priority = data.get("priority")
    if priority is None:
        priority = "medium"
    else:
        validate_enum(
            priority, dbmod.PRIORITY_VALUES, "INVALID_PRIORITY",
            "优先级必须是 low、medium 或 high 之一", "priority",
        )
    return title, status, priority


def validate_update_body(data):
    """PUT：全量更新，三字段全部必填，缺失或 null 均非法。"""
    title = validate_title(data.get("title"))
    status = validate_enum(
        data.get("status"), dbmod.STATUS_VALUES, "INVALID_STATUS",
        "状态必须是 todo、in_progress 或 done 之一", "status",
    )
    priority = validate_enum(
        data.get("priority"), dbmod.PRIORITY_VALUES, "INVALID_PRIORITY",
        "优先级必须是 low、medium 或 high 之一", "priority",
    )
    return title, status, priority


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "IssueBoardDsh/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 通用发送 ----------

    def _send_bytes(self, status, content_type, data, head_only=False,
                    extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only and data:
            self.wfile.write(data)

    def _send_json(self, status, obj, head_only=False, extra_headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            status, "application/json; charset=utf-8", data,
            head_only=head_only, extra_headers=extra_headers,
        )

    def _send_plain(self, status, text, head_only=False, extra_headers=None):
        self._send_bytes(
            status, "text/plain; charset=utf-8", text.encode("utf-8"),
            head_only=head_only, extra_headers=extra_headers,
        )

    def _method_not_allowed(self, allow):
        self._send_json(
            405,
            error_payload("METHOD_NOT_ALLOWED", "该路径不支持此请求方法"),
            extra_headers={"Allow": allow},
        )

    # ---------- 请求体 ----------

    def _read_body_bytes(self):
        """读取并排空请求体（保持 keep-alive 连接状态一致）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    @staticmethod
    def _parse_json_object(raw):
        if not raw:
            raise ApiError(400, "INVALID_BODY", "请求体缺失，必须是合法的 JSON 对象")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "INVALID_BODY", "请求体不是合法的 JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "INVALID_BODY", "请求体必须是 JSON 对象")
        return data

    # ---------- 路由 ----------

    def do_GET(self):
        self._route("GET", head_only=False)

    def do_HEAD(self):
        self._route("HEAD", head_only=True)

    def do_POST(self):
        self._route("POST", head_only=False)

    def do_PUT(self):
        self._route("PUT", head_only=False)

    def do_DELETE(self):
        self._route("DELETE", head_only=False)

    def do_PATCH(self):
        self._route("PATCH", head_only=False)

    def do_OPTIONS(self):
        self._route("OPTIONS", head_only=False)

    def _route(self, method, head_only):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api" or path.startswith("/api/"):
            # 请求体一律先读取排空（D10：不论 Content-Type 均按 JSON 解析）
            body = self._read_body_bytes() if method in (
                "POST", "PUT", "PATCH", "DELETE") else None
            try:
                self._handle_api(method, path, parsed.query, body)
            except ApiError as exc:
                self._send_json(
                    exc.status, error_payload(exc.code, exc.message, exc.field))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._send_json(
                    500, error_payload("INTERNAL_ERROR", "服务器内部错误"))
        else:
            self._serve_static(method, path, head_only)

    # ---------- API ----------

    def _handle_api(self, method, path, query, body):
        if path == API_LIST_PATH:
            if method == "GET":
                return self._api_list(query)
            if method == "POST":
                return self._api_create(body)
            return self._method_not_allowed("GET, POST")

        match = API_ITEM_RE.match(path)
        if match:
            if method not in ("GET", "PUT", "DELETE"):
                return self._method_not_allowed("GET, PUT, DELETE")
            raw_id = match.group(1)
            if not DECIMAL_RE.match(raw_id):
                raise ApiError(
                    400, "INVALID_PARAM", "事项 id 必须是十进制整数", "id")
            return self._api_item(method, int(raw_id), body)

        raise ApiError(404, "NOT_FOUND", "接口路径不存在")

    def _api_list(self, query):
        params = parse_qs(query, keep_blank_values=True)

        def first(name):
            values = params.get(name)
            return values[0] if values else None

        # 校验顺序: status -> page -> page_size（见 D9）
        status = first("status")
        if status is not None:
            if status == "":
                status = None  # 空串视为未过滤
            elif status not in dbmod.STATUS_VALUES:
                raise ApiError(
                    400, "INVALID_STATUS",
                    "状态筛选必须是 todo、in_progress 或 done 之一", "status")

        page = self._parse_int_param(first("page"), 1, 1, None, "page",
                                     "page 必须是大于等于 1 的整数")
        page_size = self._parse_int_param(first("page_size"), 10, 1, 100,
                                          "page_size",
                                          "page_size 必须是 1 到 100 之间的整数")

        keyword = first("keyword")
        if keyword is not None and not keyword.strip():
            keyword = None  # 空串或纯空白视为未提供

        conn = dbmod.connect(self.server.db_path)
        try:
            items, total, total_pages = dbmod.list_issues(
                conn, keyword=keyword, status=status,
                page=page, page_size=page_size)
        finally:
            conn.close()
        self._send_json(200, {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })

    @staticmethod
    def _parse_int_param(raw, default, minimum, maximum, field, message):
        if raw is None:
            return default
        if not DECIMAL_RE.match(raw):
            raise ApiError(400, "INVALID_PARAM", message, field)
        value = int(raw)
        if value < minimum or (maximum is not None and value > maximum):
            raise ApiError(400, "INVALID_PARAM", message, field)
        return value

    def _api_create(self, body):
        data = self._parse_json_object(body)
        title, status, priority = validate_create_body(data)
        conn = dbmod.connect(self.server.db_path)
        try:
            issue = dbmod.create_issue(conn, title, status, priority)
        finally:
            conn.close()
        self._send_json(201, {"issue": issue})

    def _api_item(self, method, issue_id, body):
        conn = dbmod.connect(self.server.db_path)
        try:
            if method == "GET":
                issue = dbmod.get_issue_by_id(conn, issue_id)
                if issue is None:
                    raise ApiError(404, "NOT_FOUND", "事项不存在")
                self._send_json(200, {"issue": issue})
            elif method == "PUT":
                # 404 优先于 body 校验（见 D9）
                existing = dbmod.get_issue_by_id(conn, issue_id)
                if existing is None:
                    raise ApiError(404, "NOT_FOUND", "事项不存在")
                data = self._parse_json_object(body)
                title, status, priority = validate_update_body(data)
                issue = dbmod.update_issue(
                    conn, issue_id, title, status, priority)
                self._send_json(200, {"issue": issue})
            else:  # DELETE
                if not dbmod.delete_issue(conn, issue_id):
                    raise ApiError(404, "NOT_FOUND", "事项不存在")
                self._send_json(200, {"ok": True, "id": issue_id})
        finally:
            conn.close()

    # ---------- 静态资源 ----------

    def _serve_static(self, method, path, head_only):
        if method not in ("GET", "HEAD"):
            return self._send_plain(
                405, "405 Method Not Allowed", head_only=head_only,
                extra_headers={"Allow": "GET, HEAD"})
        if path == "/":
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = unquote(path[len("/static/"):])
        else:
            return self._send_plain(404, "404 Not Found", head_only=head_only)

        if not rel or rel.endswith("/") or "\x00" in rel:
            return self._send_plain(404, "404 Not Found", head_only=head_only)

        static_root = STATIC_DIR.resolve()
        try:
            candidate = (STATIC_DIR / rel).resolve()
            if candidate != static_root and static_root not in candidate.parents:
                return self._send_plain(
                    404, "404 Not Found", head_only=head_only)
            if not candidate.is_file():
                return self._send_plain(
                    404, "404 Not Found", head_only=head_only)
            data = candidate.read_bytes()
        except (OSError, ValueError):
            return self._send_plain(404, "404 Not Found", head_only=head_only)

        content_type = CONTENT_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream")
        self._send_bytes(200, content_type, data, head_only=head_only)

    def log_message(self, format, *args):  # noqa: A002 - 保持安静输出
        pass


def create_server(host, port, db_path):
    """创建（但不启动）HTTP 服务；port=0 时由系统分配，
    实际端口从 server.server_address[1] 读取。"""
    db_path = Path(db_path).expanduser()
    if str(db_path.parent):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    dbmod.init_db(str(db_path))
    server = ThreadingHTTPServer((host, port), RequestHandler)
    server.daemon_threads = True
    server.db_path = str(db_path)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="事项管理应用服务（Python 标准库 + SQLite，无外部依赖）")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000,
                        help="监听端口（默认 8000；0 表示随机可用端口）")
    parser.add_argument("--db", default=str(BASE_DIR / "issues.db"),
                        help="SQLite 数据库文件路径（默认 case 目录下 issues.db）")
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port, args.db)
    host, port = server.server_address[0], server.server_address[1]
    print("Serving on http://%s:%d/ (db: %s)" % (host, port, server.db_path),
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
