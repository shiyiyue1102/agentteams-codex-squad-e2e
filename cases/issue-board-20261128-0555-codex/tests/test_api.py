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
        for title in ("<img src=x onerror=alert(1)>", "<script>alert(1)</script>", '"><svg/onload=alert(1)>'):
            item = self.create(title=title)
            self.assertEqual(item["title"], title)
            fetched = self.call("GET", "/api/items/{0}".format(item["id"]))
            self.assertEqual(fetched.status, 200)
            self.assertEqual(fetched.json()["title"], title)
