"""REST 端点业务处理：health/meta/items 列表/新增/详情/编辑/删除（契约第 4、6 节）。"""

import json
import math

from . import APP_VERSION
from .db import connect, count_items, delete_item, fetch_item, insert_item, list_items, update_item
from .errors import ApiError, INVALID_BODY, ITEM_NOT_FOUND, METHOD_NOT_ALLOWED, NOT_FOUND
from .validation import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_PRIORITY,
    DEFAULT_STATUS,
    PAGE_SIZE_OPTIONS,
    PRIORITY_LABELS,
    STATUS_LABELS,
    TITLE_MAX_LENGTH,
    normalize_title,
    parse_item_id,
    parse_priority,
    parse_query_keyword,
    parse_query_page,
    parse_query_page_size,
    parse_query_status,
    parse_status,
    require_string_title,
)

API_PREFIX = "/api/v1"
HEALTH_PATH = API_PREFIX + "/health"
META_PATH = API_PREFIX + "/meta"
ITEMS_PATH = API_PREFIX + "/items"

READ_ONLY_METHODS = ("GET",)
COLLECTION_METHODS = ("GET", "POST")
DETAIL_METHODS = ("GET", "PUT", "DELETE")


class JsonResponse:
    """一次 API 应答：HTTP 状态、JSON 负载与附加响应头。"""

    __slots__ = ("status", "payload", "headers")

    def __init__(self, status, payload, headers=None):
        self.status = status
        self.payload = payload
        self.headers = dict(headers or {})


def is_api_path(path):
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def parse_json_object(raw_body):
    """空 body、非法 JSON、顶层非对象 → 400 INVALID_BODY（契约 6.2）。"""
    if not raw_body:
        raise ApiError(INVALID_BODY)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(INVALID_BODY) from None
    if not isinstance(payload, dict):
        raise ApiError(INVALID_BODY)
    return payload


def dispatch(method, path, query_pairs, raw_body, db_path):
    """按路径与方法分发到具体端点；未匹配则 404 NOT_FOUND。"""
    if not is_api_path(path):
        raise ApiError(NOT_FOUND)
    if path == HEALTH_PATH:
        _require_method(method, READ_ONLY_METHODS)
        return health()
    if path == META_PATH:
        _require_method(method, READ_ONLY_METHODS)
        return meta()
    if path == ITEMS_PATH:
        _require_method(method, COLLECTION_METHODS)
        if method == "GET":
            return list_items_endpoint(query_pairs, db_path)
        return create_item(raw_body, db_path)
    if path.startswith(ITEMS_PATH + "/"):
        raw_id = path[len(ITEMS_PATH) + 1:]
        if "/" in raw_id:
            raise ApiError(NOT_FOUND)
        _require_method(method, DETAIL_METHODS)
        if method == "GET":
            return get_item(raw_id, db_path)
        if method == "PUT":
            return update_item_endpoint(raw_id, raw_body, db_path)
        return delete_item_endpoint(raw_id, db_path)
    raise ApiError(NOT_FOUND)


def _require_method(method, allowed):
    if method not in allowed:
        raise ApiError(METHOD_NOT_ALLOWED, headers={"Allow": ", ".join(allowed)})


def _ok(data):
    return {"ok": True, "data": data}


def _first_values(query_pairs):
    """parse_qsl 结果转为 dict，重复键取首次出现的值；未声明的参数一律忽略（D15）。"""
    params = {}
    for name, value in query_pairs:
        if name not in params:
            params[name] = value
    return params


def health():
    return JsonResponse(200, _ok({"status": "ok", "version": APP_VERSION}))


def meta():
    return JsonResponse(
        200,
        _ok(
            {
                "statuses": [{"value": value, "label": label} for value, label in STATUS_LABELS],
                "priorities": [{"value": value, "label": label} for value, label in PRIORITY_LABELS],
                "page_size_options": list(PAGE_SIZE_OPTIONS),
                "default_page_size": DEFAULT_PAGE_SIZE,
                "title_max_length": TITLE_MAX_LENGTH,
            }
        ),
    )


def list_items_endpoint(query_pairs, db_path):
    params = _first_values(query_pairs)
    keyword = parse_query_keyword(params)
    status = parse_query_status(params)
    page = parse_query_page(params)
    page_size = parse_query_page_size(params)
    offset = (page - 1) * page_size

    connection = connect(db_path)
    try:
        total = count_items(connection, keyword=keyword, status=status)
        items = list_items(connection, keyword=keyword, status=status, limit=page_size, offset=offset)
    finally:
        connection.close()

    total_pages = max(1, int(math.ceil(total / float(page_size))))
    return JsonResponse(
        200,
        _ok(
            {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "q": keyword,
                "status": status,
            }
        ),
    )


def create_item(raw_body, db_path):
    """校验顺序：体积（handler）→ JSON/顶层类型 → title 类型 → title → status → priority。"""
    payload = parse_json_object(raw_body)
    require_string_title(payload)
    title = normalize_title(payload)
    status = parse_status(payload, DEFAULT_STATUS)
    priority = parse_priority(payload, DEFAULT_PRIORITY)

    connection = connect(db_path)
    try:
        item = insert_item(connection, title, status, priority)
    finally:
        connection.close()
    return JsonResponse(201, _ok(item))


def get_item(raw_id, db_path):
    item_id = parse_item_id(raw_id)
    connection = connect(db_path)
    try:
        item = fetch_item(connection, item_id)
    finally:
        connection.close()
    if item is None:
        raise ApiError(ITEM_NOT_FOUND, field="id", details={"id": item_id})
    return JsonResponse(200, _ok(item))


def update_item_endpoint(raw_id, raw_body, db_path):
    """校验顺序（D6）：INVALID_ID → INVALID_BODY → ITEM_NOT_FOUND → 字段校验。"""
    item_id = parse_item_id(raw_id)
    payload = parse_json_object(raw_body)
    require_string_title(payload)

    connection = connect(db_path)
    try:
        current = fetch_item(connection, item_id)
        if current is None:
            raise ApiError(ITEM_NOT_FOUND, field="id", details={"id": item_id})
        title = normalize_title(payload)
        status = parse_status(payload, current["status"])
        priority = parse_priority(payload, current["priority"])
        item = update_item(connection, item_id, title, status, priority)
    finally:
        connection.close()
    return JsonResponse(200, _ok(item))


def delete_item_endpoint(raw_id, db_path):
    item_id = parse_item_id(raw_id)
    connection = connect(db_path)
    try:
        if fetch_item(connection, item_id) is None:
            raise ApiError(ITEM_NOT_FOUND, field="id", details={"id": item_id})
        delete_item(connection, item_id)
    finally:
        connection.close()
    return JsonResponse(200, _ok({"id": item_id, "deleted": True}))
