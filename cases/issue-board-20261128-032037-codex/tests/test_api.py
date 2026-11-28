"""开发自测：REST 契约、错误码边界与 SQLite 持久化（契约第 8 节 API 部分）。

运行方式（case 目录内）：python3 -B -m unittest discover -s tests -v
每个用例使用独立临时目录 + 随机端口，不读写默认 data/issues.db。
"""

import json
import os
import re
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection

from app.db import init_db
from app.handler import IssueBoardServer

MISSING = object()
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ITEM_FIELDS = {"id", "title", "status", "priority", "created_at", "updated_at"}
MAX_BODY_BYTES = 1024 * 1024


class ServerTestCase(unittest.TestCase):
    """在临时目录建库、随机端口起服务，用例结束后关闭服务与连接。"""

    def setUp(self):
        temp_holder = tempfile.TemporaryDirectory()
        self.addCleanup(temp_holder.cleanup)
        self.temp_dir = temp_holder.name
        self.db_path = os.path.join(self.temp_dir, "issues.db")
        self.server = self._start_server()
        self.addCleanup(self._stop_server)

    def _start_server(self):
        init_db(self.db_path)
        server = IssueBoardServer(("127.0.0.1", 0), self.db_path)
        thread = threading.Thread(target=server.serve_forever, name="issue-board-test", daemon=True)
        thread.start()
        server.worker_thread = thread
        return server

    def _stop_server(self):
        server = getattr(self, "server", None)
        if server is None:
            return
        self.server = None
        server.shutdown()
        server.server_close()
        thread = getattr(server, "worker_thread", None)
        if thread is not None:
            thread.join(timeout=5)

    @property
    def port(self):
        return self.server.server_address[1]

    def call(self, method, path, payload=MISSING, raw=None, headers=None):
        """返回 (status, headers, body_bytes)；raw 用于发送非法/超大请求体。"""
        body = raw
        if body is None and payload is not MISSING:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {}
        if body is not None:
            request_headers["Content-Length"] = str(len(body))
        if headers:
            request_headers.update(headers)
        connection = HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def api(self, method, path, payload=MISSING, raw=None):
        status, headers, body = self.call(method, path, payload=payload, raw=raw)
        return status, headers, json.loads(body.decode("utf-8"))

    def create(self, title, **fields):
        payload = {"title": title}
        payload.update(fields)
        status, _, envelope = self.api("POST", "/api/v1/items", payload)
        self.assertEqual(status, 201, envelope)
        return envelope["data"]

    def assert_error(self, status, envelope, code, field=None, http_status=400):
        self.assertEqual(status, http_status, envelope)
        self.assertIs(envelope["ok"], False)
        error = envelope["error"]
        self.assertEqual(error["code"], code)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"], "错误消息不能为空")
        self.assertRegex(error["message"], r"[\u4e00-\u9fff]", "错误消息必须是中文")
        if field is None:
            self.assertNotIn("field", error)
        else:
            self.assertEqual(error["field"], field)


class HealthAndMetaTests(ServerTestCase):
    def test_health_returns_ok(self):
        status, headers, envelope = self.api("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertEqual(int(headers["Content-Length"]), len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")))
        self.assertEqual(envelope, {"ok": True, "data": {"status": "ok", "version": "1.0.0"}})

    def test_meta_matches_contract(self):
        status, _, envelope = self.api("GET", "/api/v1/meta")
        self.assertEqual(status, 200)
        self.assertEqual(
            envelope["data"],
            {
                "statuses": [
                    {"value": "todo", "label": "待办"},
                    {"value": "doing", "label": "进行中"},
                    {"value": "done", "label": "完成"},
                ],
                "priorities": [
                    {"value": "low", "label": "低"},
                    {"value": "medium", "label": "中"},
                    {"value": "high", "label": "高"},
                ],
                "page_size_options": [10, 20, 50],
                "default_page_size": 20,
                "title_max_length": 80,
            },
        )


