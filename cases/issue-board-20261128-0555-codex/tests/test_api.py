"""开发自测：临时目录文件库 + 真实 HTTP 请求（分析报告 §8 第 6 条）。

运行方式::

    cd cases/issue-board-20261128-0555-codex
    python3 -m unittest discover -s tests -t . -v

测试只使用 ``tempfile.mkdtemp()`` 创建的目录，结束时只删除自己创建的精确路径。
"""

import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote as urllib_quote
from http.server import ThreadingHTTPServer

from issueboard import api
from issueboard.db import Database


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class ServerFixture:
    """在临时目录中以文件库启动真实服务，可停止后用同一库重启。"""

    def __init__(self, db_path=None):
        self.temp_dir = tempfile.mkdtemp(prefix="issue-board-selftest-")
        self.db_path = db_path or os.path.join(self.temp_dir, "items.sqlite3")
        self.httpd = None
        self.thread = None

    @property
    def base_url(self):
        return "http://127.0.0.1:{0}".format(self.httpd.server_address[1])

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        database = Database(self.db_path)
        database.initialize()
        handler = api.make_handler(database)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=10)
            self.thread = None

    def close(self):
        self.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def http_call(base_url, method, path, body=None, raw_body=None):
    data = raw_body
    if data is None and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base_url + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, dict(error.headers), error.read())


def raw_http_call(port, request_line, body=None):
    """用原始 socket 发送请求行，避免客户端对 ../ 做规范化。"""
    head = "{0}\r\nHost: 127.0.0.1:{1}\r\n".format(request_line, port)
    if body is not None:
        head += "Content-Length: {0}\r\n".format(len(body))
    head += "Connection: close\r\n\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(head.encode("utf-8"))
        if body is not None:
            sock.sendall(body)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "replace")


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture = ServerFixture().start()
        self.base_url = self.fixture.base_url
        self.port = self.fixture.port

    def tearDown(self):
        self.fixture.close()

    def call(self, method, path, body=None, raw_body=None):
        return http_call(self.base_url, method, path, body=body, raw_body=raw_body)

    def create(self, title="买牛奶", status="todo", priority="medium"):
        response = self.call(
            "POST", "/api/items",
            {"title": title, "status": status, "priority": priority},
        )
        self.assertEqual(response.status, 201, response.text)
        return response.json()

    def assert_error(self, response, status, code, field=None):
        self.assertEqual(response.status, status, response.text)
        body = response.json()
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertEqual(body["error"]["field"], field)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertIsInstance(body["error"]["details"], dict)
        return body["error"]

    def total(self):
        return self.call("GET", "/api/items?page_size=100").json()["total"]


