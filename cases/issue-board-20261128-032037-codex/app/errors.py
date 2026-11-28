"""错误码常量、ApiError 异常与 HTTP 状态映射（契约第 6 节）。"""

INVALID_BODY = "INVALID_BODY"
TITLE_REQUIRED = "TITLE_REQUIRED"
TITLE_TOO_LONG = "TITLE_TOO_LONG"
INVALID_STATUS = "INVALID_STATUS"
INVALID_PRIORITY = "INVALID_PRIORITY"
INVALID_ID = "INVALID_ID"
INVALID_PAGE = "INVALID_PAGE"
INVALID_PAGE_SIZE = "INVALID_PAGE_SIZE"
ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
INTERNAL_ERROR = "INTERNAL_ERROR"

HTTP_STATUS = {
    INVALID_BODY: 400,
    TITLE_REQUIRED: 400,
    TITLE_TOO_LONG: 400,
    INVALID_STATUS: 400,
    INVALID_PRIORITY: 400,
    INVALID_ID: 400,
    INVALID_PAGE: 400,
    INVALID_PAGE_SIZE: 400,
    ITEM_NOT_FOUND: 404,
    NOT_FOUND: 404,
    METHOD_NOT_ALLOWED: 405,
    PAYLOAD_TOO_LARGE: 413,
    INTERNAL_ERROR: 500,
}

MESSAGES = {
    INVALID_BODY: "请求体必须是合法的 JSON 对象",
    TITLE_REQUIRED: "标题不能为空",
    TITLE_TOO_LONG: "标题不能超过 80 个字符",
    INVALID_STATUS: "状态只能是 todo、doing 或 done",
    INVALID_PRIORITY: "优先级只能是 low、medium 或 high",
    INVALID_ID: "事项 ID 非法",
    INVALID_PAGE: "页码必须是不小于 1 的整数",
    INVALID_PAGE_SIZE: "每页条数必须是 1 到 100 之间的整数",
    ITEM_NOT_FOUND: "事项不存在",
    NOT_FOUND: "资源不存在",
    METHOD_NOT_ALLOWED: "请求方法不被允许",
    PAYLOAD_TOO_LARGE: "请求体过大",
    INTERNAL_ERROR: "服务器内部错误",
}


class ApiError(Exception):
    """业务错误：携带错误码、中文消息、可选字段与结构化补充信息。"""

    def __init__(self, code, field=None, message=None, details=None, headers=None):
        self.code = code
        self.field = field
        self.message = message or MESSAGES[code]
        self.details = details
        self.headers = dict(headers or {})
        super().__init__(self.message)

    @property
    def status(self):
        return HTTP_STATUS[self.code]

    def to_payload(self):
        error = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}