class CreateAndReadTests(ServerTestCase):
    def test_create_uses_defaults_and_full_item_shape(self):
        status, _, envelope = self.api("POST", "/api/v1/items", {"title": "买牛奶"})
        self.assertEqual(status, 201)
        item = envelope["data"]
        self.assertEqual(set(item), ITEM_FIELDS)
        self.assertEqual(item["title"], "买牛奶")
        self.assertEqual(item["status"], "todo")
        self.assertEqual(item["priority"], "medium")
        self.assertGreaterEqual(item["id"], 1)
        self.assertEqual(item["created_at"], item["updated_at"])
        self.assertRegex(item["created_at"], TIMESTAMP_RE)

    def test_create_ignores_unknown_fields_and_strips_title(self):
        item = self.create("  买牛奶  ", id=999, created_at="1999-01-01T00:00:00Z", foo="bar")
        self.assertEqual(item["title"], "买牛奶")
        self.assertNotEqual(item["id"], 999)
        self.assertNotIn("foo", item)
        self.assertRegex(item["created_at"], TIMESTAMP_RE)

    def test_list_defaults_and_desc_ordering(self):
        first = self.create("第一条")
        second = self.create("第二条")
        third = self.create("第三条")
        status, _, envelope = self.api("GET", "/api/v1/items")
        self.assertEqual(status, 200)
        data = envelope["data"]
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 20)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["total_pages"], 1)
        self.assertEqual(data["q"], "")
        self.assertIsNone(data["status"])
        self.assertEqual([row["id"] for row in data["items"]], [third["id"], second["id"], first["id"]])

    def test_detail_matches_list_entry(self):
        created = self.create("详情一致性", status="doing", priority="high")
        status, _, envelope = self.api("GET", "/api/v1/items/%d" % created["id"])
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"], created)
        _, _, listing = self.api("GET", "/api/v1/items")
        row = [item for item in listing["data"]["items"] if item["id"] == created["id"]][0]
        self.assertEqual(row, envelope["data"])


class UpdateAndDeleteTests(ServerTestCase):
    def test_update_all_fields_refreshes_updated_at(self):
        created = self.create("原始标题")
        time.sleep(1.05)
        status, _, envelope = self.api(
            "PUT",
            "/api/v1/items/%d" % created["id"],
            {"title": "新标题", "status": "done", "priority": "high"},
        )
        self.assertEqual(status, 200)
        item = envelope["data"]
        self.assertEqual(item["id"], created["id"])
        self.assertEqual(item["title"], "新标题")
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["created_at"], created["created_at"])
        self.assertRegex(item["updated_at"], TIMESTAMP_RE)
        self.assertNotEqual(item["updated_at"], created["updated_at"])

    def test_update_keeps_untouched_and_null_fields(self):
        created = self.create("保持原值", status="doing", priority="low")
        time.sleep(1.05)
        status, _, envelope = self.api("PUT", "/api/v1/items/%d" % created["id"], {"title": "只改标题"})
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["status"], "doing")
        self.assertEqual(envelope["data"]["priority"], "low")

        status, _, envelope = self.api(
            "PUT", "/api/v1/items/%d" % created["id"], {"title": "显式 null", "status": None, "priority": None}
        )
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["status"], "doing")
        self.assertEqual(envelope["data"]["priority"], "low")
        self.assertEqual(envelope["data"]["title"], "显式 null")

    def test_update_missing_title_is_title_required(self):
        created = self.create("缺字段")
        status, _, envelope = self.api("PUT", "/api/v1/items/%d" % created["id"], {"status": "done"})
        self.assert_error(status, envelope, "TITLE_REQUIRED", field="title")

    def test_delete_then_get_and_delete_again(self):
        created = self.create("待删除")
        status, _, envelope = self.api("DELETE", "/api/v1/items/%d" % created["id"])
        self.assertEqual(status, 200)
        self.assertEqual(envelope, {"ok": True, "data": {"id": created["id"], "deleted": True}})

        status, _, envelope = self.api("GET", "/api/v1/items/%d" % created["id"])
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)

        status, _, envelope = self.api("DELETE", "/api/v1/items/%d" % created["id"])
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)


