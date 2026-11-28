"""业务校验、错误类型与用例编排（分析报告 §3.1 / §5 / §6）。"""

import json
import math
import re
from datetime import datetime, timezone

from . import repository

TITLE_MAX_LENGTH = 80
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 10
MAX_BODY_BYTES = 64 * 1024

STATUS_LABELS = (("todo", "待办"), ("in_progress", "进行中"), ("done", "完成"))
PRIORITY_LABELS = (("low", "低"), ("medium", "中"), ("high", "高"))
STATUSES = tuple(value for value, _ in STATUS_LABELS)
PRIORITIES = tuple(value for value, _ in PRIORITY_LABELS)
DEFAULT_STATUS = "todo"
DEFAULT_PRIORITY = "medium"
ALLOWED_FIELDS = ("title", "status", "priority")

POSITIVE_INTEGER = re.compile(r"^[0-9]+$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

MISSING = object()


class ApiError(Exception):
    """契约规定的统一错误体（§3.3）。"""

    def __init__(self, status, code, message, field=None, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.details = details if details is not None else {}

    def to_body(self):
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "field": self.field,
                "details": self.details,
            }
        }


def invalid_json():
    return ApiError(400, "INVALID_JSON", "请求体必须是合法的 JSON 对象")


def invalid_field(key):
    return ApiError(
        400,
        "INVALID_FIELD",
        "请求体包含约定之外的字段，仅允许 title / status / priority",
        None,
        {"key": key, "allowed": list(ALLOWED_FIELDS)},
    )


def title_required():
    return ApiError(400, "TITLE_REQUIRED", "标题去除两端空白后不能为空", "title")


def title_too_long(length):
    return ApiError(
        400,
        "TITLE_TOO_LONG",
        "标题不能超过 {0} 个字符".format(TITLE_MAX_LENGTH),
        "title",
        {"max_length": TITLE_MAX_LENGTH, "length": length},
    )


def invalid_status(value=MISSING):
    details = {"allowed": list(STATUSES)}
    if value is not MISSING:
        details["value"] = value
    return ApiError(
        400,
        "INVALID_STATUS",
        "状态取值非法，允许：todo / in_progress / done",
        "status",
        details,
    )


def invalid_priority(value=MISSING):
    details = {"allowed": list(PRIORITIES)}
    if value is not MISSING:
        details["value"] = value
    return ApiError(
        400,
        "INVALID_PRIORITY",
        "优先级取值非法，允许：low / medium / high",
        "priority",
        details,
    )


def invalid_id(value):
    return ApiError(400, "INVALID_ID", "事项 ID 必须是正整数", "id", {"value": value})


def invalid_page(value):
    return ApiError(
        400, "INVALID_PAGE", "页码必须是大于等于 1 的整数", "page", {"value": value}
    )


def invalid_page_size(value):
    return ApiError(
        400,
        "INVALID_PAGE_SIZE",
        "每页条数必须是 {0} 到 {1} 之间的整数".format(PAGE_SIZE_MIN, PAGE_SIZE_MAX),
        "page_size",
        {"value": value, "min": PAGE_SIZE_MIN, "max": PAGE_SIZE_MAX},
    )


def item_not_found(item_id):
    return ApiError(404, "ITEM_NOT_FOUND", "事项不存在", "id", {"id": item_id})


def not_found():
    return ApiError(404, "NOT_FOUND", "接口或资源不存在")


def method_not_allowed(allowed):
    return ApiError(
        405,
        "METHOD_NOT_ALLOWED",
        "该方法不被允许，允许：{0}".format(" / ".join(allowed)),
        None,
        {"allow": list(allowed)},
    )


def payload_too_large():
    return ApiError(
        413,
        "PAYLOAD_TOO_LARGE",
        "请求体超过 64 KiB 限制",
        None,
        {"max_bytes": MAX_BODY_BYTES},
    )


def internal_error():
    return ApiError(500, "INTERNAL_ERROR", "服务器内部错误，请稍后重试")


