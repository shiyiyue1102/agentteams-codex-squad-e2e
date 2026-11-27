"""开发自测（unittest，仅 Python 标准库）。

运行方式（在 case 目录下）:
    python3 -m unittest discover -s tests -v

约定：
- 全部使用临时目录中的 SQLite 文件与随机端口（create_server 绑定端口 0），
  不写入/依赖默认 issues.db，不依赖固定网络环境。
- 断言 = HTTP 状态码 + error.code（不锁定 message 文案，仅断言非空）。
"""

import http.client
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_DIR))

import server as server_mod  # noqa: E402

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISSUE_KEYS = {"id", "title", "status", "priority", "created_at", "updated_at"}


class ApiTestCase(unittest.TestCase):
    """在临时目录中以随机端口启动被测服务（进程内重建即“重启”）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="issue-board-test-"))
        self.db_path = self.tmpdir / "test-issues.db"
        self._start_server()

    def _start_server(self):
        self.server = server_mod.create_server("127.0.0.1", 0, str(self.db_path))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def tearDown(self):
        self._stop_server()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------- 请求工具 ----------

    def request(self, method, path, body=None, raw_body=None, headers=None):
        url = self.base + path
        data = None
        if raw_body is not None:
            data = raw_body if isinstance(raw_body, bytes) \
                else raw_body.encode("utf-8")
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(), dict(exc.headers)
            finally:
                exc.close()

    def api(self, method, path, **kwargs):
        """返回 (status, parsed_json, headers)。"""
        status, payload, headers = self.request(method, path, **kwargs)
        parsed = json.loads(payload.decode("utf-8")) if payload else None
        return status, parsed, headers

    def post_issue(self, title, **extra):
        payload = dict(extra)
        payload["title"] = title
        return self.api("POST", "/api/issues", body=payload)

    def create_issue(self, title, status="todo", priority="medium"):
        result = self.post_issue(title, status=status, priority=priority)
        self.assertEqual(result[0], 201, result[1])
        return result[1]["issue"]

    def assert_error(self, result, status, code, field=None):
        got_status, payload, _headers = result
        self.assertEqual(got_status, status)
        self.assertIsInstance(payload, dict)
        self.assertIn("error", payload)
        err = payload["error"]
        self.assertEqual(err.get("code"), code)
        self.assertIsInstance(err.get("message"), str)
        self.assertTrue(err["message"].strip(), "message 必须非空")
        if field is not None:
            self.assertEqual(err.get("field"), field)
        return err


class TestCreate(ApiTestCase):
    def test_create_defaults_and_trim(self):
        result = self.post_issue("  修复登录页样式  ")
        self.assertEqual(result[0], 201)
        issue = result[1]["issue"]
        self.assertEqual(set(issue), ISSUE_KEYS)
        self.assertIsInstance(issue["id"], int)
        self.assertGreater(issue["id"], 0)
        self.assertEqual(issue["title"], "修复登录页样式")
        self.assertEqual(issue["status"], "todo")
        self.assertEqual(issue["priority"], "medium")
        self.assertRegex(issue["created_at"], TS_RE)
        self.assertRegex(issue["updated_at"], TS_RE)
        # GET 单条与创建响应一致
        single = self.api("GET", "/api/issues/%d" % issue["id"])
        self.assertEqual(single[0], 200)
        self.assertEqual(single[1]["issue"], issue)

    def test_create_explicit_enums(self):
        result = self.post_issue("t", status="in_progress", priority="high")
        self.assertEqual(result[0], 201)
        self.assertEqual(result[1]["issue"]["status"], "in_progress")
        self.assertEqual(result[1]["issue"]["priority"], "high")
        result = self.post_issue("t2", status="done", priority="low")
        self.assertEqual(result[0], 201)
        self.assertEqual(result[1]["issue"]["status"], "done")
        self.assertEqual(result[1]["issue"]["priority"], "low")

    def test_create_null_enums_use_defaults(self):
        result = self.api("POST", "/api/issues",
                          body={"title": "t", "status": None, "priority": None})
        self.assertEqual(result[0], 201)
        self.assertEqual(result[1]["issue"]["status"], "todo")
        self.assertEqual(result[1]["issue"]["priority"], "medium")

    def test_create_title_exactly_80_codepoints(self):
        for title in ("a" * 80, "字" * 80, "  " + "字" * 80 + "  "):
            result = self.post_issue(title)
            self.assertEqual(result[0], 201, result[1])
            self.assertEqual(len(result[1]["issue"]["title"]), 80)

    def test_create_title_81_too_long(self):
        for title in ("a" * 81, "字" * 81, "  " + "字" * 81):
            self.assert_error(self.post_issue(title), 400,
                              "TITLE_TOO_LONG", field="title")

    def test_create_title_required_variants(self):
        for payload in ({}, {"title": None}, {"title": "   "},
                        {"title": "\t\n "}, {"title": 123},
                        {"title": ["x"]}, {"title": True}):
            self.assert_error(
                self.api("POST", "/api/issues", body=payload),
                400, "TITLE_REQUIRED", field="title")

    def test_create_invalid_status(self):
        for status in ("TODO", "doing", "", "todo ", 3, True, ("todo",)):
            self.assert_error(self.post_issue("t", status=status),
                              400, "INVALID_STATUS", field="status")

    def test_create_invalid_priority(self):
        for priority in ("URGENT", "Medium", "", 1, False):
            self.assert_error(self.post_issue("t", priority=priority),
                              400, "INVALID_PRIORITY", field="priority")

    def test_create_multi_error_order(self):
        # title -> status -> priority（D9）
        self.assert_error(
            self.api("POST", "/api/issues",
                     body={"title": "  ", "status": "bad",
                           "priority": "bad"}),
            400, "TITLE_REQUIRED", field="title")
        self.assert_error(
            self.api("POST", "/api/issues",
                     body={"title": "ok", "status": "bad",
                           "priority": "bad"}),
            400, "INVALID_STATUS", field="status")

    def test_create_invalid_body(self):
        self.assert_error(
            self.api("POST", "/api/issues", raw_body="{not json"),
            400, "INVALID_BODY")
        self.assert_error(self.api("POST", "/api/issues"), 400, "INVALID_BODY")
        self.assert_error(
            self.api("POST", "/api/issues", raw_body=""),
            400, "INVALID_BODY")
        self.assert_error(
            self.api("POST", "/api/issues", raw_body='["title"]'),
            400, "INVALID_BODY")
        self.assert_error(
            self.api("POST", "/api/issues", raw_body='"str"'),
            400, "INVALID_BODY")

    def test_create_unknown_fields_ignored(self):
        result = self.api("POST", "/api/issues",
                          body={"title": "t", "foo": "bar", "id": 999})
        self.assertEqual(result[0], 201)
        self.assertEqual(set(result[1]["issue"]), ISSUE_KEYS)

    def test_create_body_parsed_regardless_of_content_type(self):
        # D10：不论 Content-Type 一律按 JSON 解析
        status, payload, _headers = self.request(
            "POST", "/api/issues",
            raw_body=json.dumps({"title": "no-ct"}),
            headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(payload)["issue"]["title"], "no-ct")


class TestReadUpdateDelete(ApiTestCase):
    def test_get_single_and_not_found(self):
        issue = self.create_issue("任务A")
        result = self.api("GET", "/api/issues/%d" % issue["id"])
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["issue"], issue)
        self.assert_error(self.api("GET", "/api/issues/999999"),
                          404, "NOT_FOUND")
        self.assert_error(self.api("GET", "/api/issues/0"), 404, "NOT_FOUND")

    def test_non_integer_id_variants(self):
        for raw in ("abc", "1.5", "-1a", "-1", "1a", "%20", "null"):
            self.assert_error(self.api("GET", "/api/issues/" + raw),
                              400, "INVALID_PARAM", field="id")
            self.assert_error(
                self.api("PUT", "/api/issues/" + raw,
                         body={"title": "t", "status": "todo",
                               "priority": "low"}),
                400, "INVALID_PARAM", field="id")
            self.assert_error(self.api("DELETE", "/api/issues/" + raw),
                              400, "INVALID_PARAM", field="id")

    def test_put_full_update(self):
        issue = self.create_issue("旧标题", status="todo", priority="low")
        result = self.api(
            "PUT", "/api/issues/%d" % issue["id"],
            body={"title": "  新标题  ", "status": "done",
                  "priority": "high"})
        self.assertEqual(result[0], 200)
        updated = result[1]["issue"]
        self.assertEqual(updated["id"], issue["id"])
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["priority"], "high")
        self.assertEqual(updated["created_at"], issue["created_at"])
        self.assertRegex(updated["updated_at"], TS_RE)
        self.assertGreaterEqual(updated["updated_at"], issue["updated_at"])
        # 后续 GET 反映修改
        single = self.api("GET", "/api/issues/%d" % issue["id"])
        self.assertEqual(single[1]["issue"], updated)

    def test_put_requires_all_fields(self):
        issue = self.create_issue("t")
        url = "/api/issues/%d" % issue["id"]
        self.assert_error(
            self.api("PUT", url, body={"title": "t2"}),
            400, "INVALID_STATUS", field="status")
        self.assert_error(
            self.api("PUT", url, body={"title": "t2", "status": "todo"}),
            400, "INVALID_PRIORITY", field="priority")
        self.assert_error(
            self.api("PUT", url, body={"status": "todo", "priority": "low"}),
            400, "TITLE_REQUIRED", field="title")
        self.assert_error(
            self.api("PUT", url, body={"title": None, "status": None,
                                       "priority": None}),
            400, "TITLE_REQUIRED", field="title")
        self.assert_error(
            self.api("PUT", url, body={"title": "t2", "status": None,
                                       "priority": "low"}),
            400, "INVALID_STATUS", field="status")
        self.assert_error(
            self.api("PUT", url, body={"title": "x" * 81,
                                       "status": "todo",
                                       "priority": "low"}),
            400, "TITLE_TOO_LONG", field="title")

    def test_put_not_found_before_body_validation(self):
        # D9：404 优先于 body 校验
        self.assert_error(
            self.api("PUT", "/api/issues/999999", raw_body="{bad json"),
            404, "NOT_FOUND")
        self.assert_error(
            self.api("PUT", "/api/issues/999999", body={"title": "  "}),
            404, "NOT_FOUND")

    def test_put_invalid_json_existing_id(self):
        issue = self.create_issue("t")
        self.assert_error(
            self.api("PUT", "/api/issues/%d" % issue["id"],
                     raw_body="{bad"),
            400, "INVALID_BODY")

    def test_delete_flow(self):
        issue = self.create_issue("待删除")
        url = "/api/issues/%d" % issue["id"]
        result = self.api("DELETE", url)
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1], {"ok": True, "id": issue["id"]})
        self.assert_error(self.api("GET", url), 404, "NOT_FOUND")
        self.assert_error(self.api("DELETE", url), 404, "NOT_FOUND")
        listing = self.api("GET", "/api/issues")
        ids = [item["id"] for item in listing[1]["items"]]
        self.assertNotIn(issue["id"], ids)

    def test_delete_not_found(self):
        self.assert_error(self.api("DELETE", "/api/issues/999999"),
                          404, "NOT_FOUND")


class TestListSearchFilterPagination(ApiTestCase):
    def test_list_empty_envelope(self):
        result = self.api("GET", "/api/issues")
        self.assertEqual(result[0], 200)
        payload = result[1]
        self.assertEqual(set(payload),
                         {"items", "page", "page_size", "total",
                          "total_pages"})
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 10)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["total_pages"], 0)

    def test_list_order_created_desc_id_desc(self):
        first = self.create_issue("第一个")
        second = self.create_issue("第二个")
        third = self.create_issue("第三个")
        listing = self.api("GET", "/api/issues")[1]
        self.assertEqual([i["id"] for i in listing["items"]],
                         [third["id"], second["id"], first["id"]])

    def test_keyword_substring_and_case_insensitive(self):
        from urllib.parse import quote
        self.create_issue("修复登录页样式")
        self.create_issue("Login Page redesign")
        self.create_issue("无关事项")
        result = self.api(
            "GET", "/api/issues?keyword=" + quote("登录"))[1]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "修复登录页样式")
        result = self.api("GET", "/api/issues?keyword=login")[1]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Login Page redesign")
        result = self.api("GET", "/api/issues?keyword=LOGIN")[1]
        self.assertEqual(result["total"], 1)

    def test_keyword_wildcards_literal(self):
        self.create_issue("100%")
        self.create_issue("1001")
        self.create_issue("a_c")
        self.create_issue("abc")
        self.create_issue("a\\b")
        self.create_issue("axb")
        cases = {
            "100%": ["100%"],
            "a_c": ["a_c"],
            "a\\b": ["a\\b"],
            "%": ["100%"],          # % 字面：仅标题含 % 的条目
            "_c": ["a_c"],
        }
        for keyword, expected_titles in cases.items():
            from urllib.parse import quote
            result = self.api(
                "GET", "/api/issues?keyword=" + quote(keyword, safe=""))[1]
            got = sorted(i["title"] for i in result["items"])
            self.assertEqual(got, sorted(expected_titles),
                             "keyword=%r" % keyword)

    def test_keyword_blank_treated_as_absent(self):
        self.create_issue("甲")
        self.create_issue("乙")
        for query in ("keyword=", "keyword=%20%20"):
            result = self.api("GET", "/api/issues?" + query)[1]
            self.assertEqual(result["total"], 2, query)

    def test_status_filter_and_combination(self):
        self.create_issue("登录 todo", status="todo")
        self.create_issue("登录 done", status="done")
        self.create_issue("其他 done", status="done")
        for status, expect in (("todo", 1), ("in_progress", 0), ("done", 2)):
            result = self.api("GET", "/api/issues?status=" + status)[1]
            self.assertEqual(result["total"], expect)
            self.assertTrue(all(i["status"] == status
                                for i in result["items"]))
        # AND 组合
        result = self.api(
            "GET", "/api/issues?keyword=%E7%99%BB%E5%BD%95&status=done")[1]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "登录 done")
        # 空串视为未过滤
        result = self.api("GET", "/api/issues?status=")[1]
        self.assertEqual(result["total"], 3)

    def test_status_filter_invalid(self):
        self.assert_error(self.api("GET", "/api/issues?status=TODO"),
                          400, "INVALID_STATUS", field="status")
        self.assert_error(self.api("GET", "/api/issues?status=bad"),
                          400, "INVALID_STATUS", field="status")

    def test_pagination_math_and_echo(self):
        created = [self.create_issue("条目%02d" % n) for n in range(25)]
        listing = self.api("GET", "/api/issues?page=1&page_size=10")[1]
        self.assertEqual(listing["total"], 25)
        self.assertEqual(listing["total_pages"], 3)
        self.assertEqual(listing["page"], 1)
        self.assertEqual(listing["page_size"], 10)
        seen = [i["id"] for i in listing["items"]]
        self.assertEqual(len(seen), 10)
        # 三页无重复无遗漏，且整体等于倒序创建顺序
        all_ids = []
        for page in (1, 2, 3):
            data = self.api(
                "GET", "/api/issues?page=%d&page_size=10" % page)[1]
            self.assertEqual(data["page"], page)
            all_ids.extend(i["id"] for i in data["items"])
        self.assertEqual(len(all_ids), 25)
        self.assertEqual(len(set(all_ids)), 25)
        self.assertEqual(all_ids,
                         [i["id"] for i in reversed(created)])
        # 末页 5 条
        data = self.api("GET", "/api/issues?page=3&page_size=10")[1]
        self.assertEqual(len(data["items"]), 5)
        # page_size=100 一页取完
        data = self.api("GET", "/api/issues?page_size=100")[1]
        self.assertEqual(data["total_pages"], 1)
        self.assertEqual(len(data["items"]), 25)

    def test_pagination_out_of_range_page(self):
        self.create_issue("仅一条")
        result = self.api("GET", "/api/issues?page=99")
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["items"], [])
        self.assertEqual(result[1]["total"], 1)
        self.assertEqual(result[1]["total_pages"], 1)
        self.assertEqual(result[1]["page"], 99)

    def test_pagination_invalid_params(self):
        for query, field in (("page=0", "page"), ("page=-1", "page"),
                             ("page=abc", "page"), ("page=1.5", "page"),
                             ("page=", "page"),
                             ("page_size=0", "page_size"),
                             ("page_size=101", "page_size"),
                             ("page_size=xx", "page_size"),
                             ("page_size=-5", "page_size")):
            self.assert_error(self.api("GET", "/api/issues?" + query),
                              400, "INVALID_PARAM", field=field)

    def test_list_validation_order_status_before_page(self):
        self.assert_error(self.api("GET", "/api/issues?status=bad&page=0"),
                          400, "INVALID_STATUS", field="status")

    def test_duplicate_params_take_first(self):
        self.create_issue("登录 todo", status="todo")
        self.create_issue("登录 done", status="done")
        result = self.api("GET", "/api/issues?status=todo&status=done")[1]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["status"], "todo")


class TestRoutingAndEnvelope(ApiTestCase):
    def test_unknown_api_path_404(self):
        self.assert_error(self.api("GET", "/api/foo"), 404, "NOT_FOUND")
        self.assert_error(self.api("POST", "/api/foo", body={}),
                          404, "NOT_FOUND")
        self.assert_error(self.api("GET", "/api/issues/1/2"),
                          404, "NOT_FOUND")
        self.assert_error(self.api("GET", "/api"), 404, "NOT_FOUND")

    def test_method_not_allowed(self):
        issue = self.create_issue("t")
        result = self.api("POST", "/api/issues/%d" % issue["id"], body={})
        self.assert_error(result, 405, "METHOD_NOT_ALLOWED")
        self.assertIn("GET", result[2].get("Allow", ""))
        result = self.api("DELETE", "/api/issues")
        self.assert_error(result, 405, "METHOD_NOT_ALLOWED")
        self.assertEqual(result[2].get("Allow"), "GET, POST")
        self.assert_error(self.api("PUT", "/api/issues",
                                   body={"title": "t"}),
                          405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.api("PATCH", "/api/issues/%d" % issue["id"],
                                   body={}),
                          405, "METHOD_NOT_ALLOWED")

    def test_json_content_type_and_nosniff(self):
        _status, _payload, headers = self.request("GET", "/api/issues")
        ctype = headers.get("Content-Type", "")
        self.assertIn("application/json", ctype)
        self.assertIn("charset=utf-8", ctype)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_error_envelope_shape(self):
        result = self.api("POST", "/api/issues", body={"title": "  "})
        err = self.assert_error(result, 400, "TITLE_REQUIRED", field="title")
        self.assertEqual(set(err), {"code", "message", "field"})
        result = self.api("GET", "/api/issues/999999")
        err = self.assert_error(result, 404, "NOT_FOUND")
        self.assertEqual(set(err), {"code", "message"})


class TestStaticPages(ApiTestCase):
    def test_index_page(self):
        status, payload, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("charset=utf-8", headers.get("Content-Type", ""))
        text = payload.decode("utf-8")
        self.assertIn("事项管理", text)
        self.assertIn('name="viewport"', text)

    def test_static_assets(self):
        for path, ctype_part in (("/static/app.css", "text/css"),
                                 ("/static/app.js", "javascript")):
            status, payload, headers = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(ctype_part, headers.get("Content-Type", ""))
            self.assertTrue(payload)

    def test_static_missing_file_404(self):
        status, _payload, _headers = self.request("GET", "/static/nope.css")
        self.assertEqual(status, 404)
        status, _payload, _headers = self.request("GET", "/unknown-page")
        self.assertEqual(status, 404)

    def test_static_traversal_rejected(self):
        # 原始未规范化路径（http.client 不做规范化）
        for raw_path in ("/static/../server.py", "/static/../../etc/hosts",
                         "/static/%2e%2e/server.py",
                         "/static/%2e%2e%2fserver.py"):
            conn = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=15)
            try:
                conn.request("GET", raw_path)
                resp = conn.getresponse()
                body = resp.read()
                self.assertIn(resp.status, (400, 403, 404), raw_path)
                self.assertNotIn(b"ThreadingHTTPServer", body, raw_path)
                self.assertNotIn(b"def create_server", body, raw_path)
            finally:
                conn.close()


class TestPersistence(ApiTestCase):
    def test_restart_in_process_same_db(self):
        a = self.create_issue("重启前A", status="in_progress",
                              priority="high")
        b = self.create_issue("重启前B")
        updated = self.api("PUT", "/api/issues/%d" % b["id"],
                           body={"title": "重启前B改",
                                 "status": "done",
                                 "priority": "low"})[1]["issue"]
        before = [a, updated]

        # “重启”：停掉当前 server 实例，用同一 db 文件重建
        self._stop_server()
        self._start_server()

        listing = self.api("GET", "/api/issues?page_size=100")[1]
        self.assertEqual(listing["total"], 2)
        by_id = {i["id"]: i for i in listing["items"]}
        for issue in before:
            self.assertEqual(by_id.get(issue["id"]), issue)

    def test_restart_real_subprocess(self):
        # 真实子进程：启动 server.py → 写入 → terminate → 同一 --db 重启 → 数据保留
        def spawn():
            proc = subprocess.Popen(
                [sys.executable, "-B", str(CASE_DIR / "server.py"),
                 "--host", "127.0.0.1", "--port", "0",
                 "--db", str(self.db_path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=str(self.tmpdir))
            line = ""
            deadline = time.time() + 20
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line.startswith("Serving on"):
                    break
            match = re.search(r"http://([^:/]+):(\d+)/", line)
            self.assertIsNotNone(match, "未读取到启动端口: %r" % line)
            return proc, "http://%s:%s" % (match.group(1), match.group(2))

        old_base = self.base
        self._stop_server()  # 释放测试夹具服务，端口/数据库交给子进程

        proc, base = spawn()
        try:
            self.base = base
            issue = self.create_issue("子进程写入", status="done",
                                      priority="high")
        finally:
            proc.terminate()
            proc.wait(timeout=10)

        proc2, base2 = spawn()
        try:
            self.base = base2
            single = self.api("GET", "/api/issues/%d" % issue["id"])
            self.assertEqual(single[0], 200)
            self.assertEqual(single[1]["issue"], issue)
            listing = self.api("GET", "/api/issues")[1]
            self.assertEqual(listing["total"], 1)
        finally:
            proc2.terminate()
            proc2.wait(timeout=10)
            # 恢复夹具服务供 tearDown 停止
            self._start_server()
            self.base = old_base


class TestSchema(ApiTestCase):
    def test_table_schema_matches_spec(self):
        import sqlite3
        self.create_issue("schema")
        conn = sqlite3.connect(str(self.db_path))
        try:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master"
                " WHERE type='table' AND name='issues'").fetchone()[0]
        finally:
            conn.close()
        normalized = re.sub(r"\s+", " ", ddl)
        self.assertIn("id INTEGER PRIMARY KEY AUTOINCREMENT", normalized)
        self.assertIn("title TEXT NOT NULL", normalized)
        self.assertIn("CHECK (status IN ('todo', 'in_progress', 'done'))",
                      normalized)
        self.assertIn("CHECK (priority IN ('low', 'medium', 'high'))",
                      normalized)
        self.assertIn("created_at TEXT NOT NULL", normalized)
        self.assertIn("updated_at TEXT NOT NULL", normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
