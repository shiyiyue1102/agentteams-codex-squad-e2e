"""事项管理应用 API 开发自测。

仅使用 Python 标准库（unittest + urllib + tempfile）；进程内在子线程启动
ThreadingHTTPServer 于临时端口 + 临时数据库，不在仓库目录残留任何文件。

运行方式（在 case 根目录 cases/issue-board-20261128-005233-qwen/ 下）：
    python3 -B -m unittest discover -s tests -v

覆盖范围（与分析报告第 6 节一致）：
创建成功与默认值、title 必填/超长/trim、非法 status/priority、非法 JSON、
404、invalid_id、PUT 部分更新与 empty_update、DELETE 后 404、
搜索（含 % / _ 字面量）、状态筛选、分页边界、路由 405/404、静态文件、
路径穿越、重启持久化（关闭并重新打开同一 DB 文件后数据仍在）。
"""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

CASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CASE_DIR not in sys.path:
    sys.path.insert(0, CASE_DIR)

os.environ["ISSUE_BOARD_QUIET"] = "1"  # 测试期间不打印请求日志

import server as server_module  # noqa: E402

TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class ApiTestCase(unittest.TestCase):
    """基类：每个用例启动独立的临时 DB + 临时端口服务器。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="issue-board-selftest-")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self._start_server()

    def _start_server(self):
        self.server = server_module.make_server("127.0.0.1", 0, self.db_path)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()

    def tearDown(self):
        self._stop_server()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # HTTP 工具
    # ------------------------------------------------------------------

    def call(self, method, path, body=None, raw_body=None):
        """发起请求，返回 (status, headers, 原始 body 字节)。"""
        url = self.base_url + path
        data = None
        headers = {}
        if raw_body is not None:
            data = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
            headers["Content-Type"] = "application/json"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def call_json(self, method, path, body=None, raw_body=None):
        status, headers, payload = self.call(method, path, body=body, raw_body=raw_body)
        parsed = json.loads(payload.decode("utf-8")) if payload else None
        return status, headers, parsed

    def create_item(self, title="默认标题", status=None, priority=None):
        body = {"title": title}
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        status_code, _, item = self.call_json("POST", "/api/items", body)
        self.assertEqual(status_code, 201)
        return item

    def assert_error(self, status, payload, expected_code, expected_status=400):
        """统一错误体断言：{"error": {"code", "message"}}，message 非空。"""
        self.assertEqual(status, expected_status)
        self.assertIsInstance(payload, dict)
        self.assertEqual(set(payload.keys()), {"error"})
        self.assertEqual(set(payload["error"].keys()), {"code", "message"})
        self.assertEqual(payload["error"]["code"], expected_code)
        message = payload["error"]["message"]
        self.assertIsInstance(message, str)
        self.assertTrue(message.strip())

    @staticmethod
    def api_path(**params):
        return "/api/items?" + urllib.parse.urlencode(params)


class TestCreate(ApiTestCase):
    def test_create_success_defaults_and_location(self):
        status, headers, item = self.call_json(
            "POST", "/api/items", {"title": "  第一个事项  "}
        )
        self.assertEqual(status, 201)
        self.assertEqual(headers.get("Content-Type"), JSON_CONTENT_TYPE)
        self.assertEqual(item["title"], "第一个事项")   # trim 后存储
        self.assertEqual(item["status"], "todo")        # 默认状态
        self.assertEqual(item["priority"], "medium")    # 默认优先级
        self.assertEqual(headers.get("Location"), "/api/items/%d" % item["id"])
        self.assertEqual(
            set(item.keys()),
            {"id", "title", "status", "priority", "created_at", "updated_at"},
        )
        self.assertRegex(item["created_at"], TIMESTAMP_PATTERN)
        self.assertEqual(item["created_at"], item["updated_at"])
        # 单条读回一致
        status, _, fetched = self.call_json("GET", "/api/items/%d" % item["id"])
        self.assertEqual(status, 200)
        self.assertEqual(fetched, item)

    def test_create_with_status_and_priority(self):
        item = self.create_item("指定字段", status="doing", priority="high")
        self.assertEqual(item["status"], "doing")
        self.assertEqual(item["priority"], "high")
        item = self.create_item("完成态", status="done", priority="low")
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["priority"], "low")

    def test_title_required_variants(self):
        bodies = ({}, {"title": None}, {"title": 123}, {"title": ""},
                  {"title": "  \t\n  "})
        for body in bodies:
            with self.subTest(body=body):
                status, _, payload = self.call_json("POST", "/api/items", body)
                self.assert_error(status, payload, "title_required")

    def test_title_length_boundary(self):
        exact_80 = "word" * 20            # 恰好 80 字符
        too_long_81 = exact_80 + "x"      # 81 字符
        status, _, item = self.call_json("POST", "/api/items", {"title": exact_80})
        self.assertEqual(status, 201)
        self.assertEqual(len(item["title"]), 80)
        status, _, payload = self.call_json("POST", "/api/items", {"title": too_long_81})
        self.assert_error(status, payload, "title_too_long")
        # 中文按 Unicode 码点计数：80 个汉字合法，81 个超长
        status, _, item = self.call_json("POST", "/api/items", {"title": "事项" * 40})
        self.assertEqual(status, 201)
        self.assertEqual(len(item["title"]), 80)
        status, _, payload = self.call_json(
            "POST", "/api/items", {"title": "事项" * 40 + "超"}
        )
        self.assert_error(status, payload, "title_too_long")
        # 两端空白去除后再判长度：trim 后恰好 80 字 -> 合法
        status, _, item = self.call_json(
            "POST", "/api/items", {"title": "  " + exact_80 + "  "}
        )
        self.assertEqual(status, 201)
        self.assertEqual(item["title"], exact_80)

    def test_invalid_status_and_priority(self):
        status, _, payload = self.call_json(
            "POST", "/api/items", {"title": "t", "status": "bogus"}
        )
        self.assert_error(status, payload, "invalid_status")
        status, _, payload = self.call_json(
            "POST", "/api/items", {"title": "t", "status": 1}
        )
        self.assert_error(status, payload, "invalid_status")
        status, _, payload = self.call_json(
            "POST", "/api/items", {"title": "t", "priority": "urgent"}
        )
        self.assert_error(status, payload, "invalid_priority")
        status, _, payload = self.call_json(
            "POST", "/api/items", {"title": "t", "priority": None}
        )
        self.assert_error(status, payload, "invalid_priority")

    def test_invalid_json_bodies(self):
        status, _, payload = self.call_json(
            "POST", "/api/items", raw_body="not json at all"
        )
        self.assert_error(status, payload, "invalid_json")
        status, _, payload = self.call_json("POST", "/api/items", raw_body="[1, 2, 3]")
        self.assert_error(status, payload, "invalid_json")
        status, _, payload = self.call_json(
            "POST", "/api/items", raw_body='"just a string"'
        )
        self.assert_error(status, payload, "invalid_json")
        status, _, payload = self.call_json("POST", "/api/items")  # 无请求体
        self.assert_error(status, payload, "invalid_json")

    def test_unknown_fields_ignored(self):
        status, _, item = self.call_json(
            "POST", "/api/items", {"title": "带未知字段", "unknown": "x"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(item["title"], "带未知字段")


class TestReadOne(ApiTestCase):
    def test_get_existing_item(self):
        created = self.create_item("查询我")
        status, _, item = self.call_json("GET", "/api/items/%d" % created["id"])
        self.assertEqual(status, 200)
        self.assertEqual(item, created)

    def test_get_missing_item_returns_404(self):
        status, headers, payload = self.call_json("GET", "/api/items/999999")
        self.assert_error(status, payload, "item_not_found", expected_status=404)
        self.assertEqual(headers.get("Content-Type"), JSON_CONTENT_TYPE)

    def test_invalid_id_formats(self):
        for bad in ("abc", "0", "-1", "1.5", "1a", " 1"):
            with self.subTest(bad_id=bad):
                path = "/api/items/" + urllib.parse.quote(bad)
                for method in ("GET", "PUT", "DELETE"):
                    body = {"title": "x"} if method == "PUT" else None
                    status, _, payload = self.call_json(method, path, body=body)
                    self.assert_error(status, payload, "invalid_id")

    def test_collection_trailing_slash_is_list(self):
        # 宽松行为说明：/api/items/（带尾斜杠）等同集合路径
        self.create_item("尾斜杠")
        status, _, data = self.call_json("GET", "/api/items/")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)


class TestUpdate(ApiTestCase):
    def test_put_partial_update_keeps_other_fields(self):
        created = self.create_item("原标题", status="todo", priority="medium")
        time.sleep(1.05)  # 时间戳为秒级精度，等待 1 秒确保 updated_at 可变化
        status, _, updated = self.call_json(
            "PUT", "/api/items/%d" % created["id"], {"title": "新标题"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["status"], "todo")      # 未传字段保持原值
        self.assertEqual(updated["priority"], "medium")
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertRegex(updated["updated_at"], TIMESTAMP_PATTERN)
        self.assertGreater(updated["updated_at"], created["created_at"])

    def test_put_status_and_priority(self):
        created = self.create_item("改状态")
        status, _, updated = self.call_json(
            "PUT",
            "/api/items/%d" % created["id"],
            {"status": "done", "priority": "low"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["priority"], "low")
        self.assertEqual(updated["title"], "改状态")

    def test_put_empty_update(self):
        created = self.create_item("空更新")
        status, _, payload = self.call_json(
            "PUT", "/api/items/%d" % created["id"], {}
        )
        self.assert_error(status, payload, "empty_update")
        status, _, payload = self.call_json(
            "PUT", "/api/items/%d" % created["id"], {"unknown_field": 1}
        )
        self.assert_error(status, payload, "empty_update")

    def test_put_validation_errors(self):
        created = self.create_item("校验")
        path = "/api/items/%d" % created["id"]
        status, _, payload = self.call_json("PUT", path, {"title": "   "})
        self.assert_error(status, payload, "title_required")
        status, _, payload = self.call_json("PUT", path, {"title": None})
        self.assert_error(status, payload, "title_required")
        status, _, payload = self.call_json("PUT", path, {"title": "x" * 81})
        self.assert_error(status, payload, "title_too_long")
        status, _, payload = self.call_json("PUT", path, {"status": "bogus"})
        self.assert_error(status, payload, "invalid_status")
        status, _, payload = self.call_json("PUT", path, {"priority": "urgent"})
        self.assert_error(status, payload, "invalid_priority")
        status, _, payload = self.call_json("PUT", path, raw_body="{broken")
        self.assert_error(status, payload, "invalid_json")

    def test_put_missing_item(self):
        status, _, payload = self.call_json(
            "PUT", "/api/items/999999", {"title": "ok"}
        )
        self.assert_error(status, payload, "item_not_found", expected_status=404)


class TestDelete(ApiTestCase):
    def test_delete_flow(self):
        created = self.create_item("待删除")
        path = "/api/items/%d" % created["id"]
        status, _, payload = self.call("DELETE", path)
        self.assertEqual(status, 204)
        self.assertEqual(payload, b"")  # 204 空响应体
        status, _, payload = self.call_json("GET", path)
        self.assert_error(status, payload, "item_not_found", expected_status=404)
        status, _, payload = self.call_json("DELETE", path)  # 重复删除仍 404
        self.assert_error(status, payload, "item_not_found", expected_status=404)

    def test_delete_invalid_id(self):
        for bad in ("abc", "0", "-1"):
            with self.subTest(bad_id=bad):
                status, _, payload = self.call_json("DELETE", "/api/items/" + bad)
                self.assert_error(status, payload, "invalid_id")

    def test_delete_missing_item(self):
        status, _, payload = self.call_json("DELETE", "/api/items/999999")
        self.assert_error(status, payload, "item_not_found", expected_status=404)


class TestListSearchFilterPagination(ApiTestCase):
    def test_list_shape_and_order(self):
        first = self.create_item("第一条")
        second = self.create_item("第二条")
        third = self.create_item("第三条")
        status, headers, data = self.call_json("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), JSON_CONTENT_TYPE)
        self.assertEqual(
            set(data.keys()), {"items", "page", "page_size", "total", "total_pages"}
        )
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 10)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["total_pages"], 1)
        ids = [item["id"] for item in data["items"]]
        self.assertEqual(ids, [third["id"], second["id"], first["id"]])  # id DESC
        for item in data["items"]:
            self.assertEqual(
                set(item.keys()),
                {"id", "title", "status", "priority", "created_at", "updated_at"},
            )

    def test_empty_list_shape(self):
        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["total_pages"], 1)  # max(1, ceil(0/10))

    def test_search_case_insensitive_substring(self):
        self.create_item("Hello World")
        self.create_item("另一条事项")
        status, _, data = self.call_json("GET", self.api_path(q="hello"))
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "Hello World")
        status, _, data = self.call_json("GET", self.api_path(q="ORLD"))
        self.assertEqual(data["total"], 1)
        # 空串等同不传
        status, _, data = self.call_json("GET", self.api_path(q=""))
        self.assertEqual(data["total"], 2)

    def test_search_escapes_like_wildcards(self):
        self.create_item("100% done")
        self.create_item("snake_case task")
        self.create_item("plain title")
        # % 按字面量匹配：只命中标题含字面 % 的记录
        status, _, data = self.call_json("GET", self.api_path(q="%"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "100% done")
        # q=100% 精确命中
        status, _, data = self.call_json("GET", self.api_path(q="100%"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "100% done")
        # _ 按字面量匹配
        status, _, data = self.call_json("GET", self.api_path(q="_"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "snake_case task")
        status, _, data = self.call_json("GET", self.api_path(q="snake_case"))
        self.assertEqual(data["total"], 1)

    def test_status_filter_and_combined_with_q(self):
        self.create_item("买牛奶", status="todo")
        self.create_item("写报告", status="doing")
        self.create_item("买菜回家", status="done")
        status, _, data = self.call_json("GET", self.api_path(status="doing"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "写报告")
        # q AND status 组合
        status, _, data = self.call_json("GET", self.api_path(q="买", status="todo"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "买牛奶")
        status, _, data = self.call_json("GET", self.api_path(q="买", status="done"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "买菜回家")
        # 过滤后无匹配：200 + 空 items
        status, _, data = self.call_json(
            "GET", self.api_path(q="不存在的词", status="todo")
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["total_pages"], 1)

    def test_invalid_query_params(self):
        queries = (
            "page=0", "page=abc", "page=-1", "page=1.5", "page=%201",
            "page_size=0", "page_size=101", "page_size=abc", "page_size=-5",
            "status=bogus", "status=TODO", "status=",
        )
        for query in queries:
            with self.subTest(query=query):
                status, _, payload = self.call_json("GET", "/api/items?" + query)
                self.assert_error(status, payload, "invalid_param")

    def test_pagination_pages(self):
        for index in range(25):
            self.create_item("分页事项%02d" % index)
        status, _, page1 = self.call_json("GET", self.api_path(page=1, page_size=10))
        self.assertEqual(status, 200)
        self.assertEqual(page1["total"], 25)
        self.assertEqual(page1["total_pages"], 3)
        self.assertEqual(len(page1["items"]), 10)
        self.assertEqual(page1["items"][0]["title"], "分页事项24")  # id DESC 最新在前
        status, _, page2 = self.call_json("GET", self.api_path(page=2, page_size=10))
        self.assertEqual(len(page2["items"]), 10)
        self.assertEqual(page2["items"][0]["title"], "分页事项14")
        status, _, page3 = self.call_json("GET", self.api_path(page=3, page_size=10))
        self.assertEqual(len(page3["items"]), 5)
        self.assertEqual(page3["items"][-1]["title"], "分页事项00")
        # page 超出总页数：200 + 空数组（不是 404）
        status, _, beyond = self.call_json("GET", self.api_path(page=4, page_size=10))
        self.assertEqual(status, 200)
        self.assertEqual(beyond["items"], [])
        self.assertEqual(beyond["page"], 4)
        self.assertEqual(beyond["total"], 25)
        # page_size 上限 100 合法
        status, _, big = self.call_json("GET", self.api_path(page_size=100))
        self.assertEqual(status, 200)
        self.assertEqual(len(big["items"]), 25)
        self.assertEqual(big["page_size"], 100)
        # 各页无重复、无遗漏
        all_ids = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(len(all_ids), 25)


class TestRoutingAndStatic(ApiTestCase):
    def test_method_not_allowed(self):
        created = self.create_item("路由")
        cases = (
            ("DELETE", "/api/items"),
            ("PUT", "/api/items"),
            ("POST", "/api/items/%d" % created["id"]),
            ("PATCH", "/api/items/%d" % created["id"]),
            ("OPTIONS", "/api/items"),
            ("HEAD", "/api/items"),
        )
        for method, path in cases:
            with self.subTest(method=method, path=path):
                status, headers, payload = self.call(method, path)
                self.assertEqual(status, 405)
                self.assertEqual(headers.get("Content-Type"), JSON_CONTENT_TYPE)
                if method == "HEAD":
                    # HEAD 响应按 RFC 不带 body，仅核对状态码与头部
                    self.assertEqual(payload, b"")
                    continue
                parsed = json.loads(payload.decode("utf-8"))
                self.assertEqual(parsed["error"]["code"], "method_not_allowed")
                self.assertTrue(parsed["error"]["message"])

    def test_unknown_paths_are_not_found(self):
        for path in ("/api/unknown", "/api/items/1/extra", "/nope.html", "/api"):
            with self.subTest(path=path):
                status, headers, payload = self.call_json("GET", path)
                self.assert_error(status, payload, "not_found", expected_status=404)
                self.assertEqual(headers.get("Content-Type"), JSON_CONTENT_TYPE)

    def test_static_files_served(self):
        status, headers, payload = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = payload.decode("utf-8")
        self.assertIn("<title>", html)
        self.assertIn("app.js", html)
        self.assertIn("app.css", html)
        status, headers, _ = self.call("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))
        status, headers, payload = self.call("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertIn("textContent", payload.decode("utf-8"))

    def test_path_traversal_blocked(self):
        for path in ("/%2e%2e/server.py", "/%2e%2e/%2e%2e/etc/passwd",
                     "/../server.py", "/..%2fserver.py"):
            with self.subTest(path=path):
                status, _, payload = self.call("GET", path)
                self.assertEqual(status, 404, "路径穿越应返回 404: %s" % path)
                self.assertNotIn(b"SCHEMA", payload)
                self.assertNotIn(b"root:", payload)

    def test_head_on_static_has_no_body(self):
        status, headers, payload = self.call("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertEqual(payload, b"")


class TestPersistence(ApiTestCase):
    def test_data_survives_server_restart(self):
        created = self.create_item("持久化数据", status="doing", priority="high")
        second = self.create_item("将被删除")
        status, _, _ = self.call("DELETE", "/api/items/%d" % second["id"])
        self.assertEqual(status, 204)

        # 模拟服务重启：关闭当前 server，用同一 DB 文件重新启动
        self._stop_server()
        self._start_server()

        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        restored = data["items"][0]
        self.assertEqual(restored["id"], created["id"])                  # id 保留
        self.assertEqual(restored["title"], "持久化数据")
        self.assertEqual(restored["status"], "doing")
        self.assertEqual(restored["priority"], "high")
        self.assertEqual(restored["created_at"], created["created_at"])  # created_at 保留
        # 已删除记录保持删除状态
        status, _, payload = self.call_json("GET", "/api/items/%d" % second["id"])
        self.assert_error(status, payload, "item_not_found", expected_status=404)

    def test_fresh_db_file_starts_empty(self):
        self.create_item("旧库数据")
        self._stop_server()
        # --db 指向新路径 -> 全新空库（D3）
        self.db_path = os.path.join(self.tmpdir, "another.db")
        self._start_server()
        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)

    def test_relist_after_mutations_matches_operations(self):
        # 等价于「浏览器刷新页面」：每次增/改/删后重新 GET 列表，结果与操作一致（D1）
        item = self.create_item("刷新前")
        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual([i["id"] for i in data["items"]], [item["id"]])
        self.call_json("PUT", "/api/items/%d" % item["id"], {"status": "done"})
        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual(data["items"][0]["status"], "done")
        self.call("DELETE", "/api/items/%d" % item["id"])
        status, _, data = self.call_json("GET", "/api/items")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