class TestContractAndSchema(ApiTestCase):
    def test_meta_matches_contract(self):
        response = self.call("GET", "/api/meta")
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.assertEqual(
            payload["statuses"],
            [
                {"value": "todo", "label": "待办"},
                {"value": "in_progress", "label": "进行中"},
                {"value": "done", "label": "完成"},
            ],
        )
        self.assertEqual(
            payload["priorities"],
            [
                {"value": "low", "label": "低"},
                {"value": "medium", "label": "中"},
                {"value": "high", "label": "高"},
            ],
        )
        self.assertEqual(payload["defaults"], {"status": "todo", "priority": "medium"})
        self.assertEqual(payload["title_max_length"], 80)
        self.assertEqual(payload["page_size"], {"min": 1, "max": 100, "default": 10})

    def test_schema_and_indexes_created(self):
        with sqlite3.connect(self.fixture.db_path) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                )
            }
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
            ).fetchone()[0]
        for expected in ("items", "idx_items_status", "idx_items_created_id"):
            self.assertIn(expected, names)
        self.assertIn("AUTOINCREMENT", sql)
        self.assertIn("CHECK (length(title) BETWEEN 1 AND 80)", sql)

    def test_index_page_served_without_external_resources(self):
        response = self.call("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn('name="viewport"', response.text)
        self.assertNotIn("http://", response.text)
        self.assertNotIn("https://", response.text)
        for path in ("/styles.css", "/app.js"):
            asset = self.call("GET", path)
            self.assertEqual(asset.status, 200, path)
            self.assertNotIn("http://", asset.text)
            self.assertNotIn("https://", asset.text)


class TestCreateItem(ApiTestCase):
    def test_create_returns_201_location_and_full_item(self):
        response = self.call(
            "POST", "/api/items",
            {"title": "写周报", "status": "in_progress", "priority": "high"},
        )
        self.assertEqual(response.status, 201)
        self.assertIn("charset=utf-8", response.headers["Content-Type"])
        self.assertEqual(response.headers["Location"], "/api/items/{0}".format(response.json()["id"]))
        item = response.json()
        self.assertEqual(sorted(item), ["created_at", "id", "priority", "status", "title", "updated_at"])
        self.assertIsInstance(item["id"], int)
        self.assertGreater(item["id"], 0)
        self.assertEqual(item["title"], "写周报")
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["created_at"], item["updated_at"])
        self.assertRegex(item["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_title_is_stripped_and_stored_raw(self):
        item = self.create(title="  买牛奶  ")
        self.assertEqual(item["title"], "买牛奶")
        with sqlite3.connect(self.fixture.db_path) as connection:
            stored = connection.execute(
                "SELECT title FROM items WHERE id = ?", (item["id"],)
            ).fetchone()[0]
        self.assertEqual(stored, "买牛奶")

    def test_internal_whitespace_preserved(self):
        item = self.create(title="  买 牛奶 \t ")
        self.assertEqual(item["title"], "买 牛奶")

    def test_title_length_boundary_80_ok_and_81_rejected(self):
        ok = self.create(title="字" * 80)
        self.assertEqual(len(ok["title"]), 80)
        response = self.call(
            "POST", "/api/items",
            {"title": "字" * 81, "status": "todo", "priority": "low"},
        )
        error = self.assert_error(response, 400, "TITLE_TOO_LONG", "title")
        self.assertEqual(error["details"]["max_length"], 80)
        self.assertEqual(error["details"]["length"], 81)

    def test_title_required_variants(self):
        for title in ("", "   ", "\t\n", None, 123, ["a"], {"a": 1}, True):
            payload = {"status": "todo", "priority": "low", "title": title}
            response = self.call("POST", "/api/items", payload)
            self.assert_error(response, 400, "TITLE_REQUIRED", "title")
        missing = {"status": "todo", "priority": "low"}
        self.assert_error(self.call("POST", "/api/items", missing), 400, "TITLE_REQUIRED", "title")

    def test_invalid_status_variants(self):
        for status in ("finished", "TODO", "Todo", "待办", "all", "", 1, None):
            response = self.call(
                "POST", "/api/items",
                {"title": "标题", "status": status, "priority": "low"},
            )
            self.assert_error(response, 400, "INVALID_STATUS", "status")
        payload = {"title": "标题", "priority": "low"}
        self.assert_error(self.call("POST", "/api/items", payload), 400, "INVALID_STATUS", "status")

    def test_invalid_priority_variants(self):
        for priority in ("urgent", "HIGH", "High", "高", "", 3, None):
            response = self.call(
                "POST", "/api/items",
                {"title": "标题", "status": "todo", "priority": priority},
            )
            self.assert_error(response, 400, "INVALID_PRIORITY", "priority")
        payload = {"title": "标题", "status": "todo"}
        self.assert_error(self.call("POST", "/api/items", payload), 400, "INVALID_PRIORITY", "priority")

    def test_invalid_json_variants(self):
        for raw in (b"[1,2]", b'"x"', b"{bad json", b"", b"null", b"\xff\xfe\x00bad"):
            response = self.call("POST", "/api/items", raw_body=raw)
            self.assert_error(response, 400, "INVALID_JSON", None)

    def test_unknown_field_rejected(self):
        response = self.call(
            "POST", "/api/items",
            {"title": "a", "status": "todo", "priority": "low", "owner": "x"},
        )
        error = self.assert_error(response, 400, "INVALID_FIELD", None)
        self.assertEqual(error["details"]["key"], "owner")

    def test_validation_failures_do_not_write(self):
        self.assertEqual(self.total(), 0)
        self.call("POST", "/api/items", raw_body=b"{bad json")
        self.call("POST", "/api/items", {"title": "  ", "status": "todo", "priority": "low"})
        self.call("POST", "/api/items", {"title": "a", "status": "nope", "priority": "low"})
        self.call("POST", "/api/items", {"title": "a", "status": "todo", "priority": "nope"})
        self.call("POST", "/api/items", {"title": "字" * 81, "status": "todo", "priority": "low"})
        self.call("POST", "/api/items", {"title": "a", "status": "todo", "priority": "low", "x": 1})
        self.assertEqual(self.total(), 0)
        with sqlite3.connect(self.fixture.db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self.assertEqual(count, 0)

    def test_payload_too_large(self):
        raw = b'{"title":"' + b"a" * (70 * 1024) + b'","status":"todo","priority":"low"}'
        response = self.call("POST", "/api/items", raw_body=raw)
        self.assert_error(response, 413, "PAYLOAD_TOO_LARGE", None)

    def test_html_title_is_legal_and_returned_raw(self):
        for title in ("<img src=x onerror=alert(1)>", "<script>alert(1)</script>", '\"><svg/onload=alert(1)>'):
            item = self.create(title=title)
            self.assertEqual(item["title"], title)
            fetched = self.call("GET", "/api/items/{0}".format(item["id"]))
            self.assertEqual(fetched.status, 200)
            self.assertEqual(fetched.json()["title"], title)


class TestReadUpdateDelete(ApiTestCase):
    def test_get_single_item(self):
        created = self.create(title="待读取")
        response = self.call("GET", "/api/items/{0}".format(created["id"]))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), created)

    def test_update_refreshes_updated_at_and_keeps_id_and_created_at(self):
        created = self.create(title="旧标题", status="todo", priority="low")
        response = self.call(
            "PUT", "/api/items/{0}".format(created["id"]),
            {"title": "  新标题  ", "status": "done", "priority": "high"},
        )
        self.assertEqual(response.status, 200)
        updated = response.json()
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["priority"], "high")
        self.assertGreaterEqual(updated["updated_at"], created["updated_at"])

    def test_update_validation_order(self):
        created = self.create()
        url = "/api/items/{0}".format(created["id"])
        self.assert_error(
            self.call("PUT", url, {"title": "x", "priority": "low"}),
            400, "INVALID_STATUS", "status",
        )
        self.assert_error(
            self.call("PUT", url, {"title": "x", "status": "todo"}),
            400, "INVALID_PRIORITY", "priority",
        )
        self.assert_error(
            self.call("PUT", url, {"title": "", "status": "todo", "priority": "low"}),
            400, "TITLE_REQUIRED", "title",
        )
        self.assert_error(
            self.call("PUT", url, {"title": "字" * 81, "status": "todo", "priority": "low"}),
            400, "TITLE_TOO_LONG", "title",
        )
        self.assert_error(self.call("PUT", url, raw_body=b"{oops"), 400, "INVALID_JSON", None)
        self.assertEqual(self.call("GET", url).json()["title"], "买牛奶")

    def test_format_error_before_existence_check(self):
        response = self.call(
            "PUT", "/api/items/999999",
            {"title": "", "status": "nope", "priority": "nope"},
        )
        self.assert_error(response, 400, "TITLE_REQUIRED", "title")
        ok = self.call(
            "PUT", "/api/items/999999",
            {"title": "x", "status": "todo", "priority": "low"},
        )
        self.assert_error(ok, 404, "ITEM_NOT_FOUND", "id")

    def test_not_found_for_missing_records(self):
        for method in ("GET", "PUT", "DELETE"):
            body = {"title": "x", "status": "todo", "priority": "low"} if method == "PUT" else None
            response = self.call(method, "/api/items/999999", body=body)
            self.assert_error(response, 404, "ITEM_NOT_FOUND", "id")

    def test_invalid_id_variants(self):
        for raw in ("abc", "0", "-1", "1.5", "1e2", "+1", "01x"):
            self.assert_error(self.call("GET", "/api/items/" + raw), 400, "INVALID_ID", "id")
        self.assert_error(self.call("GET", "/api/items/1/2"), 404, "NOT_FOUND", None)

    def test_percent_encoded_id_variants(self):
        # urllib 会把裸空格折进请求行，这里用原始 socket 验证百分号编码的路径
        for encoded, status, code in (
            ("%20", "400", "INVALID_ID"),
            ("1%2F2", "404", "NOT_FOUND"),
            ("%2e%2e%2fserver.py", "404", "NOT_FOUND"),
            # %2f 解码后成为多余分段，属未定义路径，不得被吞并为集合路径
            ("%2f", "404", "NOT_FOUND"),
        ):
            raw = raw_http_call(self.port, "GET /api/items/{0} HTTP/1.1".format(encoded))
            self.assertIn(status, raw.split("\r\n")[0], encoded)
            self.assertIn(code, raw, encoded)
            self.assertNotIn("Traceback", raw, encoded)
            self.assertNotIn('"items"', raw, encoded)

    def test_delete_then_delete_again_is_404(self):
        created = self.create()
        first = self.call("DELETE", "/api/items/{0}".format(created["id"]))
        self.assertEqual(first.status, 200)
        self.assertEqual(first.json(), {"ok": True, "id": created["id"]})
        second = self.call("DELETE", "/api/items/{0}".format(created["id"]))
        self.assert_error(second, 404, "ITEM_NOT_FOUND", "id")
        self.assertEqual(self.total(), 0)

    def test_method_not_allowed_has_allow_header(self):
        response = self.call("DELETE", "/api/items")
        self.assert_error(response, 405, "METHOD_NOT_ALLOWED", None)
        self.assertIn("GET", response.headers.get("Allow", ""))
        self.assertIn("POST", response.headers.get("Allow", ""))

        created = self.create()
        response = self.call("POST", "/api/items/{0}".format(created["id"]), {"title": "x"})
        self.assert_error(response, 405, "METHOD_NOT_ALLOWED", None)
        allow = response.headers.get("Allow", "")
        for method in ("GET", "PUT", "DELETE"):
            self.assertIn(method, allow)

        raw = raw_http_call(self.port, "PATCH /api/items HTTP/1.1")
        self.assertIn("405", raw.split("\r\n")[0])
        self.assertIn("METHOD_NOT_ALLOWED", raw)

    def test_empty_id_path_is_invalid_id_and_never_writes(self):
        """``/api/items/``（空 id）按 ``{id}`` 路由处理，POST 为 405 且零写入。"""
        payload = {"title": "空 id 写入", "status": "todo", "priority": "low"}
        for method in ("GET", "PUT", "DELETE"):
            body = payload if method == "PUT" else None
            response = self.call(method, "/api/items/", body=body)
            self.assert_error(response, 400, "INVALID_ID", "id")
            self.assertNotIn("items", response.json())

        response = self.call("POST", "/api/items/", payload)
        self.assert_error(response, 405, "METHOD_NOT_ALLOWED", None)
        allow = response.headers.get("Allow", "")
        for method in ("GET", "PUT", "DELETE"):
            self.assertIn(method, allow)
        self.assertNotIn("POST", allow)
        self.assertEqual(self.total(), 0)

        raw = raw_http_call(self.port, "GET /api/items/ HTTP/1.1")
        self.assertIn("400", raw.split("\r\n")[0])
        self.assertIn("INVALID_ID", raw)
        self.assertNotIn('"items"', raw)

    def test_trailing_slash_api_paths_are_not_found(self):
        """带尾斜杠或多余分段的 API 路径固定返回 404 NOT_FOUND。"""
        created = self.create()
        item_id = created["id"]
        payload = {"title": "尾斜杠更新", "status": "done", "priority": "high"}
        for method in ("GET", "PUT", "DELETE"):
            body = payload if method == "PUT" else None
            response = self.call(method, "/api/items/{0}/".format(item_id), body=body)
            self.assert_error(response, 404, "NOT_FOUND", None)
        # 尾斜杠路由既不返回也不修改或删除精确路径的记录
        again = self.call("GET", "/api/items/{0}".format(item_id))
        self.assertEqual(again.status, 200)
        self.assertEqual(again.json()["title"], "买牛奶")
        self.assertEqual(self.total(), 1)

        self.assert_error(self.call("GET", "/api/meta/"), 404, "NOT_FOUND", None)
        self.assertEqual(self.call("GET", "/api/meta").status, 200)
        self.assertEqual(self.call("GET", "/api/items").status, 200)
        for request_line in ("GET /api/items//1 HTTP/1.1", "GET /api/items/%2f HTTP/1.1"):
            raw = raw_http_call(self.port, request_line)
            self.assertIn("404", raw.split("\r\n")[0], request_line)
            self.assertIn("NOT_FOUND", raw, request_line)
            self.assertNotIn('"items"', raw, request_line)


