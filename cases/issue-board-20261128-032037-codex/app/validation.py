"""输入校验：路径 id、标题、枚举、分页参数（契约 4.3-4.6、6.2、6.3）。"""

import re

from .errors import (
    ApiError,
    INVALID_BODY,
    INVALID_ID,
    INVALID_PAGE,
    INVALID_PAGE_SIZE,
    INVALID_PRIORITY,
    INVALID_STATUS,
    TITLE_REQUIRED,
    TITLE_TOO_LONG,
)

STATUS_LABELS = (("todo", "待办"), ("doing", "进行中"), ("done", "完成"))
PRIORITY_LABELS = (("low", "低"), ("medium", "中"), ("high", "高"))
STATUS_VALUES = tuple(value for value, _ in STATUS_LABELS)
PRIORITY_VALUES = tuple(value for value, _ in PRIORITY_LABELS)
DEFAULT_STATUS = "todo"
DEFAULT_PRIORITY = "medium"

TITLE_MAX_LENGTH = 80
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_OPTIONS = (10, 20, 50)
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 100

_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
_INTEGER_PATTERN = re.compile(r"^[0-9]+$")


def parse_item_id(raw_id):
    """路径 id 必须匹配 ^[1-9][0-9]*$；否则 400 INVALID_ID。"""
    if not isinstance(raw_id, str) or not _ID_PATTERN.match(raw_id):
        raise ApiError(INVALID_ID, field="id")
    return int(raw_id)


def require_string_title(payload):
    """请求体第 2 步：title 存在但不是字符串（含 null、数字、布尔）→ INVALID_BODY。"""
    if "title" in payload and not isinstance(payload["title"], str):
        raise ApiError(INVALID_BODY, field="title")


def normalize_title(payload):
    """title 必填：strip 后长度 1-80；返回规范化后的标题。"""
    if "title" not in payload:
        raise ApiError(TITLE_REQUIRED, field="title")
    title = payload["title"].strip()
    if not title:
        raise ApiError(TITLE_REQUIRED, field="title")
    if len(title) > TITLE_MAX_LENGTH:
        raise ApiError(
            TITLE_TOO_LONG,
            field="title",
            details={"max_length": TITLE_MAX_LENGTH, "actual_length": len(title)},
        )
    return title


def parse_status(payload, default):
    """POST/PUT 的 status：键缺失或 null → default；否则必须是枚举值（大小写敏感）。"""
    if "status" not in payload or payload["status"] is None:
        return default
    value = payload["status"]
    if not isinstance(value, str) or value not in STATUS_VALUES:
        raise ApiError(INVALID_STATUS, field="status", details={"allowed": list(STATUS_VALUES)})
    return value


def parse_priority(payload, default):
    """POST/PUT 的 priority：键缺失或 null → default；否则必须是枚举值（大小写敏感）。"""
    if "priority" not in payload or payload["priority"] is None:
        return default
    value = payload["priority"]
    if not isinstance(value, str) or value not in PRIORITY_VALUES:
        raise ApiError(INVALID_PRIORITY, field="priority", details={"allowed": list(PRIORITY_VALUES)})
    return value


def parse_query_keyword(params):
    """查询串 q：缺失 → 空串；始终 strip，空串表示不加关键词条件。"""
    value = params.get("q", "")
    return value.strip() if isinstance(value, str) else ""


def parse_query_status(params):
    """查询串 status：缺失或空串 → None（全部）；否则必须是枚举值。"""
    if "status" not in params:
        return None
    value = params["status"]
    if value == "":
        return None
    if value not in STATUS_VALUES:
        raise ApiError(INVALID_STATUS, field="status", details={"allowed": list(STATUS_VALUES)})
    return value


def parse_query_page(params):
    return _parse_int_param(params, "page", DEFAULT_PAGE, INVALID_PAGE, MIN_PAGE_SIZE, None)


def parse_query_page_size(params):
    return _parse_int_param(
        params, "page_size", DEFAULT_PAGE_SIZE, INVALID_PAGE_SIZE, MIN_PAGE_SIZE, MAX_PAGE_SIZE
    )


def _parse_int_param(params, name, default, code, minimum, maximum):
    """键缺失 → 默认值；空串/非十进制/带符号/带小数点/超范围 → 对应 400 错误码。"""
    if name not in params:
        return default
    raw = params[name]
    if not isinstance(raw, str) or not _INTEGER_PATTERN.match(raw):
        raise ApiError(code, field=name)
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise ApiError(code, field=name)
    return value