class SearchFilterPagingTests(ServerTestCase):
    def test_keyword_search_rules(self):
        self.create("ABC 任务")
        self.create("买牛奶")
        self.create("100% 完成")
        self.create("100 完成")
        self.create("下_划线")

        _, _, envelope = self.api("GET", "/api/v1/items?q=abc")
        self.assertEqual([row["title"] for row in envelope["data"]["items"]], ["ABC 任务"])
        self.assertEqual(envelope["data"]["q"], "abc")

        _, _, envelope = self.api("GET", "/api/v1/items?q=%E4%B9%B0%E7%89%9B%E5%A5%B6")
        self.assertEqual([row["title"] for row in envelope["data"]["items"]], ["买牛奶"])

        _, _, envelope = self.api("GET", "/api/v1/items?q=100%25")
        self.assertEqual([row["title"] for row in envelope["data"]["items"]], ["100% 完成"])

        _, _, envelope = self.api("GET", "/api/v1/items?q=_")
        self.assertEqual([row["title"] for row in envelope["data"]["items"]], ["下_划线"])

        _, _, envelope = self.api("GET", "/api/v1/items?q=%E4%B8%8D%E5%AD%98%E5%9C%A8")
        self.assertEqual(envelope["data"]["items"], [])
        self.assertEqual(envelope["data"]["total"], 0)
        self.assertEqual(envelope["data"]["total_pages"], 1)

    def test_status_filter_and_and_combination(self):
        self.create("待办一", status="todo")
        self.create("进行一", status="doing")
        self.create("完成一", status="done")
        self.create("待办二", status="todo")

        for status_value, expected in (("todo", 2), ("doing", 1), ("done", 1)):
            _, _, envelope = self.api("GET", "/api/v1/items?status=" + status_value)
            self.assertEqual(envelope["data"]["total"], expected)
            self.assertEqual(envelope["data"]["status"], status_value)
            self.assertTrue(all(row["status"] == status_value for row in envelope["data"]["items"]))

        _, _, envelope = self.api("GET", "/api/v1/items?status=")
        self.assertIsNone(envelope["data"]["status"])
        self.assertEqual(envelope["data"]["total"], 4)

        _, _, envelope = self.api("GET", "/api/v1/items?q=%E5%BE%85%E5%8A%9E&status=todo")
        self.assertEqual(envelope["data"]["total"], 2)
        _, _, envelope = self.api("GET", "/api/v1/items?q=%E5%BE%85%E5%8A%9E&status=done")
        self.assertEqual(envelope["data"]["items"], [])

        status, _, envelope = self.api("GET", "/api/v1/items?status=pending")
        self.assert_error(status, envelope, "INVALID_STATUS", field="status")

    def test_pagination_boundaries(self):
        for index in range(25):
            self.create("分页数据 %02d" % index)

        _, _, envelope = self.api("GET", "/api/v1/items?page_size=10")
        data = envelope["data"]
        self.assertEqual(data["total"], 25)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(len(data["items"]), 10)

        _, _, envelope = self.api("GET", "/api/v1/items?page=3&page_size=10")
        self.assertEqual(len(envelope["data"]["items"]), 5)

        status, _, envelope = self.api("GET", "/api/v1/items?page=4&page_size=10")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["items"], [])
        self.assertEqual(envelope["data"]["total"], 25)

        _, _, envelope = self.api("GET", "/api/v1/items?page_size=1")
        self.assertEqual(envelope["data"]["total_pages"], 25)
        self.assertEqual(len(envelope["data"]["items"]), 1)

        status, _, envelope = self.api("GET", "/api/v1/items?page_size=100")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["page_size"], 100)

        ids = [row["id"] for row in self.api("GET", "/api/v1/items?page_size=100")[2]["data"]["items"]]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_invalid_pagination_params(self):
        for query, code, field in (
            ("page=0", "INVALID_PAGE", "page"),
            ("page=abc", "INVALID_PAGE", "page"),
            ("page=", "INVALID_PAGE", "page"),
            ("page=1.5", "INVALID_PAGE", "page"),
            ("page=-2", "INVALID_PAGE", "page"),
            ("page=%2B1", "INVALID_PAGE", "page"),
            ("page_size=0", "INVALID_PAGE_SIZE", "page_size"),
            ("page_size=101", "INVALID_PAGE_SIZE", "page_size"),
            ("page_size=abc", "INVALID_PAGE_SIZE", "page_size"),
            ("page_size=", "INVALID_PAGE_SIZE", "page_size"),
        ):
            status, _, envelope = self.api("GET", "/api/v1/items?" + query)
            self.assert_error(status, envelope, code, field=field, http_status=400)

    def test_unknown_query_params_are_ignored(self):
        self.create("忽略未知参数")
        status, _, envelope = self.api("GET", "/api/v1/items?foo=1&bar=2")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["total"], 1)


