#!/usr/bin/env python3
"""开发自测：事项管理应用 API（标准库 unittest + urllib，零外部依赖）。

运行方式（在 case 目录 cases/issue-board-20261128-032037-qwen/ 下）：
    python3 -m unittest discover tests -v
或：
    python3 tests/test_api.py

测试在临时目录创建 SQLite 数据库、在随机可用端口上启动真实服务
（线程内服务 + 一次真实子进程重启验证持久化），结束后自动清理。
覆盖分析报告 §8 验收清单第 1–13 条（API/持久化/错误边界）。
"""

import json
import os
import shutil
import socket
import subprocess
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

import db as db_module  # noqa: E402
import server as server_module  # noqa: E402


def http_request(method, url, body=None, raw_body=None, timeout=10):
    """发起 HTTP 请求，4xx/5xx 不抛异常。

    返回 (status, headers_dict, parsed_json_or_None)。
    """
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload_bytes = resp.read()
            status = resp.status
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        payload_bytes = exc.read()
        status = exc.code
        resp_headers = dict(exc.headers)
    payload = None
    if payload_bytes:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except ValueError:
            payload = None
    return status, resp_headers, payload


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ThreadServer:
    """在后台线程中运行真实的 ThreadingHTTPServer（随机端口）。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self.httpd = None
        self.thread = None
        self.base_url = None

    def start(self):
        db_module.init_db(self.db_path)
        self.httpd = server_module.IssueBoardServer(("127.0.0.1", 0), self.db_path)
        self.base_url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(timeout=5)
            self.httpd = None


class SubprocessServer:
    """真实子进程方式运行 server.py（用于进程重启持久化验证）。"""

    def __init__(self, db_path, port=None):
        self.db_path = db_path
        self.port = port or free_port()
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.proc = None

    def start(self):
        env = dict(os.environ)
        env.pop("ISSUE_BOARD_DB", None)
        env.pop("HOST", None)
        env.pop("PORT", None)
        self.proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join(CASE_DIR, "server.py"),
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "--db", self.db_path,
            ],
            cwd=CASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "server.py exited: %s" % self.proc.stdout.read()
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server.py 启动超时")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc is not None and self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc = None


class ApiTestCase(unittest.TestCase):
    """每个用例独享：临时目录 + 临时 SQLite + 随机端口线程服务。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="issue-board-selftest-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.db_path = os.path.join(self.tmpdir, "items.db")
        self.server = ThreadServer(self.db_path).start()
        self.addCleanup(self.server.stop)

    # ---- 请求助手 ----
    def get(self, path):
        return http_request("GET", self.server.base_url + path)

    def post(self, path, body=None, raw=None):
        return http_request("POST", self.server.base_url + path, body=body, raw_body=raw)

    def put(self, path, body=None, raw=None):
        return http_request("PUT", self.server.base_url + path, body=body, raw_body=raw)

    def delete(self, path):
        return http_request("DELETE", self.server.base_url + path)

    def create_item(self, title="测试事项", status=None, priority=None):
        body = {"title": title}
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        status_code, _, payload = self.post("/api/items", body)
        self.assertEqual(status_code, 201, payload)
        return payload

    def assertError(self, result, http_status, code):
        status_code, _, payload = result
        self.assertEqual(status_code, http_status, payload)
        self.assertIsInstance(payload, dict, "错误响应必须是 JSON 对象")
        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["code"], code)
        self.assertTrue(payload["error"].get("message"), "错误必须带可读 message")


