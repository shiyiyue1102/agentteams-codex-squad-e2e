"""开发自测：临时端口 + 临时数据库，自动检查 API 正常/错误路径与重启持久化。

用法：
    python3 selftest.py

全部通过退出码 0；任一失败退出码 1 并打印失败项。运行后不残留数据库文件。
仅使用 Python 标准库。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(SCRIPT_DIR, "server.py")

PASS_COUNT = 0
FAILURES = []


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(port, db_path):
    proc = subprocess.Popen(
        [sys.executable, SERVER_PY, "--host", "127.0.0.1", "--port", str(port),
         "--db", db_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:{}".format(port)
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("服务进程提前退出，exit={}".format(proc.returncode))
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc, base
        except OSError:
            time.sleep(0.15)
    proc.terminate()
    raise RuntimeError("服务启动超时")


def request(base, method, path, body=None, raw_body=None, timeout=5):
    """返回 (status, parsed_json_or_None, raw_bytes, headers)。"""
    url = base + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            status = resp.status
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        status = e.code
        resp_headers = dict(e.headers)
    parsed = None
    if payload:
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except ValueError:
            parsed = None
    return status, parsed, payload, resp_headers


def check(name, cond, detail=""):
    global PASS_COUNT
    if cond:
        PASS_COUNT += 1
        print("  PASS  {}".format(name))
    else:
        FAILURES.append((name, detail))
        print("  FAIL  {}  {}".format(name, detail))


def expect_error(name, base, method, path, http_status, code, body=None, raw_body=None):
    status, parsed, payload, _ = request(base, method, path, body=body, raw_body=raw_body)
    ok = (
        status == http_status
        and isinstance(parsed, dict)
        and set(parsed.keys()) == {"error"}
        and parsed["error"].get("code") == code
        and isinstance(parsed["error"].get("message"), str)
        and parsed["error"]["message"]
    )
    check(name, ok, "got status={} body={}".format(status, payload[:200]))
    return parsed


def main():
    tmpdir = tempfile.mkdtemp(prefix="issue-board-selftest-")
    db_path = os.path.join(tmpdir, "items-test.db")
    port = free_port()
    proc = None
    try:
        proc, base = start_server(port, db_path)
        try:
            run_checks(base, db_path, port)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # A6 重启持久化：同一 --db 重新启动独立进程
        run_restart_check(port, db_path)
    except Exception as e:  # 基础设施异常也计入失败
        import traceback
        FAILURES.append(("自测执行异常", "{}\n{}".format(e, traceback.format_exc())))
        print("  FAIL  自测执行异常: {}".format(e))
        if proc is not None and proc.poll() is None:
            proc.kill()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("通过 {} 项，失败 {} 项".format(PASS_COUNT, len(FAILURES)))
    if FAILURES:
        print("失败项：")
        for name, detail in FAILURES:
            print("  - {}  {}".format(name, detail))
        return 1
    leftover = os.path.exists(db_path)
    if leftover:
        print("失败项：临时数据库文件残留")
        return 1
    print("SELFTEST OK（临时数据库已清理，无残留文件）")
    return 0


def run_checks(base, db_path, port):
    item_keys = {"id", "title", "status", "priority", "created_at", "updated_at"}

    # --- A7 health ---
    status, parsed, _, ctype = request(base, "GET", "/api/health")
    check("health 200 {status:ok}", status == 200 and parsed == {"status": "ok"},
          "got {} {}".format(status, parsed))

    # --- 空列表 / 空态数据 ---
    status, parsed, _, _ = request(base, "GET", "/api/items")
    check("空库列表 200 total=0 total_pages=0",
          status == 200 and parsed["items"] == [] and parsed["total"] == 0
          and parsed["total_pages"] == 0 and parsed["page"] == 1
          and parsed["page_size"] == 10,
          "got {}".format(parsed))

    # --- A1 创建：trim + 显式枚举 ---
    status, created, _, _ = request(
        base, "POST", "/api/items",
        body={"title": "  写周报  ", "status": "in_progress", "priority": "high", "foo": 1})
    check("POST 201 trim 标题 + 枚举 + 忽略未知字段",
          status == 201 and isinstance(created, dict) and set(created.keys()) == item_keys
          and created["title"] == "写周报" and created["status"] == "in_progress"
          and created["priority"] == "high" and isinstance(created["id"], int)
          and created["created_at"].endswith("Z") and created["updated_at"].endswith("Z"),
          "got {} {}".format(status, created))
    first_id = created["id"] if isinstance(created, dict) else None

    # --- A1 默认值 ---
    status, c2, _, _ = request(base, "POST", "/api/items", body={"title": "买牛奶"})
    check("POST 默认 status=todo priority=medium",
          status == 201 and c2["status"] == "todo" and c2["priority"] == "medium",
          "got {} {}".format(status, c2))

    # --- B1 标题校验 ---
    expect_error("纯空白标题 → invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"title": "   "})
    expect_error("缺失标题 → invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"status": "todo"})
    expect_error("非字符串标题 → invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"title": 123})
    expect_error("显式 null 标题 → invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"title": None})
    status, t80, _, _ = request(base, "POST", "/api/items", body={"title": "字" * 80})
    check("trim 后恰 80 字 → 201", status == 201 and t80["title"] == "字" * 80,
          "got {}".format(status))
    expect_error("81 字 → invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"title": "字" * 81})

    # --- B2 枚举校验 ---
    expect_error("status=finished → invalid_status", base, "POST", "/api/items",
                 400, "invalid_status", body={"title": "x", "status": "finished"})
    expect_error("priority=urgent → invalid_priority", base, "POST", "/api/items",
                 400, "invalid_priority", body={"title": "x", "priority": "urgent"})
    expect_error("显式 status:null → invalid_status", base, "POST", "/api/items",
                 400, "invalid_status", body={"title": "x", "status": None})
    expect_error("显式 priority:null → invalid_priority", base, "POST", "/api/items",
                 400, "invalid_priority", body={"title": "x", "priority": None})
    # 校验顺序：title 先于 status
    expect_error("title+status 同错 → 先报 invalid_title", base, "POST", "/api/items",
                 400, "invalid_title", body={"title": "", "status": "nope"})

    # --- B4 请求体与方法 ---
    expect_error("非法 JSON → invalid_json", base, "POST", "/api/items",
                 400, "invalid_json", raw_body="{not json")
    expect_error("空 body POST → invalid_json", base, "POST", "/api/items",
                 400, "invalid_json", raw_body=b"")
    expect_error("JSON 数组 body → invalid_json", base, "POST", "/api/items",
                 400, "invalid_json", body=[1, 2])
    expect_error("POST /api/items/1 → 405", base, "POST", "/api/items/{}".format(first_id),
                 405, "method_not_allowed", body={"title": "x"})
    expect_error("DELETE /api/items → 405", base, "DELETE", "/api/items",
                 405, "method_not_allowed")
    expect_error("未知 /api 路径 → 404 not_found(JSON)", base, "GET", "/api/nope",
                 404, "not_found")

    # --- GET 单条 / id 校验 ---
    status, got, _, _ = request(base, "GET", "/api/items/{}".format(first_id))
    check("GET /api/items/{id} 200 同构 Item",
          status == 200 and got["id"] == first_id and got["title"] == "写周报",
          "got {} {}".format(status, got))
    expect_error("id 非数字 → invalid_param", base, "GET", "/api/items/abc",
                 400, "invalid_param")
    expect_error("id=-1 → invalid_param", base, "GET", "/api/items/-1",
                 400, "invalid_param")
    expect_error("id=1.5 → invalid_param", base, "GET", "/api/items/1.5",
                 400, "invalid_param")
    expect_error("id=0 → not_found", base, "GET", "/api/items/0", 404, "not_found")
    expect_error("id=999999 → not_found", base, "GET", "/api/items/999999",
                 404, "not_found")

    # --- A2 PUT 部分更新 ---
    status, up, _, _ = request(base, "PUT", "/api/items/{}".format(first_id),
                               body={"status": "done"})
    check("PUT status 部分更新（其余字段不变）",
          status == 200 and up["status"] == "done" and up["title"] == "写周报"
          and up["priority"] == "high" and up["updated_at"] >= up["created_at"],
          "got {} {}".format(status, up))
    time.sleep(1.1)  # 秒级时间戳，跨秒后才能断言刷新
    status, up2, _, _ = request(base, "PUT", "/api/items/{}".format(first_id),
                                body={"title": "  写月报  ", "priority": "low"})
    check("PUT title trim + priority，updated_at 刷新",
          status == 200 and up2["title"] == "写月报" and up2["priority"] == "low"
          and up2["status"] == "done" and up2["updated_at"] > up2["created_at"],
          "got {} {}".format(status, up2))
    expect_error("PUT 空对象 → no_fields", base, "PUT", "/api/items/{}".format(first_id),
                 400, "no_fields", body={})
    expect_error("PUT 仅未知字段 → no_fields", base, "PUT",
                 "/api/items/{}".format(first_id), 400, "no_fields", body={"foo": 1})
    expect_error("PUT 非法 title → invalid_title", base, "PUT",
                 "/api/items/{}".format(first_id), 400, "invalid_title", body={"title": " "})
    expect_error("PUT 不存在 id + 非法 title → 400 先于 404", base, "PUT",
                 "/api/items/999999", 400, "invalid_title", body={"title": ""})
    expect_error("PUT 不存在 id + 合法 body → not_found", base, "PUT",
                 "/api/items/999999", 404, "not_found", body={"title": "x"})

    # --- A4/A5 搜索、筛选、分页 ---
    for title, st, pr in [
        ("Buy milk", "todo", "low"),
        ("buy BREAD", "in_progress", "medium"),
        ("100% juice", "todo", "high"),
        ("snake_case task", "done", "low"),
    ]:
        s, _, _, _ = request(base, "POST", "/api/items",
                             body={"title": title, "status": st, "priority": pr})
        assert s == 201, s
    status, r, _, _ = request(base, "GET", "/api/items?q=buy")
    titles = {i["title"] for i in r["items"]}
    check("q=buy 大小写不敏感子串",
          status == 200 and titles == {"Buy milk", "buy BREAD"} and r["total"] == 2,
          "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?q=buy&status=todo")
    check("q+status AND 组合",
          status == 200 and r["total"] == 1 and r["items"][0]["title"] == "Buy milk",
          "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?q=100%25")
    check("q=100% LIKE 元字符字面匹配",
          status == 200 and r["total"] == 1 and r["items"][0]["title"] == "100% juice",
          "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?q=_")
    check("q=_ LIKE 元字符字面匹配",
          status == 200 and r["total"] == 1 and r["items"][0]["title"] == "snake_case task",
          "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?q=")
    check("q= 空串等同未筛选", status == 200 and r["total"] == 7, "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?status=done")
    check("status=done 精确筛选",
          status == 200 and r["total"] == 2 and all(i["status"] == "done" for i in r["items"]),
          "got {}".format(r))
    status, r, _, _ = request(base, "GET", "/api/items?page=1&page_size=2")
    check("分页 page_size=2 排序 created_at DESC, id DESC",
          status == 200 and len(r["items"]) == 2 and r["total"] == 7 and r["total_pages"] == 4
          and [i["id"] for i in r["items"]] == sorted((i["id"] for i in r["items"]), reverse=True),
          "got {}".format(r))
    page_ids = []
    for p in (1, 2, 3, 4):
        _, r, _, _ = request(base, "GET", "/api/items?page={}&page_size=2".format(p))
        page_ids.extend(i["id"] for i in r["items"])
    check("翻页无重复/遗漏", sorted(page_ids) == list(range(1, 8)) and len(set(page_ids)) == 7,
          "got {}".format(page_ids))
    status, r, _, _ = request(base, "GET", "/api/items?page=99")
    check("越界页 200 空 items", status == 200 and r["items"] == [] and r["total"] == 7,
          "got {}".format(r))
    expect_error("page=0 → invalid_param", base, "GET", "/api/items?page=0",
                 400, "invalid_param")
    expect_error("page=abc → invalid_param", base, "GET", "/api/items?page=abc",
                 400, "invalid_param")
    expect_error("page_size=0 → invalid_param", base, "GET", "/api/items?page_size=0",
                 400, "invalid_param")
    expect_error("page_size=101 → invalid_param", base, "GET", "/api/items?page_size=101",
                 400, "invalid_param")
    expect_error("筛选 status=foo → invalid_param", base, "GET", "/api/items?status=foo",
                 400, "invalid_param")

    # --- A3 DELETE ---
    status, parsed, payload, _ = request(base, "DELETE", "/api/items/{}".format(first_id))
    check("DELETE 204 无 body", status == 204 and payload == b"",
          "got {} {!r}".format(status, payload))
    expect_error("删除后 GET → not_found", base, "GET", "/api/items/{}".format(first_id),
                 404, "not_found")
    expect_error("重复 DELETE → not_found", base, "DELETE", "/api/items/{}".format(first_id),
                 404, "not_found")

    # --- 静态文件与安全基线 ---
    status, _, payload, headers = request(base, "GET", "/")
    body_text = payload.decode("utf-8", "replace")
    check("GET / → index.html（含 viewport）",
          status == 200 and "viewport" in body_text and "text/html" in headers.get("Content-Type", ""),
          "got {}".format(status))
    for asset in ("/app.css", "/app.js"):
        status, _, payload, _ = request(base, "GET", asset)
        check("静态资源 {} 200".format(asset), status == 200 and payload,
              "got {}".format(status))
    status, _, _, _ = request(base, "GET", "/../server.py")
    status2, _, _, _ = request(base, "GET", "/%2e%2e/server.py")
    status3, _, _, _ = request(base, "GET", "/static/../../server.py")
    check("目录穿越（.. 与编码变体）→ 404",
          status == 404 and status2 == 404 and status3 == 404,
          "got {} {} {}".format(status, status2, status3))
    status, _, _, _ = request(base, "GET", "/nope")
    check("未知非 API 路径 → 404", status == 404, "got {}".format(status))

    # --- A6 重启持久化预检：文件落盘、删除后余 6 条（重启验证在 run_restart_check） ---
    _, before, _, _ = request(base, "GET", "/api/items?page_size=100")
    check("数据库文件真实落盘且删除后余 6 条",
          os.path.isfile(db_path) and before["total"] == 6,
          "exists={} total={}".format(os.path.isfile(db_path), before.get("total")))


def run_restart_check(port, db_path):
    """同一 --db 重启独立进程后验证数据保留（由 main 调用）。"""
    proc2, base2 = start_server(port, db_path)
    try:
        _, after, _, _ = request(base2, "GET", "/api/items?page_size=100")
        _, juice, _, _ = request(base2, "GET", "/api/items?q=juice")
        check("重启后数据完整保留（6 条 + 搜索命中）",
              after["total"] == 6 and juice["total"] == 1
              and juice["items"][0]["title"] == "100% juice",
              "got total={} juice={}".format(after.get("total"), juice.get("total")))
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()


if __name__ == "__main__":
    sys.exit(main())