def utc_now():
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def meta_payload():
    return {
        "statuses": [{"value": v, "label": l} for v, l in STATUS_LABELS],
        "priorities": [{"value": v, "label": l} for v, l in PRIORITY_LABELS],
        "defaults": {"status": DEFAULT_STATUS, "priority": DEFAULT_PRIORITY},
        "title_max_length": TITLE_MAX_LENGTH,
        "page_size": {
            "min": PAGE_SIZE_MIN,
            "max": PAGE_SIZE_MAX,
            "default": PAGE_SIZE_DEFAULT,
        },
    }


def parse_json_body(raw):
    """§3.1 第 3 步：非 UTF-8 / 非法 JSON / 顶层不是对象 → INVALID_JSON。"""
    if not raw:
        raise invalid_json()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise invalid_json() from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise invalid_json() from exc
    if not isinstance(data, dict):
        raise invalid_json()
    return data


def validate_payload(data):
    """§3.1 第 4~8 步，返回 (title, status, priority)，title 已 strip。"""
    for key in data:
        if key not in ALLOWED_FIELDS:
            raise invalid_field(key)

    raw_title = data.get("title", MISSING)
    if not isinstance(raw_title, str):
        raise title_required()
    title = raw_title.strip()
    if not title:
        raise title_required()
    if len(title) > TITLE_MAX_LENGTH:
        raise title_too_long(len(title))

    raw_status = data.get("status", MISSING)
    if not isinstance(raw_status, str) or raw_status not in STATUSES:
        raise invalid_status(raw_status)

    raw_priority = data.get("priority", MISSING)
    if not isinstance(raw_priority, str) or raw_priority not in PRIORITIES:
        raise invalid_priority(raw_priority)

    return title, raw_status, raw_priority


def _parse_positive_int(raw, error_factory):
    if not isinstance(raw, str) or not POSITIVE_INTEGER.match(raw):
        raise error_factory(raw)
    value = int(raw)
    if value < 1:
        raise error_factory(raw)
    return value


def parse_item_id(raw):
    return _parse_positive_int(raw, invalid_id)


def parse_page(raw):
    if raw is None:
        return 1
    return _parse_positive_int(raw, invalid_page)


def parse_page_size(raw):
    if raw is None:
        return PAGE_SIZE_DEFAULT
    value = _parse_positive_int(raw, invalid_page_size)
    if value > PAGE_SIZE_MAX:
        raise invalid_page_size(raw)
    return value


def parse_status_filter(raw):
    if raw is None:
        return "all"
    value = raw.strip()
    if value == "" or value == "all":
        return "all"
    if value not in STATUSES:
        raise invalid_status(value)
    return value


def parse_search(raw):
    return "" if raw is None else raw.strip()


def create_item(connection, payload):
    title, status, priority = validate_payload(payload)
    timestamp = utc_now()
    item_id = repository.insert_item(connection, title, status, priority, timestamp)
    return repository.get_item(connection, item_id)


def get_item(connection, item_id):
    item = repository.get_item(connection, item_id)
    if item is None:
        raise item_not_found(item_id)
    return item


def update_item(connection, item_id, payload):
    title, status, priority = validate_payload(payload)
    timestamp = utc_now()
    updated = repository.update_item(
        connection, item_id, title, status, priority, timestamp
    )
    if not updated:
        raise item_not_found(item_id)
    return repository.get_item(connection, item_id)


def delete_item(connection, item_id):
    if not repository.delete_item(connection, item_id):
        raise item_not_found(item_id)
    return {"ok": True, "id": item_id}


def list_items(connection, params):
    query = parse_search(params.get("q"))
    status = parse_status_filter(params.get("status"))
    page = parse_page(params.get("page"))
    page_size = parse_page_size(params.get("page_size"))

    total = repository.count_items(connection, query, status)
    total_pages = max(1, math.ceil(total / page_size))
    items = repository.fetch_items(
        connection, query, status, page_size, (page - 1) * page_size
    )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "filters": {"q": query, "status": status},
    }
