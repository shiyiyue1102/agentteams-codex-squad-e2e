#!/usr/bin/env python3
"""开发自测脚本（仅 Python 标准库，自动使用临时数据库，结束后自动清理）。

自动在系统临时目录创建一次性 SQLite 数据库，用随机可用端口启动 server.py，
覆盖分析报告 §5 中可由 API 验证的组别：

- A2  静态资源与首页可达、前端无外部资源、app.js 不使用 HTML 注入 API
- B1–B5  CRUD 正常路径、标题去空白、缺省枚举、时间戳格式
- C1–C7 标题必填/80-81 边界、非法枚举、非法 JSON/非对象/PUT 空对象、
        404 与 405、统一错误体格式、page/page_size/status 参数校验
- D1–D4 关键词搜索（大小写不敏感、LIKE 通配符转义）、状态筛选、AND 组合、
        25 条数据分页、越界页、排序稳定性
- E2  服务进程重启（同一 --db 文件）后数据完整保留

F 组（真实浏览器 UI/XSS/375px）由 testing 节点用 browser-dev-team 独立验证。

用法：
    python3 selftest.py        # 汇总输出
    python3 selftest.py -v     # 逐条输出每个用例

退出码 0 表示全部通过；1 表示存在失败。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(BASE_DIR, "server.py")
STATIC_DIR = os.path.join(BASE_DIR, "static")
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

VERBOSE = "-v" in sys.argv[1:]
PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        if VERBOSE:
            print("  pass  %s" % name)
    else:
        FAILED.append("%s  %s" % (name, detail))
        print("  FAIL  %s  %s" % (name, detail))


def http(method, base, path, body=None, raw_body=None):
    """发送请求，返回 (status, parsed_json_or_None, raw_bytes)。4xx/5xx 不抛异常。"""
    data = raw_body
    if data is None and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        payload = err.read()
        status = err.code
    parsed = None
    if payload:
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except ValueError:
            parsed = None
    return status, parsed, payload


def is_error_body(parsed):
    """§3 统一错误体：{"error": {"code": 非空, "message": 非空, "field"?}}。"""
    if not isinstance(parsed, dict):
        return False
    err = parsed.get("error")
    if not isinstance(err, dict):
        return False
    return bool(err.get("code")) and bool(err.get("message"))


def err_ok(status, parsed, expect_status, code, field=None):
    if status != expect_status or not is_error_body(parsed):
        return False
    if parsed["error"]["code"] != code:
        return False
    if field is not None and parsed["error"].get("field") != field:
        return False
    return True


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """一次性服务实例：随机端口 + 临时数据库 + 日志文件。"""

    def __init__(self, workdir, name, db_path=None):
        self.dir = os.path.join(workdir, name)
        os.makedirs(self.dir, exist_ok=True)
        self.db_path = db_path or os.path.join(self.dir, "items.db")
        self.log_path = os.path.join(self.dir, "server.log")
        self.proc = None
        self.log_file = None
        self.base = None

    def start(self):
        port = _free_port()
        self.log_file = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-B",
                SERVER_PY,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--db",
                self.db_path,
            ],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            cwd=BASE_DIR,
        )
        self.base = "http://127.0.0.1:%d" % port
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "server exited early rc=%s log=%s"
                    % (self.proc.returncode, self.log_path)
                )
            try:
                with urllib.request.urlopen(self.base + "/api/items", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.15)
        raise RuntimeError("server not ready, log=%s" % self.log_path)

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.proc = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None


# ---------------------------------------------------------------- A2 静态资源

def test_static(base):
    status, _, payload = http("GET", base, "/")
    html = payload.decode("utf-8", "replace")
    check("A2 GET / 返回 200 HTML 页面", status == 200 and "<title>事项管理</title>" in html,
          "status=%s" % status)
    check("A2 首页包含 viewport meta", 'name="viewport"' in html)
    for path in ("/app.js", "/style.css", "/static/index.html"):
        status, _, payload = http("GET", base, path)
        check("A2 静态资源 %s 返回 200" % path, status == 200 and len(payload) > 0,
              "status=%s" % status)
    status, _, _ = http("GET", base, "/definitely-missing.html")
    check("A2 未知静态路径返回 404", status == 404, "status=%s" % status)
    for fname in ("index.html", "app.js", "style.css"):
        with open(os.path.join(STATIC_DIR, fname), encoding="utf-8") as fh:
            source = fh.read()
        check("前端 %s 无外部资源引用" % fname, "://" not in source)
    with open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8") as fh:
        check("前端 app.js 不使用 innerHTML（XSS 防护）", "innerHTML" not in fh.read())


# ---------------------------------------------------------------- B 组 CRUD

def test_crud(base):
    status, created, _ = http("POST", base, "/api/items",
                              body={"title": "  你好，事项  "})
    ok = status == 201 and isinstance(created, dict) and isinstance(created.get("id"), int)
    check("B1 POST 合法事项返回 201 + 完整 Item", ok, "status=%s body=%r" % (status, created))
    if not ok:
        return
    item_id = created["id"]
    check("B5 标题去两端空白后存储", created.get("title") == "你好，事项",
          repr(created.get("title")))
    check("B1 缺省 status=todo / priority=medium",
          created.get("status") == "todo" and created.get("priority") == "medium")
    check("B1 created_at/updated_at 为 UTC ISO-8601",
          bool(ISO_TS_RE.match(str(created.get("created_at", ""))))
          and bool(ISO_TS_RE.match(str(created.get("updated_at", "")))))

    status, got, _ = http("GET", base, "/api/items/%d" % item_id)
    check("B2 GET /api/items/{id} 与创建结果一致", status == 200 and got == created,
          "status=%s" % status)

    time.sleep(1.05)  # 保证 updated_at 秒级可见变化
    status, updated, _ = http("PUT", base, "/api/items/%d" % item_id,
                              body={"status": "done"})
    check("B3 PUT 部分更新返回 200 且仅改指定字段",
          status == 200 and isinstance(updated, dict)
          and updated.get("status") == "done"
          and updated.get("title") == "你好，事项"
          and updated.get("priority") == "medium")
    check("B3 updated_at 已刷新",
          isinstance(updated, dict) and updated.get("updated_at", "") > created["created_at"],
          "%r vs %r" % (updated.get("updated_at") if isinstance(updated, dict) else None,
                        created["created_at"]))

    status, updated2, _ = http("PUT", base, "/api/items/%d" % item_id,
                               body={"title": " 新标题 ", "priority": "high"})
    check("B3 PUT 多字段合并更新（title+priority，status 保持 done）",
          status == 200 and isinstance(updated2, dict)
          and updated2.get("title") == "新标题"
          and updated2.get("priority") == "high"
          and updated2.get("status") == "done")

    status, _, payload = http("DELETE", base, "/api/items/%d" % item_id)
    check("B4 DELETE 返回 204 且无响应体", status == 204 and payload == b"",
          "status=%s body=%r" % (status, payload))
    status, parsed, _ = http("GET", base, "/api/items/%d" % item_id)
    check("B4 删除后 GET 同 id 返回 404 NOT_FOUND",
          err_ok(status, parsed, 404, "NOT_FOUND"))


# ---------------------------------------------------------------- C 组 错误边界

def test_title_rules(base):
    title80 = "字" * 80
    status, created, _ = http("POST", base, "/api/items", body={"title": title80})
    check("B5/C2 恰好 80 字符标题创建成功",
          status == 201 and isinstance(created, dict) and created.get("title") == title80,
          "status=%s" % status)
    status, padded, _ = http("POST", base, "/api/items",
                             body={"title": "  " + title80 + "\n\t "})
    check("B5 80 字符 + 两端空白仍成功且存储去空白值",
          status == 201 and isinstance(padded, dict) and padded.get("title") == title80,
          "status=%s" % status)
    status, parsed, _ = http("POST", base, "/api/items", body={"title": "x" * 81})
    check("C2 81 字符标题返回 400 VALIDATION_ERROR(field=title)",
          err_ok(status, parsed, 400, "VALIDATION_ERROR", "title"))

    cases = (
        ("缺失 title", {"priority": "low"}),
        ("空串 title", {"title": ""}),
        ("纯空白 title", {"title": "   \t\n "}),
        ("null title", {"title": None}),
        ("非字符串 title", {"title": 123}),
    )
    for name, body in cases:
        status, parsed, _ = http("POST", base, "/api/items", body=body)
        check("C1 POST %s 返回 400 VALIDATION_ERROR(field=title)" % name,
              err_ok(status, parsed, 400, "VALIDATION_ERROR", "title"),
              "status=%s body=%r" % (status, parsed))


def test_enums(base):
    for field, bad in (("status", "finished"), ("status", ""),
                       ("priority", "urgent"), ("priority", "TODO")):
        status, parsed, _ = http("POST", base, "/api/items",
                                 body={"title": "枚举校验", field: bad})
        check("C3 POST 非法 %s=%r 返回 400(field=%s)" % (field, bad, field),
              err_ok(status, parsed, 400, "VALIDATION_ERROR", field),
              "status=%s body=%r" % (status, parsed))

    status, created, _ = http("POST", base, "/api/items",
                              body={"title": "null 枚举", "status": None, "priority": None})
    check("C3 POST status/priority 为 null 时使用缺省值",
          status == 201 and isinstance(created, dict)
          and created.get("status") == "todo" and created.get("priority") == "medium",
          "status=%s" % status)
    if status == 201 and isinstance(created, dict):
        item_id = created["id"]
        for field, bad in (("status", "doing!"), ("priority", 3)):
            status, parsed, _ = http("PUT", base, "/api/items/%d" % item_id,
                                     body={field: bad})
            check("C3 PUT 非法 %s=%r 返回 400(field=%s)" % (field, bad, field),
                  err_ok(status, parsed, 400, "VALIDATION_ERROR", field),
                  "status=%s body=%r" % (status, parsed))


def test_body_errors(base):
    for name, raw in (("非法 JSON", b"{not json"), ("JSON 数组", b"[1, 2, 3]"),
                      ("JSON 标量", b'"hello"'), ("空请求体", b"")):
        status, parsed, _ = http("POST", base, "/api/items", raw_body=raw)
        check("C4 POST %s 返回 400 VALIDATION_ERROR" % name,
              err_ok(status, parsed, 400, "VALIDATION_ERROR"),
              "status=%s body=%r" % (status, parsed))
    status, parsed, _ = http("PUT", base, "/api/items/999999", raw_body=b"{}")
    check("C4 PUT 空对象返回 400（字段校验优先于存在性）",
          err_ok(status, parsed, 400, "VALIDATION_ERROR"),
          "status=%s body=%r" % (status, parsed))
    status, parsed, _ = http("PUT", base, "/api/items/999999", raw_body=b"broken")
    check("C4 PUT 非法 JSON 返回 400", err_ok(status, parsed, 400, "VALIDATION_ERROR"))
    status, parsed, _ = http("PUT", base, "/api/items/999999", body={"note": "无关字段"})
    check("C4 PUT 无有效字段返回 400", err_ok(status, parsed, 400, "VALIDATION_ERROR"))


def test_not_found_and_405(base):
    for method in ("GET", "PUT", "DELETE"):
        body = {"title": "x"} if method == "PUT" else None
        status, parsed, _ = http(method, base, "/api/items/999999", body=body)
        check("C5 %s 不存在记录返回 404 NOT_FOUND" % method,
              err_ok(status, parsed, 404, "NOT_FOUND"),
              "status=%s body=%r" % (status, parsed))
    for bad_id in ("abc", "0", "-3", "1.5"):
        status, parsed, _ = http("GET", base, "/api/items/%s" % bad_id)
        check("C5 GET /api/items/%s（非正整数）按 404 处理" % bad_id,
              err_ok(status, parsed, 404, "NOT_FOUND"),
              "status=%s body=%r" % (status, parsed))
    status, parsed, _ = http("GET", base, "/api/unknown")
    check("C5 未知 /api/* 路径返回 404 NOT_FOUND", err_ok(status, parsed, 404, "NOT_FOUND"))
    status, parsed, _ = http("DELETE", base, "/api/items")
    check("405 DELETE /api/items 返回 METHOD_NOT_ALLOWED",
          err_ok(status, parsed, 405, "METHOD_NOT_ALLOWED"),
          "status=%s body=%r" % (status, parsed))
    status, parsed, _ = http("POST", base, "/api/items/1")
    check("405 POST /api/items/{id} 返回 METHOD_NOT_ALLOWED",
          err_ok(status, parsed, 405, "METHOD_NOT_ALLOWED"),
          "status=%s body=%r" % (status, parsed))


# ---------------------------------------------------------------- D 组 搜索/筛选/分页

def test_search_filter_paging(base):
    plan = []
    for i in range(1, 11):
        plan.append(("Alpha 任务 %02d" % i, "todo", "medium"))
    for i in range(11, 21):
        plan.append(("alpha 小写 %02d" % i, "doing", "low"))
    for i in range(21, 24):
        plan.append(("Beta 任务 %02d" % i, "done", "high"))
    plan.append(("Beta 100 号任务", "done", "high"))
    plan.append(("含 100% 与 _下划线_ 的标题", "todo", "high"))
    ids = []
    for title, st, pr in plan:
        status, created, _ = http("POST", base, "/api/items",
                                  body={"title": title, "status": st, "priority": pr})
        if status != 201 or not isinstance(created, dict):
            check("D 组准备数据 POST %r" % title, False, "status=%s" % status)
            return
        ids.append(created["id"])
    check("D3 准备 25 条数据", len(ids) == 25, "实际 %d 条" % len(ids))

    status, data, _ = http("GET", base, "/api/items?page=1&page_size=10")
    check("D3 第 1 页返回 10 条且 total=25 total_pages=3 page/page_size 回显",
          status == 200 and isinstance(data, dict)
          and len(data.get("items", [])) == 10
          and data.get("total") == 25 and data.get("total_pages") == 3
          and data.get("page") == 1 and data.get("page_size") == 10,
          "status=%s body=%r" % (status, data))

    page_ids = []
    for page, expect_len in ((1, 10), (2, 10), (3, 5)):
        status, data, _ = http("GET", base, "/api/items?page=%d&page_size=10" % page)
        items = data.get("items", []) if isinstance(data, dict) else []
        check("D3 第 %d 页返回 %d 条" % (page, expect_len),
              status == 200 and len(items) == expect_len,
              "status=%s len=%d" % (status, len(items)))
        page_ids.extend(it["id"] for it in items)
    check("D4 三页合计无重复、无遗漏", sorted(page_ids) == sorted(ids),
          "分页 ids=%r" % sorted(page_ids))

    status, data, _ = http("GET", base, "/api/items?page=1&page_size=100")
    items = data.get("items", []) if isinstance(data, dict) else []
    ordered = all(
        (items[i]["created_at"], items[i]["id"])
        >= (items[i + 1]["created_at"], items[i + 1]["id"])
        for i in range(len(items) - 1)
    )
    check("D4 固定排序 created_at DESC, id DESC",
          status == 200 and len(items) == 25 and ordered)

    status, data, _ = http("GET", base, "/api/items?page=4&page_size=10")
    check("D3 越界页（page=4）返回 200 空数组且 total 正确",
          status == 200 and isinstance(data, dict) and data.get("items") == []
          and data.get("total") == 25 and data.get("total_pages") == 3,
          "status=%s body=%r" % (status, data))

    q = urllib.parse.quote
    status, data, _ = http("GET", base, "/api/items?q=%s&page_size=100" % q("ALPHA"))
    check("D1 q 大小写不敏感子串匹配（ALPHA → 20 条）",
          status == 200 and isinstance(data, dict) and data.get("total") == 20,
          "status=%s total=%r" % (status, data.get("total") if isinstance(data, dict) else None))
    status, data, _ = http("GET", base, "/api/items?q=%s&page_size=100" % q("  alpha  "))
    check("D1 q 去两端空白后匹配", status == 200 and data.get("total") == 20)
    status, data, _ = http("GET", base, "/api/items?q=%s&page_size=100" % q("100"))
    check("D1 q=100 命中 2 条（数据准备核对）", status == 200 and data.get("total") == 2)
    status, data, _ = http("GET", base, "/api/items?q=%s&page_size=100" % q("100%"))
    check("D1 LIKE 通配符 % 按字面匹配（q=100%% 仅 1 条）",
          status == 200 and data.get("total") == 1,
          "total=%r" % (data.get("total") if isinstance(data, dict) else None))
    status, data, _ = http("GET", base, "/api/items?q=%s&page_size=100" % q("_"))
    check("D1 LIKE 通配符 _ 按字面匹配（q=_ 仅 1 条，未转义会命中全部）",
          status == 200 and data.get("total") == 1,
          "total=%r" % (data.get("total") if isinstance(data, dict) else None))
    status, data, _ = http("GET", base, "/api/items?q=%s" % q("不存在的关键词"))
    check("D1 无匹配返回 200：items=[] total=0 total_pages=0",
          status == 200 and isinstance(data, dict) and data.get("items") == []
          and data.get("total") == 0 and data.get("total_pages") == 0)

    status, data, _ = http("GET", base, "/api/items?status=doing&page_size=100")
    check("D2 status=doing 只返回该状态（10 条）",
          status == 200 and isinstance(data, dict) and data.get("total") == 10
          and all(it["status"] == "doing" for it in data.get("items", [])))
    status, data, _ = http("GET", base, "/api/items?q=alpha&status=doing&page_size=100")
    check("D2 q+status 为 AND 语义（alpha & doing → 10 条）",
          status == 200 and data.get("total") == 10)
    status, data, _ = http("GET", base, "/api/items?q=alpha&status=done&page_size=100")
    check("D2 q+status 无交集返回空结果",
          status == 200 and data.get("total") == 0 and data.get("items") == [])

    bad_params = ("page=0", "page=-1", "page=abc", "page=1.5",
                  "page_size=0", "page_size=101", "page_size=abc", "status=xxx")
    for qs in bad_params:
        status, parsed, _ = http("GET", base, "/api/items?" + qs)
        field = qs.split("=", 1)[0]
        check("C6 %s 返回 400 VALIDATION_ERROR(field=%s)" % (qs, field),
              err_ok(status, parsed, 400, "VALIDATION_ERROR", field),
              "status=%s body=%r" % (status, parsed))
    status, data, _ = http("GET", base, "/api/items?page_size=100")
    check("C6 page_size=100 上边界合法（返回全部 25 条）",
          status == 200 and isinstance(data, dict) and len(data.get("items", [])) == 25)
    status, data, _ = http("GET", base, "/api/items?page=25&page_size=1")
    check("D3 page_size=1 时第 25 页返回 1 条 total_pages=25",
          status == 200 and isinstance(data, dict)
          and len(data.get("items", [])) == 1 and data.get("total_pages") == 25)


# ---------------------------------------------------------------- E2 持久化（重启）

def test_persistence(workdir):
    srv = Server(workdir, "persist")
    srv.start()
    created_items = []
    try:
        for title, st in (("持久化 A", "todo"), ("持久化 B", "doing"), ("持久化 C", "done")):
            status, created, _ = http("POST", srv.base, "/api/items",
                                      body={"title": title, "status": st})
            if status != 201 or not isinstance(created, dict):
                check("E2 准备持久化数据", False, "status=%s" % status)
                return
            created_items.append(created)
        check("E2 准备 3 条持久化数据", len(created_items) == 3)
    finally:
        srv.stop()

    restarted = Server(workdir, "persist-restart", db_path=srv.db_path)
    restarted.start()
    try:
        status, data, _ = http("GET", restarted.base, "/api/items?page_size=100")
        titles = sorted(it["title"] for it in data.get("items", [])) if isinstance(data, dict) else []
        check("E2 服务重启（同一 --db 文件）后列表数据完整保留",
              status == 200 and isinstance(data, dict) and data.get("total") == 3
              and titles == sorted(("持久化 A", "持久化 B", "持久化 C")),
              "status=%s titles=%r" % (status, titles))
        for original in created_items:
            status, got, _ = http("GET", restarted.base, "/api/items/%d" % original["id"])
            check("E2 重启后 GET id=%d 字段逐项一致" % original["id"],
                  status == 200 and got == original,
                  "status=%s got=%r" % (status, got))
    finally:
        restarted.stop()


# ---------------------------------------------------------------- 主流程

def run_group(workdir, name, func):
    srv = Server(workdir, name)
    srv.start()
    try:
        func(srv.base)
    finally:
        srv.stop()


def main():
    print("selftest: python %s, server=%s" % (sys.version.split()[0], SERVER_PY))
    workdir = tempfile.mkdtemp(prefix="items-selftest-")
    try:
        run_group(workdir, "crud", lambda base: (test_static(base), test_crud(base)))
        run_group(workdir, "rules", lambda base: (
            test_title_rules(base),
            test_enums(base),
            test_body_errors(base),
            test_not_found_and_405(base),
        ))
        run_group(workdir, "query", test_search_filter_paging)
        test_persistence(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    total = len(PASSED) + len(FAILED)
    print("-" * 64)
    if FAILED:
        print("selftest FAILED: %d/%d 通过，%d 失败：" % (len(PASSED), total, len(FAILED)))
        for line in FAILED:
            print("  - %s" % line)
        return 1
    print("selftest PASSED: %d/%d 用例全部通过（临时数据库已自动清理）" % (len(PASSED), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