class TestCreateItem(ApiTestCase):
    """验收清单 #1 #6 #7 #8 #13（POST 部分）。"""

    def test_create_ok_and_title_stripped(self):
        st, headers, item = self.post(
            "/api/items",
            {"title": "  写周报  ", "status": "in_progress", "priority": "high"},
        )
        self.assertEqual(st, 201)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIsInstance(item["id"], int)
        self.assertEqual(item["title"], "写周报")
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["priority"], "high")
        self.assertRegex(item["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(item["created_at"], item["updated_at"])

    def test_create_defaults(self):
        st, _, item = self.post("/api/items", {"title": "默认值"})
        self.assertEqual(st, 201)
        self.assertEqual(item["status"], "todo")
        self.assertEqual(item["priority"], "medium")

    def test_blank_title_rejected(self):
        self.assertError(self.post("/api/items", {"title": "   "}), 400, "TITLE_REQUIRED")
        self.assertError(self.post("/api/items", {"title": ""}), 400, "TITLE_REQUIRED")
        self.assertError(self.post("/api/items", {"title": "\t\n "}), 400, "TITLE_REQUIRED")

    def test_missing_or_bad_typed_title_rejected(self):
        self.assertError(self.post("/api/items", {"status": "todo"}), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", {"title": 123}), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", {"title": None}), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", {"title": ["x"]}), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", {}), 400, "INVALID_INPUT")

    def test_malformed_body_rejected(self):
        self.assertError(self.post("/api/items", raw="{not json"), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", raw=""), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", raw='["list"]'), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/items", raw='"str"'), 400, "INVALID_INPUT")

    def test_title_length_boundary_80_ok_81_rejected(self):
        st, _, item = self.post("/api/items", {"title": "a" * 80})
        self.assertEqual(st, 201)
        self.assertEqual(item["title"], "a" * 80)
        self.assertError(self.post("/api/items", {"title": "a" * 81}), 400, "TITLE_TOO_LONG")
        # strip 后计数：两端空白不计入长度
        st, _, item = self.post("/api/items", {"title": "  " + "汉" * 80 + "  "})
        self.assertEqual(st, 201)
        self.assertEqual(item["title"], "汉" * 80)
        self.assertError(self.post("/api/items", {"title": "汉" * 81}), 400, "TITLE_TOO_LONG")
        # emoji 按 1 字符计
        st, _, item = self.post("/api/items", {"title": "\U0001f642" * 80})
        self.assertEqual(st, 201)
        self.assertError(self.post("/api/items", {"title": "\U0001f642" * 81}), 400, "TITLE_TOO_LONG")

    def test_invalid_status_and_priority(self):
        self.assertError(
            self.post("/api/items", {"title": "x", "status": "finished"}),
            400, "INVALID_STATUS")
        self.assertError(
            self.post("/api/items", {"title": "x", "status": "TODO"}),
            400, "INVALID_STATUS")
        self.assertError(
            self.post("/api/items", {"title": "x", "priority": "urgent"}),
            400, "INVALID_PRIORITY")


class TestGetUpdateDelete(ApiTestCase):
    """验收清单 #3 #4 #5 #9（单条/更新/删除/错误 id）。"""

    def test_get_single(self):
        item = self.create_item("单条读取")
        st, _, got = self.get("/api/items/%d" % item["id"])
        self.assertEqual(st, 200)
        self.assertEqual(got, item)

    def test_invalid_id_forms(self):
        for bad in ("abc", "0", "-1", "1.5", "1e2", " 1"):
            self.assertError(self.get("/api/items/" + urllib.parse.quote(bad)),
                             400, "INVALID_ID")
            self.assertError(self.put("/api/items/" + urllib.parse.quote(bad), {"title": "x"}),
                             400, "INVALID_ID")
            self.assertError(self.delete("/api/items/" + urllib.parse.quote(bad)),
                             400, "INVALID_ID")

    def test_not_found(self):
        self.assertError(self.get("/api/items/999999"), 404, "ITEM_NOT_FOUND")
        self.assertError(self.put("/api/items/999999", {"title": "x"}), 404, "ITEM_NOT_FOUND")
        self.assertError(self.delete("/api/items/999999"), 404, "ITEM_NOT_FOUND")

    def test_update_full_fields(self):
        item = self.create_item("原标题", status="todo", priority="low")
        time.sleep(1.05)  # updated_at 秒级精度，确保可见变化
        st, _, updated = self.put(
            "/api/items/%d" % item["id"],
            {"title": " 新标题 ", "status": "done", "priority": "high"},
        )
        self.assertEqual(st, 200)
        self.assertEqual(updated["id"], item["id"])
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["priority"], "high")
        self.assertEqual(updated["created_at"], item["created_at"])
        self.assertNotEqual(updated["updated_at"], item["updated_at"])

    def test_update_partial_keeps_other_fields(self):
        item = self.create_item("部分更新", status="todo", priority="high")
        st, _, updated = self.put("/api/items/%d" % item["id"], {"status": "in_progress"})
        self.assertEqual(st, 200)
        self.assertEqual(updated["title"], "部分更新")
        self.assertEqual(updated["priority"], "high")
        self.assertEqual(updated["status"], "in_progress")

    def test_update_empty_object_ok(self):
        item = self.create_item("空对象更新")
        st, _, got = self.put("/api/items/%d" % item["id"], {})
        self.assertEqual(st, 200)
        self.assertEqual(got["id"], item["id"])
        self.assertEqual(got["title"], item["title"])
        self.assertEqual(got["status"], item["status"])
        self.assertEqual(got["priority"], item["priority"])

    def test_update_validation_same_as_create(self):
        item = self.create_item("校验一致")
        url = "/api/items/%d" % item["id"]
        self.assertError(self.put(url, {"title": "   "}), 400, "TITLE_REQUIRED")
        self.assertError(self.put(url, {"title": "b" * 81}), 400, "TITLE_TOO_LONG")
        self.assertError(self.put(url, {"status": "x"}), 400, "INVALID_STATUS")
        self.assertError(self.put(url, {"priority": "x"}), 400, "INVALID_PRIORITY")
        self.assertError(self.put(url, {"title": 5}), 400, "INVALID_INPUT")
        self.assertError(self.put(url, raw="{bad json"), 400, "INVALID_INPUT")
        # 失败请求不得破坏原数据
        st, _, got = self.get(url)
        self.assertEqual(st, 200)
        self.assertEqual(got["title"], "校验一致")

    def test_delete_flow_and_double_delete(self):
        item = self.create_item("待删除")
        st, _, payload = self.delete("/api/items/%d" % item["id"])
        self.assertEqual(st, 200)
        self.assertEqual(payload, {"success": True, "id": item["id"]})
        self.assertError(self.get("/api/items/%d" % item["id"]), 404, "ITEM_NOT_FOUND")
        self.assertError(self.delete("/api/items/%d" % item["id"]), 404, "ITEM_NOT_FOUND")

    def test_method_not_allowed(self):
        self.assertError(self.delete("/api/items"), 405, "METHOD_NOT_ALLOWED")
        item = self.create_item("方法检查")
        self.assertError(self.post("/api/items/%d" % item["id"], {"title": "x"}),
                         405, "METHOD_NOT_ALLOWED")

    def test_unknown_api_path_404(self):
        self.assertError(self.get("/api/unknown"), 404, "NOT_FOUND")
        self.assertError(self.get("/api/items/1/extra"), 404, "NOT_FOUND")