class TestSearchFilterPaging(ApiTestCase):
    def seed(self):
        self.create(title="Buy milk", status="todo", priority="low")
        self.create(title="买牛奶", status="todo", priority="high")
        self.create(title="写周报", status="in_progress", priority="medium")
        self.create(title="100% done_task", status="done", priority="low")

    def test_search_substring_and_case_insensitive(self):
        self.seed()
        response = self.call("GET", "/api/items?q=milk")
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "Buy milk")
        self.assertEqual(payload["filters"], {"q": "milk", "status": "all"})
        chinese = self.call("GET", "/api/items?q=" + urllib_quote("牛奶")).json()
        self.assertEqual(chinese["total"], 1)
        self.assertEqual(chinese["items"][0]["title"], "买牛奶")

    def test_search_escapes_like_wildcards(self):
        self.seed()
        percent = self.call("GET", "/api/items?q=%25").json()
        self.assertEqual(percent["total"], 1)
        self.assertEqual(percent["items"][0]["title"], "100% done_task")
        underscore = self.call("GET", "/api/items?q=_").json()
        self.assertEqual(underscore["total"], 1)
        self.assertEqual(underscore["items"][0]["title"], "100% done_task")
        backslash = self.call("GET", "/api/items?q=" + urllib_quote("\\")).json()
        self.assertEqual(backslash["total"], 0)

    def test_search_query_is_trimmed(self):
        self.seed()
        response = self.call("GET", "/api/items?q=" + urllib_quote("  milk  ")).json()
        self.assertEqual(response["filters"]["q"], "milk")
        self.assertEqual(response["total"], 1)

    def test_status_filter_and_combined_and(self):
        self.seed()
        for status, expected in (("todo", 2), ("in_progress", 1), ("done", 1)):
            payload = self.call("GET", "/api/items?status=" + status).json()
            self.assertEqual(payload["total"], expected, status)
            self.assertEqual(payload["filters"]["status"], status)
            for item in payload["items"]:
                self.assertEqual(item["status"], status)
        self.assertEqual(self.call("GET", "/api/items?status=all").json()["total"], 4)
        self.assertEqual(self.call("GET", "/api/items").json()["total"], 4)
        self.assertEqual(self.call("GET", "/api/items?status=").json()["total"], 4)
        combined = self.call("GET", "/api/items?status=todo&q=milk").json()
        self.assertEqual(combined["total"], 1)
        self.assertEqual(combined["items"][0]["title"], "Buy milk")
        empty = self.call("GET", "/api/items?status=done&q=milk").json()
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["items"], [])
        self.assert_error(self.call("GET", "/api/items?status=finished"), 400, "INVALID_STATUS", "status")

    def test_pagination_fields(self):
        for index in range(23):
            self.create(title="事项 {0}".format(index))
        first = self.call("GET", "/api/items?page=1&page_size=10").json()
        self.assertEqual(first["total"], 23)
        self.assertEqual(first["total_pages"], 3)
        self.assertEqual(len(first["items"]), 10)
        self.assertFalse(first["has_prev"])
        self.assertTrue(first["has_next"])
        last = self.call("GET", "/api/items?page=3&page_size=10").json()
        self.assertEqual(len(last["items"]), 3)
        self.assertTrue(last["has_prev"])
        self.assertFalse(last["has_next"])
        self.assertEqual(last["total_pages"], 3)

    def test_pagination_invalid_values(self):
        self.assert_error(self.call("GET", "/api/items?page=0"), 400, "INVALID_PAGE", "page")
        self.assert_error(self.call("GET", "/api/items?page=abc"), 400, "INVALID_PAGE", "page")
        self.assert_error(self.call("GET", "/api/items?page=-1"), 400, "INVALID_PAGE", "page")
        self.assert_error(self.call("GET", "/api/items?page=1.5"), 400, "INVALID_PAGE", "page")
        self.assert_error(self.call("GET", "/api/items?page="), 400, "INVALID_PAGE", "page")
        self.assert_error(self.call("GET", "/api/items?page_size=0"), 400, "INVALID_PAGE_SIZE", "page_size")
        self.assert_error(self.call("GET", "/api/items?page_size=101"), 400, "INVALID_PAGE_SIZE", "page_size")
        self.assert_error(self.call("GET", "/api/items?page_size=abc"), 400, "INVALID_PAGE_SIZE", "page_size")
        self.assertEqual(self.call("GET", "/api/items?page_size=100").status, 200)

    def test_page_beyond_last_is_empty_but_valid(self):
        self.create()
        response = self.call("GET", "/api/items?page=99&page_size=10")
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["total_pages"], 1)
        self.assertTrue(payload["has_prev"])
        self.assertFalse(payload["has_next"])

    def test_empty_collection_shape(self):
        payload = self.call("GET", "/api/items").json()
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["total_pages"], 1)
        self.assertFalse(payload["has_prev"])
        self.assertFalse(payload["has_next"])

    def test_default_order_and_full_coverage_across_pages(self):
        for index in range(25):
            self.create(title="事项 {0}".format(index))
        seen = []
        for page in (1, 2, 3):
            payload = self.call("GET", "/api/items?page={0}&page_size=10".format(page)).json()
            seen.extend(item["id"] for item in payload["items"])
        self.assertEqual(len(seen), 25)
        self.assertEqual(len(set(seen)), 25)
        self.assertEqual(seen, sorted(seen, reverse=True))