class TitleBoundaryTests(ServerTestCase):
    def test_blank_titles_are_rejected(self):
        for blank in ("", "   ", "\t\n", "\u3000", " \r\n "):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": blank})
            self.assert_error(status, envelope, "TITLE_REQUIRED", field="title")
        status, _, envelope = self.api("POST", "/api/v1/items", {"status": "todo"})
        self.assert_error(status, envelope, "TITLE_REQUIRED", field="title")
        _, _, listing = self.api("GET", "/api/v1/items")
        self.assertEqual(listing["data"]["total"], 0)

    def test_exactly_80_chars_after_strip_is_accepted(self):
        title = "字" * 80
        item = self.create("  " + title + "  ")
        self.assertEqual(item["title"], title)
        self.assertEqual(len(item["title"]), 80)

    def test_81_chars_are_rejected(self):
        for title in ("字" * 81, "a" * 81):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": title})
            self.assert_error(status, envelope, "TITLE_TOO_LONG", field="title")
            self.assertEqual(envelope["error"]["details"]["max_length"], 80)
            self.assertEqual(envelope["error"]["details"]["actual_length"], 81)

    def test_emoji_counts_as_code_points(self):
        self.create("\U0001f600" * 80)
        status, _, envelope = self.api("POST", "/api/v1/items", {"title": "\U0001f600" * 81})
        self.assert_error(status, envelope, "TITLE_TOO_LONG", field="title")


class EnumValidationTests(ServerTestCase):
    def test_invalid_status_values(self):
        """字符串但不在枚举内 → INVALID_STATUS（大小写敏感，含尾随空白）。"""
        for value in ("TODO", "pending", "", "done ", "todo\n"):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": "状态非法", "status": value})
            self.assert_error(status, envelope, "INVALID_STATUS", field="status")
            self.assertEqual(envelope["error"]["details"]["allowed"], ["todo", "doing", "done"])
        item = self.create("null 走默认值", status=None)
        self.assertEqual(item["status"], "todo")

    def test_invalid_priority_values(self):
        for value in ("urgent", "MEDIUM", "", "high5"):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": "优先级非法", "priority": value})
            self.assert_error(status, envelope, "INVALID_PRIORITY", field="priority")
            self.assertEqual(envelope["error"]["details"]["allowed"], ["low", "medium", "high"])
        item = self.create("null 走默认值", priority=None)
        self.assertEqual(item["priority"], "medium")

    def test_non_string_status_is_invalid_body(self):
        """契约 6.2 类型行 + 4.4 校验顺序②：非字符串 status 先判类型 → INVALID_BODY。"""
        for value in (123, 1, True, [], {}, 1.5):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": "状态类型非法", "status": value})
            self.assert_error(status, envelope, "INVALID_BODY", field="status")
        _, _, listing = self.api("GET", "/api/v1/items")
        self.assertEqual(listing["data"]["total"], 0, "非法值不得入库")

    def test_non_string_priority_is_invalid_body(self):
        for value in (2, True, [], {"level": 1}, 1.5):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": "优先级类型非法", "priority": value})
            self.assert_error(status, envelope, "INVALID_BODY", field="priority")
        _, _, listing = self.api("GET", "/api/v1/items")
        self.assertEqual(listing["data"]["total"], 0, "非法值不得入库")

    def test_non_string_enum_on_put_is_invalid_body_and_keeps_record(self):
        item = self.create("PUT 类型非法", status="todo", priority="low")
        for field_name, value in (
            ("status", 123),
            ("status", True),
            ("status", []),
            ("priority", 2),
            ("priority", {"level": 1}),
        ):
            payload = {"title": "PUT 类型非法", field_name: value}
            status, _, envelope = self.api("PUT", "/api/v1/items/%d" % item["id"], payload)
            self.assert_error(status, envelope, "INVALID_BODY", field=field_name)
        _, _, current = self.api("GET", "/api/v1/items/%d" % item["id"])
        self.assertEqual(current["data"]["status"], "todo", "非法 PUT 不得改动原值")
        self.assertEqual(current["data"]["priority"], "low")

    def test_non_string_enum_on_missing_record_is_still_404(self):
        """D6 顺序不变：存在性先于字段校验，非字符串枚举也不例外。"""
        status, _, envelope = self.api("PUT", "/api/v1/items/999999", {"title": "存在性优先", "status": 123})
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)

    def test_status_is_checked_before_priority(self):
        status, _, envelope = self.api(
            "POST", "/api/v1/items", {"title": "顺序", "status": "nope", "priority": "nope"}
        )
        self.assert_error(status, envelope, "INVALID_STATUS", field="status")

    def test_title_error_precedes_enum_error(self):
        status, _, envelope = self.api("POST", "/api/v1/items", {"title": "", "status": "nope"})
        self.assert_error(status, envelope, "TITLE_REQUIRED", field="title")
        status, _, envelope = self.api("POST", "/api/v1/items", {"title": "字" * 81, "status": "nope"})
        self.assert_error(status, envelope, "TITLE_TOO_LONG", field="title")


