#!/usr/bin/env python3
"""开发自测脚本（仅 Python 标准库）。

自动选择空闲端口 + 临时数据库启动 server.py，跑通
「建 → 查 → 改 → 删 → 搜索 → 筛选 → 分页 → 错误码 → 重启持久化」冒烟断言，
结束后清理子进程与临时目录。

用法：
    python3 selftest.py

退出码 0 = 全部通过；非 0 = 有失败（打印 FAIL 明细）。
覆盖分析报告 §9A 的冒烟子集（A1–A16；完整独立验证属 testing 节点职责）。
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

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    line = ("PASS" if ok else "FAIL") + "  " + name
    if detail and not ok:
        line += "  -- " + detail
    print(line, flush=True)


def expect(name, cond, detail=""):
    record(name, cond, detail)
    return bool(cond)


# ---------------------------------------------------------------- HTTP 工具


def request(base_url, method, path, body=None, raw_body=None, timeout=10):
    """返回 (status, headers_dict, body_text)。4xx/5xx 不抛异常。"""
    url = base_url + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body.encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def jbody(payload):
    try:
        return json.loads(payload)
    except Exception:
        return None


def expect_error(name, resp, http_status, code):
    status, headers, payload = resp
    data = jbody(payload)
    ok = (
        status == http_status
        and isinstance(data, dict)
        and isinstance(data.get("error"), dict)
        and data["error"].get("code") == code
        and isinstance(data["error"].get("message"), str)
        and "application/json" in (headers.get("Content-Type") or "")
    )
    return expect(name, ok, "status=%s payload=%s" % (status, payload[:300]))


def details_field(data, field):
    details = (data.get("error") or {}).get("details") or []
    return any(isinstance(d, dict) and d.get("field") == field for d in details)


# ---------------------------------------------------------------- 服务进程


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Server:
    def __init__(self, db_path, work_dir):
        self.db_path = db_path
        self.port = free_port()
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.log_path = os.path.join(work_dir, "server-%d.log" % self.port)
        self.proc = None

    def start(self):
        log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                SERVER_PY,
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "--db", self.db_path,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                log.close()
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    raise RuntimeError("server 提前退出：%s" % fh.read()[-2000:])
            try:
                status, _, _ = request(self.base_url, "GET", "/api/items")
                if status == 200:
                    return
            except Exception:
                time.sleep(0.15)
        raise RuntimeError("server 15 秒内未就绪")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


# ---------------------------------------------------------------- 测试段


def section_api(base):
    """A1–A9、A11–A13、A15、A16：功能与错误边界冒烟。"""

    def req(method, path, **kw):
        return request(base, method, path, **kw)

    # A1 创建成功：trim + 缺省值 + 时间戳格式
    status, _, payload = req("POST", "/api/items", body={"title": "  验收事项  ", "priority": "high"})
    item = jbody(payload) or {}
    ok = (
        status == 201
        and isinstance(item.get("id"), int) and item["id"] > 0
        and item.get("title") == "验收事项"
        and item.get("status") == "todo"
        and item.get("priority") == "high"
        and bool(TIMESTAMP_RE.match(item.get("created_at") or ""))
        and bool(TIMESTAMP_RE.match(item.get("updated_at") or ""))
    )
    expect("A1 创建成功（trim/缺省 status=todo/时间戳格式）", ok, payload)
    id1 = item.get("id")

    # A2 标题为空/纯空白/缺失/非字符串
    for label, body in (
        ("空字符串", {"title": ""}),
        ("纯空白", {"title": "   "}),
        ("缺失", {}),
        ("数字类型", {"title": 123}),
    ):
        status, _, payload = req("POST", "/api/items", body=body)
        data = jbody(payload) or {}
        ok = status == 400 and (data.get("error") or {}).get("code") == "VALIDATION_ERROR" and details_field(data, "title")
        expect("A2 标题非法（%s）→ 400 VALIDATION_ERROR + details.title" % label, ok, payload)

    # A3 标题长度边界：80 → 201；81 → 400；40 个汉字 → 201 原样存取
    status, _, payload = req("POST", "/api/items", body={"title": "a" * 80})
    id80 = (jbody(payload) or {}).get("id")
    expect("A3 标题恰 80 字符 → 201", status == 201, payload)
    expect_error("A3 标题 81 字符 → 400 VALIDATION_ERROR",
                 req("POST", "/api/items", body={"title": "a" * 81}), 400, "VALIDATION_ERROR")
    cjk = "验" * 40
    status, _, payload = req("POST", "/api/items", body={"title": cjk})
    cjk_id = (jbody(payload) or {}).get("id")
    ok = status == 201
    if ok:
        status2, _, payload2 = req("GET", "/api/items/%d" % cjk_id)
        ok = status2 == 200 and (jbody(payload2) or {}).get("title") == cjk
    expect("A3 CJK 标题（40 汉字）→ 201 且原样存取", ok, payload)

    # A4 非法枚举（创建与更新）
    expect_error("A4 创建 status=finished → 400 VALIDATION_ERROR",
                 req("POST", "/api/items", body={"title": "x", "status": "finished"}), 400, "VALIDATION_ERROR")
    expect_error("A4 创建 priority=urgent → 400 VALIDATION_ERROR",
                 req("POST", "/api/items", body={"title": "x", "priority": "urgent"}), 400, "VALIDATION_ERROR")
    expect_error("A4 更新 status=finished → 400 VALIDATION_ERROR",
                 req("PUT", "/api/items/%d" % id1, body={"status": "finished"}), 400, "VALIDATION_ERROR")

    # A5 非法 JSON / 非对象
    expect_error("A5 请求体 '{bad json' → 400 BAD_REQUEST",
                 req("POST", "/api/items", raw_body="{bad json"), 400, "BAD_REQUEST")
    expect_error("A5 请求体数组 [1] → 400 BAD_REQUEST",
                 req("POST", "/api/items", raw_body="[1]"), 400, "BAD_REQUEST")
    expect_error("A5 请求体纯字符串 → 400 BAD_REQUEST",
                 req("POST", "/api/items", raw_body='"just a string"'), 400, "BAD_REQUEST")

    # A6 单条查询
    status, _, payload = req("GET", "/api/items/%d" % id1)
    data = jbody(payload) or {}
    ok = status == 200 and set(data) >= {"id", "title", "status", "priority", "created_at", "updated_at"}
    expect("A6 GET 已存在 id → 200 字段完整", ok, payload)
    expect_error("A6 GET 不存在 id=999999 → 404 NOT_FOUND",
                 req("GET", "/api/items/999999"), 404, "NOT_FOUND")
    for bad in ("abc", "0", "-1"):
        expect_error("A6 GET id=%s → 400 VALIDATION_ERROR" % bad,
                     req("GET", "/api/items/%s" % bad), 400, "VALIDATION_ERROR")

    # A7 更新成功（部分更新 + updated_at 刷新）
    time.sleep(1.1)  # 时间戳为秒级，确保 updated_at 可比
    status, _, payload = req("PUT", "/api/items/%d" % id1, body={"status": "done"})
    data = jbody(payload) or {}
    ok = (
        status == 200
        and data.get("status") == "done"
        and data.get("title") == "验收事项"
        and data.get("priority") == "high"
        and (data.get("updated_at") or "") > (data.get("created_at") or "")
    )
    expect("A7 PUT 只带 status → 200，其余字段不变，updated_at 刷新", ok, payload)
    status, _, payload = req("PUT", "/api/items/%d" % id1, body={"title": " 新标题A "})
    data = jbody(payload) or {}
    expect("A7 PUT 只带 title → 200，title 更新（trim）且 status 保持 done",
           status == 200 and data.get("title") == "新标题A" and data.get("status") == "done", payload)
    status, _, payload = req("PUT", "/api/items/%d" % id1, body={"priority": "low"})
    data = jbody(payload) or {}
    expect("A7 PUT 只带 priority → 200，priority=low 且 title 不变",
           status == 200 and data.get("priority") == "low" and data.get("title") == "新标题A", payload)

    # A8 更新错误路径
    expect_error("A8 PUT 体 {} → 400 VALIDATION_ERROR",
                 req("PUT", "/api/items/%d" % id1, body={}), 400, "VALIDATION_ERROR")
    expect_error("A8 PUT 体含非法 status → 400 VALIDATION_ERROR",
                 req("PUT", "/api/items/%d" % id1, body={"status": "nope"}), 400, "VALIDATION_ERROR")
    expect_error("A8 PUT 不存在 id → 404 NOT_FOUND",
                 req("PUT", "/api/items/999999", body={"title": "x"}), 404, "NOT_FOUND")
    expect_error("A8 PUT 体非法且 id 不存在 → 400（校验优先于 404）",
                 req("PUT", "/api/items/999999", body={}), 400, "VALIDATION_ERROR")
    status, _, payload = req("PUT", "/api/items/%d" % id1,
                             body={"title": "含忽略字段", "id": 999, "created_at": "x", "foo": 1})
    data = jbody(payload) or {}
    expect("A8 PUT 含 id/created_at/未知字段 → 忽略并按部分更新处理",
           status == 200 and data.get("title") == "含忽略字段" and data.get("id") == id1, payload)

    # A9 删除
    status, _, payload = req("POST", "/api/items", body={"title": "待删除"})
    del_id = (jbody(payload) or {}).get("id")
    status, _, payload = req("DELETE", "/api/items/%d" % del_id)
    data = jbody(payload) or {}
    expect("A9 DELETE 已存在 → 200 {id, deleted:true}",
           status == 200 and data == {"id": del_id, "deleted": True}, payload)
    expect_error("A9 删除后 GET → 404 NOT_FOUND",
                 req("GET", "/api/items/%d" % del_id), 404, "NOT_FOUND")
    expect_error("A9 重复 DELETE → 404 NOT_FOUND",
                 req("DELETE", "/api/items/%d" % del_id), 404, "NOT_FOUND")
    expect_error("A9 DELETE 不存在 id → 404 NOT_FOUND",
                 req("DELETE", "/api/items/999999"), 404, "NOT_FOUND")
    expect_error("A9 DELETE id=abc → 400 VALIDATION_ERROR",
                 req("DELETE", "/api/items/abc"), 400, "VALIDATION_ERROR")

    # A11 分页参数非法
    for qs in ("page=0", "page=-1", "page=abc", "page_size=0", "page_size=101"):
        expect_error("A11 GET /api/items?%s → 400 VALIDATION_ERROR" % qs,
                     req("GET", "/api/items?" + qs), 400, "VALIDATION_ERROR")
    expect("A11 page_size=100（上边界）→ 200",
           req("GET", "/api/items?page_size=100")[0] == 200)

    # A12 关键词搜索
    req("POST", "/api/items", body={"title": "Alpha 任务"})
    req("POST", "/api/items", body={"title": "beta 任务"})
    req("POST", "/api/items", body={"title": "买菜"})
    req("POST", "/api/items", body={"title": "100%完成_了"})
    status, _, payload = req("GET", "/api/items?q=alph")
    data = jbody(payload) or {}
    titles = [it["title"] for it in data.get("items", [])]
    expect("A12 q=alph → ASCII 大小写不敏感命中 Alpha",
           status == 200 and "Alpha 任务" in titles and all("alph" in t.lower() for t in titles), payload)
    status, _, payload = req("GET", "/api/items?q=%E4%B9%B0")  # q=买
    titles = [it["title"] for it in (jbody(payload) or {}).get("items", [])]
    expect("A12 q=买 → 命中「买菜」", status == 200 and "买菜" in titles, payload)
    status, _, payload = req("GET", "/api/items?q=%25")  # q=%
    data = jbody(payload) or {}
    titles = [it["title"] for it in data.get("items", [])]
    expect("A12 q=%% → LIKE 转义按字面量命中「100%%完成_了」",
           status == 200 and titles == ["100%完成_了"], payload)
    status, _, payload = req("GET", "/api/items?q=" + urllib.parse.quote("zz不存在的词zz"))
    data = jbody(payload) or {}
    expect("A12 q=不存在的词 → 200 items=[] total=0 total_pages=0",
           status == 200 and data.get("items") == [] and data.get("total") == 0
           and data.get("total_pages") == 0, payload)

    # A13 状态筛选与组合
    status, _, payload = req("POST", "/api/items", body={"title": "Alpha 进行中", "status": "in_progress"})
    alpha_ip = (jbody(payload) or {}).get("id")
    status, _, payload = req("GET", "/api/items?status=in_progress")
    data = jbody(payload) or {}
    ok = status == 200 and data.get("total", 0) >= 1 and all(
        it["status"] == "in_progress" for it in data.get("items", []))
    expect("A13 status=in_progress → 只返回该状态", ok, payload)
    status, _, payload = req("GET", "/api/items?status=in_progress&q=alph")
    ids = [it["id"] for it in (jbody(payload) or {}).get("items", [])]
    expect("A13 status=todo&q=alph 类组合（AND 交集）", status == 200 and ids == [alpha_ip], payload)
    expect_error("A13 status=bad → 400 VALIDATION_ERROR",
                 req("GET", "/api/items?status=bad"), 400, "VALIDATION_ERROR")

    # A15 路由/方法错误
    expect_error("A15 DELETE /api/items（集合）→ 405 METHOD_NOT_ALLOWED",
                 req("DELETE", "/api/items"), 405, "METHOD_NOT_ALLOWED")
    expect_error("A15 POST /api/items/1 → 405 METHOD_NOT_ALLOWED",
                 req("POST", "/api/items/1", body={}), 405, "METHOD_NOT_ALLOWED")
    expect_error("A15 GET /api/unknown → 404 NOT_FOUND",
                 req("GET", "/api/unknown"), 404, "NOT_FOUND")
    expect_error("A15 GET /no-such-page → 404 NOT_FOUND（JSON 信封）",
                 req("GET", "/no-such-page"), 404, "NOT_FOUND")

    # A16 XSS 载荷透传（服务端原样存储返回，不转义不拒绝）
    for xss in ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>"):
        status, _, payload = req("POST", "/api/items", body={"title": xss})
        xid = (jbody(payload) or {}).get("id")
        ok = status == 201 and (jbody(payload) or {}).get("title") == xss
        if ok:
            status2, _, payload2 = req("GET", "/api/items/%d" % xid)
            ok = status2 == 200 and (jbody(payload2) or {}).get("title") == xss
        expect("A16 XSS 载荷原样透传：%s" % xss, ok, payload)

    # 静态资源与首页
    status, headers, payload = req("GET", "/")
    expect("静态 GET / → 200 text/html 且含 <title>",
           status == 200 and "text/html" in (headers.get("Content-Type") or "") and "<title>" in payload)
    expect("静态 GET /style.css → 200 text/css",
           req("GET", "/style.css")[0] == 200
           and "text/css" in (req("GET", "/style.css")[1].get("Content-Type") or ""))
    expect("静态 GET /app.js → 200 javascript",
           req("GET", "/app.js")[0] == 200)

    # 清理本段产生的 id80 记录（避免影响后续无关断言）
    if id80:
        req("DELETE", "/api/items/%d" % id80)


def section_pagination_and_restart(work_dir):
    """A10 分页 + A14 重启持久化：使用独立临时库，数据可控。"""
    db_path = os.path.join(work_dir, "items-page.db")
    server = Server(db_path, work_dir)
    server.start()
    base = server.base_url
    try:
        created = []
        for i in range(1, 24):
            status, _, payload = request(base, "POST", "/api/items",
                                         body={"title": "分页事项-%02d" % i})
            assert status == 201, payload
            created.append(jbody(payload))
        expect("准备 23 条数据成功", len(created) == 23)

        # A10 默认分页
        status, _, payload = request(base, "GET", "/api/items")
        data = jbody(payload) or {}
        ids = [it["id"] for it in data.get("items", [])]
        ok = (
            status == 200
            and data.get("page") == 1
            and data.get("page_size") == 10
            and data.get("total") == 23
            and data.get("total_pages") == 3
            and len(data.get("items", [])) == 10
            and ids == sorted(ids, reverse=True)
        )
        expect("A10 23 条 → page=1 page_size=10 total=23 total_pages=3，10 条按 id DESC", ok, payload)

        status, _, payload = request(base, "GET", "/api/items?page=3")
        data = jbody(payload) or {}
        expect("A10 page=3 → 3 条", status == 200 and len(data.get("items", [])) == 3, payload)
        status, _, payload = request(base, "GET", "/api/items?page=4")
        data = jbody(payload) or {}
        expect("A10 page=4（越界）→ 200 items=[]",
               status == 200 and data.get("items") == [] and data.get("total") == 23, payload)

        first = created[0]
        # A14 重启持久化：停止 → 同库重启 → 数据保留
        server.stop()
        server.start()
        status, _, payload = request(server.base_url, "GET", "/api/items")
        data = jbody(payload) or {}
        expect("A14 重启后 GET /api/items → total 仍为 23",
               status == 200 and data.get("total") == 23, payload)
        status, _, payload = request(server.base_url, "GET", "/api/items/%d" % first["id"])
        expect("A14 重启后单条字段一致", status == 200 and jbody(payload) == first, payload)
    finally:
        server.stop()


def section_static_offline():
    """C3 相关静态自查：前端不引用任何外部资源；无 innerHTML 类危险用法。"""
    files = {}
    for name in ("index.html", "style.css", "app.js"):
        with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as fh:
            files[name] = fh.read()
    html, css, js = files["index.html"], files["style.css"], files["app.js"]

    external = []
    for name, text in files.items():
        for pat in (r'src\s*=\s*["\']https?:', r'href\s*=\s*["\']https?:', r'url\(\s*["\']?https?:',
                    r'@import', r'//cdn\.', r'src\s*=\s*["\']//'):
            if re.search(pat, text, re.IGNORECASE):
                external.append("%s:%s" % (name, pat))
    expect("前端无外链资源（无 CDN/外链字体/外链脚本）", not external, str(external))

    dangerous = []
    for pat in (r"\.innerHTML\s*=", r"\.outerHTML\s*=", r"insertAdjacentHTML\s*\(",
                r"document\.write\s*\(", r"\beval\s*\("):
        if re.search(pat, js) or re.search(pat, html):
            dangerous.append(pat)
    expect("前端渲染用户文本不使用 innerHTML/insertAdjacentHTML/document.write/eval",
           not dangerous, str(dangerous))
    expect("app.js 使用 textContent/createElement 渲染",
           "textContent" in js and "createElement" in js)


def main():
    work_dir = tempfile.mkdtemp(prefix="issue-board-selftest-")
    server = None
    try:
        db_path = os.path.join(work_dir, "items-main.db")
        server = Server(db_path, work_dir)
        server.start()
        section_api(server.base_url)
        server.stop()
        server = None

        section_pagination_and_restart(work_dir)
        section_static_offline()
    except Exception as exc:  # 环境或断言基础设施故障
        record("自测基础设施异常", False, repr(exc))
        traceback_print(exc)
    finally:
        if server is not None:
            server.stop()
        shutil.rmtree(work_dir, ignore_errors=True)

    total = len(RESULTS)
    failed = [name for name, ok in RESULTS if not ok]
    print("")
    print("=" * 60)
    print("自测结果：%d 项断言，通过 %d，失败 %d" % (total, total - len(failed), len(failed)))
    if failed:
        print("失败项：")
        for name in failed:
            print("  - " + name)
    print("=" * 60)
    return 0 if not failed else 1


def traceback_print(exc):
    import traceback
    traceback.print_exc()


if __name__ == "__main__":
    sys.exit(main())