class TestPersistenceAndStatic(ApiTestCase):
    def test_data_survives_service_restart(self):
        first = self.create(title="重启前", status="in_progress", priority="high")
        second = self.create(title="重启前 2")
        db_path = self.fixture.db_path
        self.fixture.stop()

        restarted = ServerFixture(db_path=db_path).start()
        try:
            payload = http_call(restarted.base_url, "GET", "/api/items?page_size=100").json()
            self.assertEqual(payload["total"], 2)
            by_id = {item["id"]: item for item in payload["items"]}
            self.assertEqual(by_id[first["id"]], first)
            self.assertEqual(by_id[second["id"]], second)
            single = http_call(restarted.base_url, "GET", "/api/items/{0}".format(first["id"]))
            self.assertEqual(single.status, 200)
            self.assertEqual(single.json()["created_at"], first["created_at"])
        finally:
            restarted.close()

    def test_fresh_temp_db_starts_empty(self):
        other_dir = tempfile.mkdtemp(prefix="issue-board-selftest-fresh-")
        try:
            other = ServerFixture(db_path=os.path.join(other_dir, "items.sqlite3")).start()
            try:
                payload = http_call(other.base_url, "GET", "/api/items").json()
                self.assertEqual(payload["total"], 0)
                self.assertEqual(payload["items"], [])
            finally:
                other.close()
        finally:
            shutil.rmtree(other_dir, ignore_errors=True)

    def test_write_is_committed_before_shutdown(self):
        created = self.create(title="已提交")
        with sqlite3.connect(self.fixture.db_path) as connection:
            row = connection.execute(
                "SELECT title, status, priority FROM items WHERE id = ?", (created["id"],)
            ).fetchone()
        self.assertEqual(row, ("已提交", "todo", "medium"))

    def test_unknown_api_path_returns_json_404(self):
        response = self.call("GET", "/api/unknown")
        self.assert_error(response, 404, "NOT_FOUND", None)
        self.assertIn("application/json", response.headers["Content-Type"])
        self.assert_error(self.call("GET", "/api/items/1/2"), 404, "NOT_FOUND", None)

    def test_unknown_static_path_returns_plain_text_404(self):
        response = self.call("GET", "/no-such-page")
        self.assertEqual(response.status, 404)
        self.assertIn("text/plain", response.headers["Content-Type"])
        self.assertEqual(response.text.strip(), "404 Not Found")

    def test_static_traversal_is_blocked(self):
        for request_line in (
            "GET /../server.py HTTP/1.1",
            "GET /..%2fserver.py HTTP/1.1",
            "GET /static/../../server.py HTTP/1.1",
            "GET /issueboard/db.py HTTP/1.1",
        ):
            raw = raw_http_call(self.port, request_line)
            self.assertIn("404", raw.split("\r\n")[0], request_line)
            self.assertNotIn("argparse", raw, request_line)
            self.assertNotIn("import sqlite3", raw, request_line)

    def test_utf8_roundtrip_and_charset_header(self):
        title = "中文标题 · émoji 🎉 <b>粗体</b>"
        created = self.create(title=title)
        self.assertEqual(created["title"], title)
        response = self.call("GET", "/api/items/{0}".format(created["id"]))
        self.assertIn("charset=utf-8", response.headers["Content-Type"])
        self.assertEqual(response.json()["title"], title)
        self.assertIn(title, response.text)

    def test_internal_error_is_json_and_leaks_nothing(self):
        def boom():
            raise RuntimeError("boom /secret/path")

        original = api.service.meta_payload
        api.service.meta_payload = boom
        try:
            response = self.call("GET", "/api/meta")
        finally:
            api.service.meta_payload = original
        error = self.assert_error(response, 500, "INTERNAL_ERROR", None)
        self.assertNotIn("/secret/path", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("boom", response.text)
        self.assertEqual(error["details"], {})

    def test_error_bodies_are_always_json(self):
        cases = (
            ("GET", "/api/unknown", None, 404),
            ("DELETE", "/api/items", None, 405),
            ("POST", "/api/items", {"title": "", "status": "todo", "priority": "low"}, 400),
            ("GET", "/api/items/abc", None, 400),
            ("GET", "/api/items/999999", None, 404),
            ("GET", "/api/items/", None, 400),
            ("POST", "/api/items/", {"title": "x", "status": "todo", "priority": "low"}, 405),
            ("GET", "/api/items/1/", None, 404),
            ("GET", "/api/meta/", None, 404),
        )
        for method, path, body, status in cases:
            response = self.call(method, path, body=body)
            self.assertEqual(response.status, status, path)
            self.assertIn("application/json", response.headers["Content-Type"], path)
            self.assertIn("error", response.json(), path)


if __name__ == "__main__":
    unittest.main()
