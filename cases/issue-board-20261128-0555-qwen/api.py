"""REST API 路由与校验层（仅 Python 标准库）。

契约（分析报告 §2/§3）：
    GET    /api/items       列表：page/page_size/q/status，排序 created_at DESC, id DESC
    POST   /api/items       新增：title 必填（去空白后非空且 ≤80 字符），status/priority 可选
    GET    /api/items/{id}  单条查询（{id} 非正整数按 404 处理）
    PUT    /api/items/{id}  部分更新（title/status/priority 至少一个，合并语义）
    DELETE /api/items/{id}  删除（204，无响应体）

统一错误体：{"error": {"code", "message", "field"?}}
    VALIDATION_ERROR=400 / NOT_FOUND=404 / METHOD_NOT_ALLOWED=405 / INTERNAL_ERROR=500
"""

import json
import re

import db

TITLE_MAX_LEN = 80
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 100
DEFAULT_STATUS = "todo"
DEFAULT_PRIORITY = "medium"

_ITEM_PATH_RE = re.compile(r"^/api/items/([^/]+)$")


class ApiError(Exception):
    """携带 HTTP 状态码与统一错误体的 API 异常。"""

    def __init__(self, status, code, message, field=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self):
        error = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        return {"error": error}


def _validation(message, field=None):
    return ApiError(400, "VALIDATION_ERROR", message, field)


def _not_found(message="请求的资源不存在"):
    return ApiError(404, "NOT_FOUND", message)


def _method_not_allowed(message="该路径不支持此请求方法"):
    return ApiError(405, "METHOD_NOT_ALLOWED", message)


def _validate_title(value):
    """title：必须是字符串，去两端空白后非空且 ≤80 字符；返回去空白后的值。"""
    if not isinstance(value, str):
        raise _validation("title 必须是字符串", "title")
    title = value.strip()
    if not title:
        raise _validation("title 去除两端空白后不能为空", "title")
    if len(title) > TITLE_MAX_LEN:
        raise _validation(
            "title 长度不能超过 %d 字符（当前 %d 字符）" % (TITLE_MAX_LEN, len(title)),
            "title",
        )
    return title


def _validate_enum(value, allowed, name):
    if not isinstance(value, str) or value not in allowed:
        raise _validation("%s 必须是 %s 之一" % (name, " / ".join(allowed)), name)
    return value


def _parse_json_object(body_bytes):
    """请求体必须是非空的 UTF-8 JSON 对象；否则 400（field=body）。"""
    if not body_bytes:
        raise _validation("请求体不能为空，必须是 JSON 对象", "body")
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except ValueError:
        # UnicodeDecodeError 是 ValueError 的子类，一并覆盖
        raise _validation("请求体不是合法的 UTF-8 JSON", "body")
    if not isinstance(data, dict):
        raise _validation("请求体必须是 JSON 对象", "body")
    return data


def _parse_int_param(params, name, default, minimum=None, maximum=None):
    """整数查询参数：缺省用 default；出现即必须合法，否则 400（field=name）。"""
    if name not in params:
        return default
    raw = params[name]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _validation("%s 必须是整数" % name, name)
    if minimum is not None and value < minimum:
        raise _validation("%s 不能小于 %d" % (name, minimum), name)
    if maximum is not None and value > maximum:
        raise _validation("%s 不能大于 %d" % (name, maximum), name)
    return value


def _parse_item_id(raw):
    """路径 {id}：非正整数（含非数字）一律按 404 路由不匹配处理。"""
    try:
        item_id = int(raw)
    except (TypeError, ValueError):
        raise _not_found("事项 id 无效：%s" % raw)
    if item_id <= 0:
        raise _not_found("事项 id 无效：%s" % raw)
    return item_id


def _handle_list(conn, params):
    page = _parse_int_param(params, "page", default=1, minimum=1)
    page_size = _parse_int_param(
        params, "page_size", default=10, minimum=PAGE_SIZE_MIN, maximum=PAGE_SIZE_MAX
    )
    q = params.get("q")
    if q is not None:
        q = q.strip() or None  # 空白或缺省 = 不筛选
    status = params.get("status")
    if status is not None and status not in db.VALID_STATUS:
        raise _validation(
            "status 必须是 %s 之一" % " / ".join(db.VALID_STATUS), "status"
        )
    return 200, db.list_items(conn, page=page, page_size=page_size, q=q, status=status)


def _handle_create(conn, body_bytes):
    data = _parse_json_object(body_bytes)
    if data.get("title") is None:
        raise _validation(
            "title 为必填字段（去两端空白后非空且不超过 %d 字符）" % TITLE_MAX_LEN,
            "title",
        )
    title = _validate_title(data["title"])
    status = data.get("status")
    status = DEFAULT_STATUS if status is None else _validate_enum(
        status, db.VALID_STATUS, "status"
    )
    priority = data.get("priority")
    priority = DEFAULT_PRIORITY if priority is None else _validate_enum(
        priority, db.VALID_PRIORITY, "priority"
    )
    return 201, db.create_item(conn, title=title, status=status, priority=priority)


def _handle_get_one(conn, item_id):
    item = db.get_item(conn, item_id)
    if item is None:
        raise _not_found("id=%d 的事项不存在" % item_id)
    return 200, item


def _handle_update(conn, item_id, body_bytes):
    # 先做字段校验（同时非法时返回 400），再检查存在性（404）。
    # null 视为缺省：对应字段保持原值（合并语义）。
    data = _parse_json_object(body_bytes)
    fields = {}
    if data.get("title") is not None:
        fields["title"] = _validate_title(data["title"])
    if data.get("status") is not None:
        fields["status"] = _validate_enum(data["status"], db.VALID_STATUS, "status")
    if data.get("priority") is not None:
        fields["priority"] = _validate_enum(
            data["priority"], db.VALID_PRIORITY, "priority"
        )
    if not fields:
        raise _validation(
            "至少需要提供 title / status / priority 中的一个有效字段", "body"
        )
    if db.get_item(conn, item_id) is None:
        raise _not_found("id=%d 的事项不存在" % item_id)
    return 200, db.update_item(conn, item_id, fields)


def _handle_delete(conn, item_id):
    if not db.delete_item(conn, item_id):
        raise _not_found("id=%d 的事项不存在" % item_id)
    return 204, None


def route_api(conn, method, path, params, body_bytes):
    """API 路由入口：返回 (status_code, payload)；payload=None 表示无响应体（204）。

    未知 /api/* 路径 → 404 NOT_FOUND；已定义路径的不支持方法 → 405。
    """
    normalized = path[:-1] if len(path) > 1 and path.endswith("/") else path
    if normalized == "/api/items":
        if method == "GET":
            return _handle_list(conn, params)
        if method == "POST":
            return _handle_create(conn, body_bytes)
        raise _method_not_allowed("/api/items 仅支持 GET / POST")
    match = _ITEM_PATH_RE.match(normalized)
    if match is not None:
        item_id = _parse_item_id(match.group(1))
        if method == "GET":
            return _handle_get_one(conn, item_id)
        if method == "PUT":
            return _handle_update(conn, item_id, body_bytes)
        if method == "DELETE":
            return _handle_delete(conn, item_id)
        raise _method_not_allowed("/api/items/{id} 仅支持 GET / PUT / DELETE")
    raise _not_found("接口路径不存在：%s" % path)