class BodyAndIdTests(ServerTestCase):
    def test_invalid_body_shapes(self):
        for raw in (b"", b"{", b"[1,", b"[1,2]", b'"text"', b"123", b"null"):
            status, _, envelope = self.api("POST", "/api/v1/items", raw=raw)
            self.assert_error(status, envelope, "INVALID_BODY")

    def test_title_null_or_non_string_is_invalid_body(self):
        for title in (None, 123, True, ["a"], {"a": 1}):
            status, _, envelope = self.api("POST", "/api/v1/items", {"title": title})
            self.assert_error(status, envelope, "INVALID_BODY", field="title")

    def test_body_over_1mib_is_413(self):
        oversize = b'{"title":"' + b"a" * (MAX_BODY_BYTES + 16) + b'"}'
        status, _, envelope = self.api("POST", "/api/v1/items", raw=oversize)
        self.assert_error(status, envelope, "PAYLOAD_TOO_LARGE", http_status=413)

    def test_body_exactly_1mib_is_accepted(self):
        prefix = b'{"title":"ok","pad":"'
        suffix = b'"}'
        payload = prefix + b"b" * (MAX_BODY_BYTES - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(payload), MAX_BODY_BYTES)
        status, _, envelope = self.api("POST", "/api/v1/items", raw=payload)
        self.assertEqual(status, 201, envelope)
        self.assertEqual(envelope["data"]["title"], "ok")

    def test_invalid_path_ids(self):
        for raw_id in ("abc", "0", "-1", "1.5", "1a", ""):
            for method in ("GET", "DELETE"):
                status, _, envelope = self.api(method, "/api/v1/items/" + raw_id)
                self.assert_error(status, envelope, "INVALID_ID", field="id")
            status, _, envelope = self.api("PUT", "/api/v1/items/" + raw_id, {"title": "x"})
            self.assert_error(status, envelope, "INVALID_ID", field="id")

    def test_missing_records_are_404(self):
        status, _, envelope = self.api("GET", "/api/v1/items/999999")
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)
        status, _, envelope = self.api("DELETE", "/api/v1/items/999999")
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)

    def test_put_on_missing_record_prefers_404_over_field_errors(self):
        status, _, envelope = self.api("PUT", "/api/v1/items/999999", {"title": ""})
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)
        status, _, envelope = self.api("PUT", "/api/v1/items/999999", {"title": "字" * 81})
        self.assert_error(status, envelope, "ITEM_NOT_FOUND", field="id", http_status=404)

    def test_put_null_title_on_missing_record_is_invalid_body(self):
        status, _, envelope = self.api("PUT", "/api/v1/items/999999", {"title": None})
        self.assert_error(status, envelope, "INVALID_BODY", field="title")