class TestListSearchFilterPagination(ApiTestCase):
    """验收清单 #2 #10 #11（列表/搜索/筛选/分页）。"""

    def test_list_shape_and_default_order(self):
        first = self.create_item("第一条")
        second = self.create_item("第二条")
        st, _, payload = self.get("/api/items")
        self.assertEqual(st, 200)
        self.assertEqual(
            set(payload.keys()),
            {"items", "page", "page_size", "total", "total_pages"},
        )
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 10)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["total_pages"], 1)
        self.assertEqual(
            [i["id"] for i in payload["items"]],
            [second["id"], first["id"]],  # 新建在前
        )

    def test_empty_list_shape(self):
        st, _, payload = self.get("/api/items")
        self.assertEqual(st, 200)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["total_pages"], 0)

    def test_keyword_case_insensitive_substring(self):
        self.create_item("Hello World")
        self.create_item("周报 2026")
        st, _, payload = self.get("/api/items?keyword=WORLD")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "Hello World")
        st, _, payload = self.get("/api/items?keyword=" + urllib.parse.quote("ello wor"))
        self.assertEqual(payload["total"], 1)
        st, _, payload = self.get("/api/items?keyword=" + urllib.parse.quote("周报"))
        self.assertEqual(payload["total"], 1)
        st, _, payload = self.get("/api/items?keyword=zzz-not-exists")
        self.assertEqual(st, 200)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)
        # LIKE 通配符按字面处理
        self.create_item("100%_完成")
        st, _, payload = self.get("/api/items?keyword=" + urllib.parse.quote("%_"))
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "100%_完成")

    def test_status_filter_and_and_combination(self):
        self.create_item("Alpha todo", status="todo")
        self.create_item("Alpha done", status="done")
        self.create_item("Beta done", status="done")
        st, _, payload = self.get("/api/items?status=done")
        self.assertEqual(st, 200)
        self.assertEqual(payload["total"], 2)
        self.assertTrue(all(i["status"] == "done" for i in payload["items"]))
        st, _, payload = self.get(
            "/api/items?status=done&keyword=" + urllib.parse.quote("alpha"))
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "Alpha done")
        self.assertError(self.get("/api/items?status=finished"), 400, "INVALID_STATUS")

    def test_pagination_23_items(self):
        for n in range(23):
            st, _, _ = self.post("/api/items", {"title": "分页数据 %02d" % n})
            self.assertEqual(st, 201)
        st, _, p1 = self.get("/api/items?page=1&page_size=10")
        self.assertEqual(st, 200)
        self.assertEqual(p1["total"], 23)
        self.assertEqual(p1["total_pages"], 3)
        self.assertEqual(len(p1["items"]), 10)
        st, _, p3 = self.get("/api/items?page=3&page_size=10")
        self.assertEqual(st, 200)
        self.assertEqual(len(p3["items"]), 3)
        st, _, p4 = self.get("/api/items?page=4&page_size=10")
        self.assertEqual(st, 200)  # 超出总页数不是错误
        self.assertEqual(p4["items"], [])
        self.assertEqual(p4["total"], 23)
        # 三页合起来无重复、无遗漏
        ids = []
        for page in (1, 2, 3):
            st, _, pp = self.get("/api/items?page=%d&page_size=10" % page)
            ids.extend(i["id"] for i in pp["items"])
        self.assertEqual(len(ids), 23)
        self.assertEqual(len(set(ids)), 23)

    def test_invalid_paging_params(self):
        for bad in ("page=0", "page=-1", "page=abc", "page=1.5",
                    "page_size=0", "page_size=101", "page_size=abc", "page_size=-5"):
            self.assertError(self.get("/api/items?%s" % bad), 400, "INVALID_PAGE")

    def test_valid_paging_boundaries(self):
        self.create_item("边界")
        st, _, payload = self.get("/api/items?page_size=100")
        self.assertEqual(st, 200)
        self.assertEqual(payload["page_size"], 100)
        st, _, payload = self.get("/api/items?page_size=1&page=1")
        self.assertEqual(st, 200)
        self.assertEqual(payload["total_pages"], 1)


