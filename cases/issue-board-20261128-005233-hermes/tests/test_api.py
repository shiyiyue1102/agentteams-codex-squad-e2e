#!/usr/bin/env python3
"""Development self-test for the Issue Board API (stdlib unittest only).

Spawns the real server on a free port with a temporary SQLite db file,
runs HTTP tests against it via urllib, then tears everything down.

Run:
    python3 -m unittest discover tests/ -v
or:
    python3 tests/test_api.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(BASE_DIR, "server.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerFixture:
    """Starts server.py against a temp db; provides request helpers."""

    def __init__(self):
        self.port = free_port()
        self.tmpdir = tempfile.mkdtemp(prefix="board-test-")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.proc = None

    def start(self, db_path=None):
        self.db_path = db_path or self.db_path
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PY,
             "--port", str(self.port),
             "--host", "127.0.0.1",
             "--db", self.db_path],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.5):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("server exited early")
                time.sleep(0.2)
        raise RuntimeError("server did not start in time")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def cleanup(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- HTTP helpers: return (status, parsed_json) ----------------------

    def request(self, method, path, body=None, raw_body=None,
                content_type="application/json; charset=utf-8"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = None
        headers = {}
        if raw_body is not None:
            data = raw_body.encode("utf-8")
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode("utf-8")
                return resp.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8")
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = {"_raw": payload}
            return e.code, parsed


class TestIssueBoardApi(unittest.TestCase):
    fx = None

    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture()
        cls.fx.start()

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()
        cls.fx.cleanup()

    def req(self, *a, **kw):
        return self.fx.request(*a, **kw)

    # ---------- A. core API ----------

    def test_a1_create_defaults_and_trim(self):
        st, body = self.req("POST", "/api/issues",
                            {"title": "  第一个事项  "})
        self.assertEqual(st, 201)
        d = body["data"]
        self.assertEqual(d["title"], "第一个事项")
        self.assertEqual(d["status"], "todo")
        self.assertEqual(d["priority"], "medium")
        self.assertIsInstance(d["id"], int)

    def test_a2_create_explicit_enums(self):
        st, body = self.req("POST", "/api/issues",
                            {"title": "显式枚举", "status": "done",
                             "priority": "high"})
        self.assertEqual(st, 201)
        self.assertEqual(body["data"]["status"], "done")
        self.assertEqual(body["data"]["priority"], "high")

    def test_a3_list_pagination_structure(self):
        st, body = self.req("GET", "/api/issues")
        self.assertEqual(st, 200)
        d = body["data"]
        for key in ("items", "page", "page_size", "total", "total_pages"):
            self.assertIn(key, d)
        self.assertEqual(d["page"], 1)
        self.assertEqual(d["page_size"], 10)

    def test_a4_keyword_search_case_insensitive(self):
        self.req("POST", "/api/issues", {"title": "Alpha Task"})
        self.req("POST", "/api/issues", {"title": "beta 任务"})
        st, body = self.req("GET", "/api/issues?keyword=alpha")
        self.assertEqual(st, 200)
        titles = [i["title"] for i in body["data"]["items"]]
        self.assertTrue(any("Alpha" in t for t in titles))
        self.assertFalse(any("beta" in t for t in titles))

    def test_a5_status_filter_and_combo(self):
        self.req("POST", "/api/issues",
                 {"title": "组合筛选X", "status": "done"})
        st, body = self.req("GET", "/api/issues?status=done")
        self.assertEqual(st, 200)
        self.assertTrue(all(i["status"] == "done"
                            for i in body["data"]["items"]))
        st, body = self.req("GET",
                            "/api/issues?status=done&keyword="
                            + urllib.parse.quote("组合筛选X"))
        self.assertEqual(st, 200)
        self.assertGreaterEqual(body["data"]["total"], 1)

    def test_a6_pagination_slice(self):
        for i in range(12):
            self.req("POST", "/api/issues", {"title": "分页事项%02d" % i})
        st, b1 = self.req("GET", "/api/issues?page=1&page_size=5")
        st2, b2 = self.req("GET", "/api/issues?page=2&page_size=5")
        self.assertEqual(st, 200)
        self.assertEqual(st2, 200)
        ids1 = {i["id"] for i in b1["data"]["items"]}
        ids2 = {i["id"] for i in b2["data"]["items"]}
        self.assertEqual(len(ids1 & ids2), 0)
        self.assertLessEqual(len(b1["data"]["items"]), 5)
        self.assertGreaterEqual(b1["data"]["total"], 12)
        self.assertEqual(b1["data"]["total_pages"],
                         -(-b1["data"]["total"] // 5))

    def test_a7_get_one(self):
        _, body = self.req("POST", "/api/issues", {"title": "单查事项"})
        iid = body["data"]["id"]
        st, got = self.req("GET", "/api/issues/%d" % iid)
        self.assertEqual(st, 200)
        self.assertEqual(got["data"]["id"], iid)
        self.assertEqual(got["data"]["title"], "单查事项")

    def test_a8_update_subset(self):
        _, body = self.req("POST", "/api/issues", {"title": "待更新"})
        iid = body["data"]["id"]
        time.sleep(0.01)
        st, upd = self.req("PUT", "/api/issues/%d" % iid,
                           {"status": "doing"})
        self.assertEqual(st, 200)
        self.assertEqual(upd["data"]["status"], "doing")
        self.assertEqual(upd["data"]["title"], "待更新")
        self.assertNotEqual(upd["data"]["updated_at"],
                            body["data"]["updated_at"])

    def test_a9_delete(self):
        _, body = self.req("POST", "/api/issues", {"title": "待删除"})
        iid = body["data"]["id"]
        st, delres = self.req("DELETE", "/api/issues/%d" % iid)
        self.assertEqual(st, 200)
        self.assertTrue(delres["data"]["deleted"])
        st, _ = self.req("GET", "/api/issues/%d" % iid)
        self.assertEqual(st, 404)

    def test_a10_empty_search_is_200(self):
        st, body = self.req("GET",
                            "/api/issues?keyword="
                            + urllib.parse.quote("绝对不存在的关键词zzz"))
        self.assertEqual(st, 200)
        self.assertEqual(body["data"]["items"], [])
        self.assertEqual(body["data"]["total"], 0)
        self.assertEqual(body["data"]["total_pages"], 0)

    # ---------- B. error boundaries ----------

    def test_b1_title_required(self):
        for payload in ({}, {"title": ""}, {"title": "   \u3000 "},
                        {"title": 123}):
            st, body = self.req("POST", "/api/issues", payload)
            self.assertEqual(st, 400, payload)
            self.assertEqual(body["error"]["code"], "TITLE_REQUIRED")

    def test_b2_title_length_boundary(self):
        st, body = self.req("POST", "/api/issues", {"title": "字" * 80})
        self.assertEqual(st, 201)
        st, body = self.req("POST", "/api/issues", {"title": "字" * 81})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "TITLE_TOO_LONG")

    def test_b3_invalid_enums(self):
        st, body = self.req("POST", "/api/issues",
                            {"title": "x", "status": "TODO"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "INVALID_STATUS")
        st, body = self.req("POST", "/api/issues",
                            {"title": "x", "priority": "urgent"})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "INVALID_PRIORITY")
        st, body = self.req("GET", "/api/issues?status=invalid")
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "INVALID_STATUS")

    def test_b4_invalid_id(self):
        for bad in ("abc", "0", "-1", "1.5"):
            for method, kw in (("GET", {}),
                               ("PUT", {"body": {"title": "x"}}),
                               ("DELETE", {})):
                st, body = self.req(method, "/api/issues/" + bad, **kw)
                self.assertEqual(st, 400, (method, bad))
                self.assertEqual(body["error"]["code"], "INVALID_ID")

    def test_b5_not_found(self):
        st, body = self.req("GET", "/api/issues/999999")
        self.assertEqual(st, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")
        st, body = self.req("PUT", "/api/issues/999999", {"title": "x"})
        self.assertEqual(st, 404)
        st, body = self.req("DELETE", "/api/issues/999999")
        self.assertEqual(st, 404)

    def test_b6_invalid_json(self):
        st, body = self.req("POST", "/api/issues", raw_body="{not json")
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "INVALID_JSON")
        st, body = self.req("PUT", "/api/issues/1", raw_body="[1,2")
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["code"], "INVALID_JSON")

    def test_b7_invalid_page_params(self):
        cases = [("page=0", "INVALID_PAGE"), ("page=abc", "INVALID_PAGE"),
                 ("page_size=0", "INVALID_PAGE_SIZE"),
                 ("page_size=101", "INVALID_PAGE_SIZE")]
        for qs, code in cases:
            st, body = self.req("GET", "/api/issues?" + qs)
            self.assertEqual(st, 400, qs)
            self.assertEqual(body["error"]["code"], code, qs)

    def test_b8_empty_update(self):
        _, body = self.req("POST", "/api/issues", {"title": "空更新基线"})
        iid = body["data"]["id"]
        st, resp = self.req("PUT", "/api/issues/%d" % iid, {"other": 1})
        self.assertEqual(st, 400)
        self.assertEqual(resp["error"]["code"], "EMPTY_UPDATE")

    def test_b9_error_shape(self):
        st, body = self.req("GET", "/api/issues/999999")
        self.assertEqual(st, 404)
        self.assertIn("error", body)
        self.assertIn("code", body["error"])
        self.assertIsInstance(body["error"]["message"], str)
        self.assertTrue(body["error"]["message"])

    def test_b10_validation_order_fields_before_404(self):
        # existing id + bad field -> 400; missing id + good field -> 404
        _, body = self.req("POST", "/api/issues", {"title": "顺序校验"})
        iid = body["data"]["id"]
        st, resp = self.req("PUT", "/api/issues/%d" % iid,
                            {"status": "nope"})
        self.assertEqual(st, 400)
        self.assertEqual(resp["error"]["code"], "INVALID_STATUS")
        st, resp = self.req("PUT", "/api/issues/999998", {"status": "done"})
        self.assertEqual(st, 404)

    def test_method_not_allowed_and_unknown_api_path(self):
        st, body = self.req("PATCH", "/api/issues")
        self.assertEqual(st, 405)
        self.assertEqual(body["error"]["code"], "METHOD_NOT_ALLOWED")
        st, body = self.req("GET", "/api/unknown")
        self.assertEqual(st, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    # ---------- static ----------

    def test_static_index_and_assets(self):
        # index returns HTML, not JSON — do a raw urlopen
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/" % self.fx.port, timeout=10) as r:
            html = r.read().decode("utf-8")
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers.get("Content-Type"))
            self.assertIn("事项管理板", html)
        for path in ("/static/style.css", "/static/app.js"):
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d%s" % (self.fx.port, path),
                    timeout=10) as r:
                self.assertEqual(r.status, 200, path)


class TestPersistenceRestart(unittest.TestCase):
    """C1: data survives server restart with the same db file."""

    def test_c1_restart_persistence(self):
        tmpdir = tempfile.mkdtemp(prefix="board-restart-")
        db_path = os.path.join(tmpdir, "persist.db")
        import shutil
        try:
            fx = ServerFixture()
            fx.db_path = db_path
            fx.start(db_path)
            st, body = fx.request("POST", "/api/issues",
                                  {"title": "重启后仍在", "priority": "high"})
            self.assertEqual(st, 201)
            iid = body["data"]["id"]
            fx.stop()

            fx2 = ServerFixture()
            fx2.port = fx.port
            fx2.db_path = db_path
            fx2.tmpdir = tmpdir
            fx2.start(db_path)
            st, body = fx2.request("GET", "/api/issues/%d" % iid)
            self.assertEqual(st, 200)
            self.assertEqual(body["data"]["title"], "重启后仍在")
            self.assertEqual(body["data"]["priority"], "high")
            fx2.stop()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