class RoutingAndStaticTests(ServerTestCase):
    def test_unknown_api_path_is_json_404(self):
        status, headers, envelope = self.api("GET", "/api/v1/unknown")
        self.assert_error(status, envelope, "NOT_FOUND", http_status=404)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))

    def test_method_not_allowed_has_allow_header(self):
        created = self.create("方法不允许")
        cases = (
            ("POST", "/api/v1/items/%d" % created["id"], "GET, PUT, DELETE"),
            ("PUT", "/api/v1/items", "GET, POST"),
            ("DELETE", "/api/v1/items", "GET, POST"),
            ("PATCH", "/api/v1/items/%d" % created["id"], "GET, PUT, DELETE"),
            ("DELETE", "/api/v1/health", "GET"),
        )
        for method, path, allow in cases:
            status, headers, envelope = self.api(method, path, payload={"title": "x"} if method in ("POST", "PUT", "PATCH") else MISSING)
            self.assert_error(status, envelope, "METHOD_NOT_ALLOWED", http_status=405)
            self.assertEqual(headers.get("Allow"), allow)

    def test_static_files_are_served(self):
        for path, content_type, marker in (
            ("/", "text/html", "事项管理"),
            ("/index.html", "text/html", "id=\"item-list\""),
            ("/styles.css", "text/css", ".item-row"),
            ("/app.js", "application/javascript", "textContent"),
        ):
            status, headers, body = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(content_type), path)
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertIn(marker, body.decode("utf-8"))

    def test_paths_outside_whitelist_are_404(self):
        for path in ("/favicon.ico", "/web/index.html", "/../../etc/passwd", "/index.html%2e%2e", "/etc/passwd"):
            status, _, body = self.call("GET", path)
            self.assertEqual(status, 404, path)
            envelope = json.loads(body.decode("utf-8"))
            self.assertIs(envelope["ok"], False)
            self.assertEqual(envelope["error"]["code"], "NOT_FOUND")

    def test_error_bodies_never_leak_internals(self):
        responses = [
            self.call("GET", "/api/v1/unknown")[2],
            self.call("POST", "/api/v1/items", raw=b"{")[2],
            self.call("GET", "/api/v1/items/abc")[2],
            self.call("GET", "/api/v1/items?page=0")[2],
            self.call("DELETE", "/api/v1/items/999999")[2],
            self.call("GET", "/../../etc/passwd")[2],
        ]
        for body in responses:
            text = body.decode("utf-8")
            for forbidden in ("Traceback", "sqlite", "SELECT", self.temp_dir, "app/", ".py"):
                self.assertNotIn(forbidden, text, text)


class PersistenceTests(ServerTestCase):
    def test_data_survives_restart_in_single_db_file(self):
        first = self.create("重启保留一", status="doing", priority="high")
        second = self.create("重启保留二")
        self._stop_server()

        files_before = sorted(os.listdir(self.temp_dir))
        self.assertEqual(files_before, ["issues.db"], "运行期数据目录只应有单个 .db 文件")

        self.server = self._start_server()
        status, _, envelope = self.api("GET", "/api/v1/items")
        self.assertEqual(status, 200)
        items = envelope["data"]["items"]
        self.assertEqual(envelope["data"]["total"], 2)
        self.assertEqual([row["id"] for row in items], [second["id"], first["id"]])
        self.assertEqual(items[1], first)
        self.assertEqual(items[0], second)

        status, _, envelope = self.api("GET", "/api/v1/items/%d" % first["id"])
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"], first)

    def test_update_and_delete_are_committed_before_response(self):
        created = self.create("提交可见")
        self.api("PUT", "/api/v1/items/%d" % created["id"], {"title": "已改名", "status": "done"})
        self.api("DELETE", "/api/v1/items/%d" % created["id"])
        self._stop_server()

        self.server = self._start_server()
        _, _, envelope = self.api("GET", "/api/v1/items")
        self.assertEqual(envelope["data"]["items"], [])
        self.assertEqual(envelope["data"]["total"], 0)

        kept = self.create("删除后仍在")
        self._stop_server()
        self.server = self._start_server()
        _, _, envelope = self.api("GET", "/api/v1/items")
        self.assertEqual([row["id"] for row in envelope["data"]["items"]], [kept["id"]])


if __name__ == "__main__":
    unittest.main()