class TestStaticFrontend(ApiTestCase):
    """静态页面路由与前端源码级防 XSS 检查。"""

    def test_index_served_at_root(self):
        st, headers, _ = http_request("GET", self.server.base_url + "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_static_assets(self):
        for path, ctype in (("/style.css", "text/css"), ("/app.js", "javascript")):
            st, headers, _ = http_request("GET", self.server.base_url + path)
            self.assertEqual(st, 200)
            self.assertIn(ctype, headers.get("Content-Type", ""))
            st, headers, _ = http_request("GET", self.server.base_url + "/static" + path)
            self.assertEqual(st, 200)

    def test_unknown_static_404_json(self):
        st, _, payload = http_request("GET", self.server.base_url + "/nope.html")
        self.assertEqual(st, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_path_traversal_blocked(self):
        st, _, _ = http_request("GET", self.server.base_url + "/static/../server.py")
        self.assertEqual(st, 404)

    def test_frontend_source_is_xss_safe(self):
        index_path = os.path.join(CASE_DIR, "static", "index.html")
        app_path = os.path.join(CASE_DIR, "static", "app.js")
        with open(index_path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('name="viewport"', html)
        with open(app_path, encoding="utf-8") as fh:
            js = fh.read()
        self.assertNotIn("innerHTML", js)          # 禁止 HTML 拼接
        self.assertNotIn("document.write", js)
        self.assertIn("textContent", js)           # 用户文本纯文字渲染
        self.assertIn("/api/items", js)            # 相对路径请求


class TestPersistenceAcrossRestart(ApiTestCase):
    """验收清单 #12：真实子进程重启后数据完整保留。"""

    def test_data_survives_process_restart(self):
        db_path = os.path.join(self.tmpdir, "restart.db")
        srv1 = SubprocessServer(db_path)
        srv1.start()
        self.addCleanup(srv1.stop)
        created = []
        for title in ("重启前A", "重启前B"):
            st, _, item = http_request("POST", srv1.base_url + "/api/items",
                                       body={"title": title})
            self.assertEqual(st, 201)
            created.append(item)
        st, _, payload = http_request("PUT", srv1.base_url + "/api/items/%d" % created[0]["id"],
                                      body={"status": "done"})
        self.assertEqual(st, 200)
        srv1.stop()  # kill 服务进程

        srv2 = SubprocessServer(db_path)  # 同一 DB 文件重启新进程
        srv2.start()
        self.addCleanup(srv2.stop)
        st, _, payload = http_request("GET", srv2.base_url + "/api/items")
        self.assertEqual(st, 200)
        self.assertEqual(payload["total"], 2)
        by_id = {i["id"]: i for i in payload["items"]}
        self.assertEqual(set(by_id), {c["id"] for c in created})
        self.assertEqual(by_id[created[0]["id"]]["title"], "重启前A")
        self.assertEqual(by_id[created[0]["id"]]["status"], "done")
        self.assertEqual(by_id[created[1]["id"]]["title"], "重启前B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
