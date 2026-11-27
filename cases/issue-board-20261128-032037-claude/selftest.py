#!/usr/bin/env python3
"""开发自测脚本（仅 Python 标准库）。

自动选择临时端口与临时 SQLite 数据库启动 server.py 子进程，通过 urllib
断言分析报告 §8 要求的覆盖点；随后以同一 --db 重启子进程验证服务重启
持久化。全部通过打印摘要并以退出码 0 结束；任一失败以非 0 退出并打印
失败详情。结束自动清理临时文件与子进程，不污染默认数据文件。

用法:
    python3 selftest.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")

PASS_COUNT = 0
FAILURES = []


def check(name, condition, detail=""):
    global PASS_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}")
    else:
        FAILURES.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def start_server(port, db_path):
    proc = subprocess.Popen(
        [sys.executable, SERVER, "--host", "127.0.0.1",
         "--port", str(port), "--db", db_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        cwd=HERE,
    )
    if not wait_ready(port):
        proc.kill()
        raise RuntimeError(f"服务未在超时内就绪（port={port}）")
    return proc


def request(port, method, path, body=None, expect_json=True):
    """返回 (status, headers, parsed_json_or_raw_bytes)。"""
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            status = resp.status
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
        resp_headers = dict(e.headers)
    parsed = raw
    if expect_json and raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError:
            pass
    return status, resp_headers, parsed


def err_code(payload):
    try:
        return payload["error"]["code"]
    except (KeyError, TypeError):
        return None


def err_message(payload):
    try:
        return payload["error"]["message"]
    except (KeyError, TypeError):
        return ""


def has_timestamp(value):
    return isinstance(value, str) and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
    )


# ---------------------------------------------------------------- 阶段 1

def phase_api(port):
    print("== 阶段 1：API / 错误边界 / 搜索筛选分页 ==")

    # 1. 创建成功：201 与字段回显、时间戳格式
    s, h, d = request(port, "POST", "/api/items",
                      {"title": "  第一个事项  ", "status": "todo", "priority": "high"})
    check("POST 创建成功返回 201", s == 201, f"status={s} body={d!r}")
    item1 = d if s == 201 else {}
    check("201 返回完整 Item（id/title/status/priority/时间戳）",
          item1.get("title") == "第一个事项"
          and item1.get("status") == "todo"
          and item1.get("priority") == "high"
          and isinstance(item1.get("id"), int)
          and has_timestamp(item1.get("created_at"))
          and has_timestamp(item1.get("updated_at")),
          f"item={item1!r}")
    check("标题 trim 后存储（去除两端空白）",
          item1.get("title") == "第一个事项", f"title={item1.get('title')!r}")
    check("创建时 created_at == updated_at",
          item1.get("created_at") == item1.get("updated_at"))
    check("JSON 响应 Content-Type 正确",
          "application/json" in h.get("Content-Type", ""),
          f"ct={h.get('Content-Type')!r}")

    # 缺省枚举
    s, h, d = request(port, "POST", "/api/items", {"title": "默认枚举事项"})
    check("POST 缺省 status=todo / priority=medium",
          s == 201 and d.get("status") == "todo" and d.get("priority") == "medium",
          f"status={s} body={d!r}")
    item2 = d if s == 201 else {}

    # 2. 标题边界：空 / 全空白 / 80 字 / 81 字
    s, h, d = request(port, "POST", "/api/items", {"title": ""})
    check("空标题返回 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")
    check("错误 message 非空（明确失败原因）", bool(err_message(d)), f"body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "   \t\n  "})
    check("全空白标题返回 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    title80 = "字" * 80
    s, h, d = request(port, "POST", "/api/items", {"title": title80})
    check("80 码点标题创建成功（201）", s == 201, f"status={s} body={d!r}")
    if s == 201:
        check("80 字标题按 Unicode 码点计（len==80 通过）",
              len(d["title"]) == 80, f"len={len(d.get('title', ''))}")

    s, h, d = request(port, "POST", "/api/items", {"title": "字" * 81})
    check("81 码点标题返回 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "emoji" + "🚀" * 75})
    check("emoji 按码点计：80 码点成功", s == 201, f"status={s} body={d!r}")

    # 3. 非法枚举 / 类型错误 / 未知字段 / 非 JSON
    s, h, d = request(port, "POST", "/api/items", {"title": "t1", "status": "TODO"})
    check("status 大小写敏感（TODO 非法）→ 400",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "t1", "status": "unknown"})
    check("非法 status → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "t1", "priority": "urgent"})
    check("非法 priority → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": 123})
    check("title 类型错误（数字）→ 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "x", "color": "red"})
    check("未知字段 → 400 VALIDATION_ERROR（message 指明字段）",
          s == 400 and err_code(d) == "VALIDATION_ERROR" and "color" in err_message(d),
          f"status={s} body={d!r}")

    s, h, raw = request(port, "POST", "/api/items", "{not json", expect_json=False)
    try:
        d = json.loads(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        d = raw
    check("非法 JSON → 400 INVALID_JSON",
          s == 400 and err_code(d) == "INVALID_JSON", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", "[1,2,3]")
    check("JSON 数组体 → 400 INVALID_JSON",
          s == 400 and err_code(d) == "INVALID_JSON", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {})
    check("缺少必填 title → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    # 4. 单条读取与 404 / 405
    s, h, d = request(port, "GET", f"/api/items/{item1.get('id', 1)}")
    check("GET 单条返回 200 与一致字段",
          s == 200 and d.get("id") == item1.get("id")
          and d.get("title") == item1.get("title"), f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items/999999")
    check("不存在记录 → 404 NOT_FOUND",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items/0")
    check("id=0 → 404 NOT_FOUND（非正整数按不存在处理）",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items/-3")
    check("id=-3 → 404 NOT_FOUND", s == 404 and err_code(d) == "NOT_FOUND",
          f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items/abc")
    check("id=abc → 404 NOT_FOUND", s == 404 and err_code(d) == "NOT_FOUND",
          f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/unknown")
    check("未知路径 → 404 NOT_FOUND（JSON 格式）",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")

    s, h, d = request(port, "POST", f"/api/items/{item1.get('id', 1)}",
                      {"title": "x"})
    check("POST /api/items/{id} → 405 METHOD_NOT_ALLOWED（带 Allow 头）",
          s == 405 and err_code(d) == "METHOD_NOT_ALLOWED"
          and "Allow" in {k.title() for k in h},
          f"status={s} headers={h!r} body={d!r}")

    # 5. PUT 更新
    iid = item1.get("id", 1)
    s, h, d = request(port, "PUT", f"/api/items/{iid}", {"status": "done"})
    check("PUT 部分更新成功（200，status 生效，title 保持）",
          s == 200 and d.get("status") == "done"
          and d.get("title") == item1.get("title"), f"status={s} body={d!r}")
    check("PUT 后 updated_at 刷新且 created_at 不变",
          has_timestamp(d.get("updated_at"))
          and d.get("created_at") == item1.get("created_at"),
          f"body={d!r}")

    s, h, d = request(port, "PUT", f"/api/items/{iid}", {})
    check("PUT 空对象（无可更新字段）→ 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "PUT", "/api/items/999999", {"title": "x"})
    check("PUT 不存在记录 → 404（优先于字段校验）",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")

    s, h, d = request(port, "PUT", f"/api/items/{iid}", {"status": "done", "title": "  "})
    check("PUT 非法 title（空白）→ 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    # 6. 列表 / 搜索 / 筛选 / 分页
    s, h, d = request(port, "GET", "/api/items")
    check("GET 列表 200 且结构完整（items/page/page_size/total/total_pages）",
          s == 200 and all(k in d for k in
                           ("items", "page", "page_size", "total", "total_pages")),
          f"status={s} body={d!r}")
    check("列表默认 page=1 / page_size=20 回显",
          d.get("page") == 1 and d.get("page_size") == 20, f"body={d!r}")
    ids = [it["id"] for it in d.get("items", [])]
    check("排序固定 id DESC（新记录在前）", ids == sorted(ids, reverse=True),
          f"ids={ids!r}")

    s, h, d = request(port, "GET", "/api/items?q=" + urllib.parse.quote("默认枚举"))
    check("q 关键词搜索命中（大小写不敏感子串）",
          s == 200 and any("默认枚举" in it["title"] for it in d.get("items", []))
          and all("默认枚举" in it["title"] for it in d.get("items", [])),
          f"status={s} body={d!r}")

    s, h, d = request(port, "POST", "/api/items", {"title": "Alpha Bravo"})
    check("创建英文标题样本（供大小写不敏感搜索）", s == 201, f"status={s} body={d!r}")
    s, h, d = request(port, "GET", "/api/items?q=alpha")
    check("q 对英文标题大小写不敏感命中（alpha → Alpha Bravo）",
          s == 200 and any(it["title"] == "Alpha Bravo" for it in d.get("items", []))
          and d.get("total", 0) >= 1,
          f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?q=%20%20")
    check("空 q（strip 后为空）视为未传 → 200 不过滤不报错",
          s == 200 and d.get("total", -1) >= 1, f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?status=done")
    check("status 筛选精确命中",
          s == 200 and all(it["status"] == "done" for it in d.get("items", []))
          and d.get("total", 0) >= 1, f"status={s} body={d!r}")

    s, h, d = request(port, "GET",
                      "/api/items?status=done&q=" + urllib.parse.quote("第一个"))
    check("q+status 组合为 AND",
          s == 200 and all(it["status"] == "done" and "第一个" in it["title"]
                           for it in d.get("items", [])),
          f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?status=TODO")
    check("筛选参数 status 非法 → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?page=0")
    check("page=0 → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?page=abc")
    check("page=abc → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?page_size=101")
    check("page_size=101 → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?page_size=0")
    check("page_size=0 → 400 VALIDATION_ERROR",
          s == 400 and err_code(d) == "VALIDATION_ERROR", f"status={s} body={d!r}")

    # 分页计算：造 5 条，page_size=2 → 3 页
    for i in range(5):
        request(port, "POST", "/api/items", {"title": f"分页样本-{i}"})
    s, h, d = request(port, "GET", "/api/items?q=" + urllib.parse.quote("分页样本")
                      + "&page_size=2")
    check("分页 page_size=2：每页 ≤2 条且 total_pages=ceil(total/2)",
          s == 200 and len(d.get("items", [])) == 2
          and d.get("total_pages") == max(1, -(-d.get("total", 0) // 2))
          and d.get("total", 0) == 5,
          f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?q=" + urllib.parse.quote("分页样本")
                      + "&page_size=2&page=3")
    check("末页只有余数条（5 条 page_size=2 第 3 页 1 条）",
          s == 200 and len(d.get("items", [])) == 1, f"status={s} body={d!r}")

    s, h, d = request(port, "GET", "/api/items?page=9999")
    check("越界页 → 200 空 items（非错误）",
          s == 200 and d.get("items") == [], f"status={s} body={d!r}")

    # 7. DELETE 与删除后 404
    s, h, d = request(port, "DELETE", f"/api/items/{item2.get('id', 2)}")
    check("DELETE 成功返回 204 且无响应体",
          s == 204 and d == b"", f"status={s} body={d!r}")
    s, h, d = request(port, "GET", f"/api/items/{item2.get('id', 2)}")
    check("删除后 GET → 404 NOT_FOUND",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")
    s, h, d = request(port, "DELETE", f"/api/items/{item2.get('id', 2)}")
    check("重复 DELETE → 404 NOT_FOUND",
          s == 404 and err_code(d) == "NOT_FOUND", f"status={s} body={d!r}")

    # 8. 静态资源与路径穿越
    s, h, d = request(port, "GET", "/", expect_json=False)
    check("GET / 返回 200 HTML（含 viewport meta）",
          s == 200 and b"viewport" in d and b"text/html"
          in h.get("Content-Type", "").encode(), f"status={s} ct={h.get('Content-Type')!r}")
    s, h, d = request(port, "GET", "/static/app.js", expect_json=False)
    check("GET /static/app.js 返回 200 JS",
          s == 200 and "javascript" in h.get("Content-Type", ""),
          f"status={s} ct={h.get('Content-Type')!r}")
    s, h, d = request(port, "GET", "/static/../server.py", expect_json=False)
    check("路径穿越 /static/../server.py → 404",
          s == 404, f"status={s}")
    s, h, d = request(port, "GET", "/static/%2e%2e/server.py", expect_json=False)
    check("编码路径穿越 %2e%2e → 404", s == 404, f"status={s}")

    return item1


# ---------------------------------------------------------------- 阶段 2

def phase_restart(proc1, port1, db_path):
    print("== 阶段 2：服务重启持久化（同一 --db 文件） ==")
    s, h, before = request(port1, "GET", "/api/items?page_size=100")
    check("重启前列表可读", s == 200, f"status={s}")
    total_before = before.get("total")
    sample = before.get("items", [None])[0]

    # 停止第一个服务进程（模拟服务重启）
    proc1.terminate()
    proc1.wait(timeout=10)

    port2 = free_port()
    proc2 = start_server(port2, db_path)
    try:
        s, h, after = request(port2, "GET", "/api/items?page_size=100")
        check("重启后 GET /api/items 返回 200", s == 200, f"status={s}")
        check("重启后 total 与重启前一致",
              s == 200 and after.get("total") == total_before,
              f"before={total_before} after={after.get('total')}")
        if sample is not None and s == 200:
            ids_after = {it["id"] for it in after.get("items", [])}
            check("重启后原记录（含全部字段）仍在",
                  sample["id"] in ids_after, f"missing id={sample.get('id')}")
            match = next((it for it in after["items"] if it["id"] == sample["id"]), None)
            check("重启后字段逐一相等（title/status/priority/created_at/updated_at）",
                  match is not None and all(
                      match.get(k) == sample.get(k) for k in
                      ("title", "status", "priority", "created_at", "updated_at")),
                  f"before={sample!r} after={match!r}")
        # 重启后仍可写
        s, h, d = request(port2, "POST", "/api/items", {"title": "重启后新增"})
        check("重启后仍可创建（201）", s == 201, f"status={s} body={d!r}")
    finally:
        proc2.terminate()
        proc2.wait(timeout=10)


# ---------------------------------------------------------------- 主流程

def main():
    tmpdir = tempfile.mkdtemp(prefix="issue-board-selftest-")
    db_path = os.path.join(tmpdir, "selftest.sqlite3")
    port = free_port()
    proc = None
    try:
        print(f"临时目录: {tmpdir}")
        print(f"临时数据库: {db_path}")
        print(f"临时端口: {port}")
        proc = start_server(port, db_path)
        phase_api(port)
        phase_restart(proc, port, db_path)
        proc = None
    except Exception as exc:  # 环境或断言外的异常：如实报告
        FAILURES.append(("selftest 运行异常", repr(exc)))
        print(f"  [ERROR] selftest 运行异常: {exc!r}")
        import traceback
        traceback.print_exc()
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"自测失败：{len(FAILURES)} 项未通过，{PASS_COUNT} 项通过。")
        for name, detail in FAILURES:
            print(f"  - {name}: {detail}")
        return 1
    print(f"自测全部通过：{PASS_COUNT} 项断言 PASS，退出码 0。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
