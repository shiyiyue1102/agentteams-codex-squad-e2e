#!/usr/bin/env python3
"""开发自测：对 API 契约做端到端验证（标准库 unittest + 真实 HTTP 服务进程）。

运行方式（在 cases/issue-board-20261128-032037-hermes/ 目录下）：
    python3 -m unittest discover -s tests -v
"""

import json
import os
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
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
SERVER_PY = os.path.join(APP_DIR, "server.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcess:
    """在临时数据库上启动真实服务进程，用于端到端与重启持久化测试。"""

    def __init__(self, db_path=None):
        self.port = free_port()
        self.db_path = db_path or os.path.join(tempfile.mkdtemp(prefix="board-selftest-"), "test.db")
        self.proc = None
        self.base = f"http://127.0.0.1:{self.port}"

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PY, "--port", str(self.port), "--db", self.db_path, "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    out = self.proc.stdout.read().decode(errors="replace")
                    raise RuntimeError(f"服务启动失败:\n{out}")
                time.sleep(0.1)
        raise RuntimeError("服务启动超时")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def request(base, method, path, body=None, raw_body=None, headers=None):
    url = base + path
    data = None
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    def _decode(payload, headers):
        if not payload:
            return None
        ctype = (headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            return json.loads(payload)
        return payload.decode("utf-8", errors="replace")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            headers = dict(resp.headers)
            return resp.status, _decode(payload, headers), headers
    except urllib.error.HTTPError as e:
        payload = e.read()
        headers = dict(e.headers)
        return e.code, _decode(payload, headers), headers


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ServerProcess()
        cls.srv.start()
        cls.base = cls.srv.base

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def create(self, title, **kw):
        body = {"title": title}
        body.update(kw)
        status, data, _ = request(self.base, "POST", "/api/items", body=body)
        self.assertEqual(status, 201, data)
        return data

    # ---- B1 创建/详情 ----
    def test_b1_create_and_get(self):
        item = self.create("  示例事项  ", priority="high")
        self.assertEqual(item["title"], "示例事项")  # strip 后入库
        self.assertEqual(item["status"], "todo")
        self.assertEqual(item["priority"], "high")
        self.assertRegex(item["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        s, got, _ = request(self.base, "GET", f"/api/items/{item['id']}")
        self.assertEqual(s, 200)
        self.assertEqual(got, item)

    # ---- B2 搜索/筛选/分页/排序 ----
    def test_b2_search_filter_pagination(self):
        srv2 = ServerProcess()
        srv2.start()
        try:
            for i in range(12):
                request(srv2.base, "POST", "/api/items", body={"title": f"Hello 事项 {i}"})
            request(srv2.base, "POST", "/api/items", body={"title": "其他任务", "status": "done"})
            # 子串搜索不区分大小写
            s, d, _ = request(srv2.base, "GET", "/api/items?keyword=hello&page_size=5")
            self.assertEqual(s, 200)
            self.assertEqual(d["total"], 12)
            self.assertEqual(d["total_pages"], 3)
            self.assertEqual(len(d["items"]), 5)
            ids = [it["id"] for it in d["items"]]
            self.assertEqual(ids, sorted(ids, reverse=True))  # id DESC
            # status 精确筛选 + keyword 组合（URL 编码中文）
            s, d, _ = request(srv2.base, "GET", "/api/items?status=done&keyword=" + urllib.parse.quote("其他"))
            self.assertEqual(d["total"], 1)
            # 非法 status
            s, d, _ = request(srv2.base, "GET", "/api/items?status=TODO")
            self.assertEqual(s, 400)
            self.assertEqual(d["error"]["code"], "INVALID_STATUS")
            # 分页越界 → 200 空列表
            s, d, _ = request(srv2.base, "GET", "/api/items?page=99")
            self.assertEqual(s, 200)
            self.assertEqual(d["items"], [])
        finally:
            srv2.stop()

    # ---- B3 PUT 部分更新 ----
    def test_b3_partial_update(self):
        item = self.create("原始标题")
        time.sleep(1.05)  # 保证 updated_at 秒级变化
        s, d, _ = request(self.base, "PUT", f"/api/items/{item['id']}", body={"status": "in_progress"})
        self.assertEqual(s, 200)
        self.assertEqual(d["status"], "in_progress")
        self.assertEqual(d["title"], "原始标题")
        self.assertEqual(d["created_at"], item["created_at"])
        self.assertNotEqual(d["updated_at"], item["updated_at"])

    # ---- B4 DELETE ----
    def test_b4_delete(self):
        item = self.create("待删除")
        s, d, hdrs = request(self.base, "DELETE", f"/api/items/{item['id']}")
        self.assertEqual(s, 204)
        self.assertIsNone(d)
        s, d, _ = request(self.base, "GET", f"/api/items/{item['id']}")
        self.assertEqual(s, 404)
        self.assertEqual(d["error"]["code"], "ITEM_NOT_FOUND")
        s, d, _ = request(self.base, "DELETE", f"/api/items/{item['id']}")
        self.assertEqual(s, 404)

    # ---- B5 标题边界 ----
    def test_b5_title_boundaries(self):
        s, d, _ = request(self.base, "POST", "/api/items", body={})
        self.assertEqual((s, d["error"]["code"]), (400, "TITLE_REQUIRED"))
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "   "})
        self.assertEqual((s, d["error"]["code"]), (400, "TITLE_REQUIRED"))
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "\u3000abc\u3000"})
        self.assertEqual((s, d["title"]), (201, "abc"))  # 全角空格 strip
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "汉" * 80})
        self.assertEqual(s, 201)
        self.assertEqual(len(d["title"]), 80)
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "汉" * 81})
        self.assertEqual((s, d["error"]["code"]), (400, "TITLE_TOO_LONG"))
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": 123})
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_INPUT"))

    # ---- B6 非法枚举 ----
    def test_b6_invalid_enums(self):
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "x", "status": "TODO"})
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_STATUS"))
        s, d, _ = request(self.base, "POST", "/api/items", body={"title": "x", "priority": "urgent"})
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_PRIORITY"))

    # ---- B7 错误输入 ----
    def test_b7_bad_input(self):
        s, d, _ = request(self.base, "POST", "/api/items", raw_body="not json")
        self.assertEqual((s, d["error"]["code"]), (400, "JSON_PARSE_ERROR"))
        s, d, _ = request(self.base, "POST", "/api/items", raw_body=b"")
        self.assertEqual((s, d["error"]["code"]), (400, "JSON_PARSE_ERROR"))
        s, d, _ = request(self.base, "POST", "/api/items", raw_body="[1,2]")
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_INPUT"))
        for q in ("page=0", "page=-1", "page=abc", "page_size=0", "page_size=101"):
            s, d, _ = request(self.base, "GET", f"/api/items?{q}")
            self.assertEqual((s, d["error"]["code"]), (400, "INVALID_INPUT"), q)
        item = self.create("put空对象")
        s, d, _ = request(self.base, "PUT", f"/api/items/{item['id']}", body={})
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_INPUT"))
        s, d, _ = request(self.base, "GET", "/api/items/abc")
        self.assertEqual((s, d["error"]["code"]), (400, "INVALID_INPUT"))

    # ---- B8 不存在记录 / 路由 ----
    def test_b8_not_found_routes(self):
        for m in ("GET", "PUT", "DELETE"):
            s, d, _ = request(self.base, m, "/api/items/999999", body=({"title": "x"} if m == "PUT" else None))
            self.assertEqual((s, d["error"]["code"]), (404, "ITEM_NOT_FOUND"), m)
        s, d, hdrs = request(self.base, "GET", "/api/unknown")
        self.assertEqual((s, d["error"]["code"]), (404, "NOT_FOUND"))
        self.assertIn("application/json", hdrs.get("Content-Type", ""))
        s, d, _ = request(self.base, "DELETE", "/api/items")
        self.assertEqual((s, d["error"]["code"]), (405, "METHOD_NOT_ALLOWED"))
        # 404 错误体必须是 JSON（未知 API 路径已验证；不存在的 item id 同样验证）
        s, d, hdrs = request(self.base, "GET", "/api/items/999999")
        self.assertEqual(s, 404)
        self.assertIn("application/json", hdrs.get("Content-Type", ""))
        self.assertEqual(d["error"]["code"], "ITEM_NOT_FOUND")

    # ---- C2/C3 重启持久化 ----
    def test_c2_c3_restart_persistence(self):
        db_path = os.path.join(tempfile.mkdtemp(prefix="board-persist-"), "persist.db")
        srv = ServerProcess(db_path=db_path)
        srv.start()
        try:
            _, item, _ = request(srv.base, "POST", "/api/items", body={"title": "持久化事项", "priority": "high"})
            _, item2, _ = request(srv.base, "POST", "/api/items", body={"title": "将被删除"})
            request(srv.base, "DELETE", f"/api/items/{item2['id']}")
        finally:
            srv.stop()
        srv2 = ServerProcess(db_path=db_path)
        srv2.start()
        try:
            s, got, _ = request(srv2.base, "GET", f"/api/items/{item['id']}")
            self.assertEqual(s, 200)
            self.assertEqual(got, item)  # id、时间戳完全不变
            s, d, _ = request(srv2.base, "GET", f"/api/items/{item2['id']}")
            self.assertEqual(s, 404)  # 删除不复现
        finally:
            srv2.stop()

    # ---- 静态页面与并发 ----
    def test_static_and_concurrency(self):
        s, body, hdrs = request(self.base, "GET", "/")
        self.assertEqual(s, 200)
        s, _, hdrs = request(self.base, "GET", "/style.css")
        self.assertEqual(s, 200)
        self.assertIn("text/css", hdrs.get("Content-Type", ""))
        # 并发 20 个写请求：无 500、无 database is locked
        def do_post(i):
            return request(self.base, "POST", "/api/items", body={"title": f"并发事项 {i}"})
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(do_post, range(20)))
        for s, d, _ in results:
            self.assertEqual(s, 201, d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
