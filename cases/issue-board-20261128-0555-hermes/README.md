# 事项管理应用（Issue Board）

多文件事项管理应用：Python 标准库 HTTP 服务 + SQLite 后端，HTML/CSS/原生 JS 前端，**无任何外部运行依赖**（不需要 pip install，前端无框架、无 CDN）。

对应 Issue：shiyiyue1102/agentteams-codex-squad-e2e#100

## 目录结构

```
cases/issue-board-20261128-0555-hermes/
├── README.md          # 本文件：启动说明、临时数据库测试方式、开发自测说明
├── server.py          # 标准库 HTTP 服务（http.server + sqlite3），支持 --port/--host/--db
└── static/
    ├── index.html     # 页面骨架
    ├── app.js         # 原生 JS（fetch 调用 API；所有用户文本用 textContent 显示，防 XSS）
    └── style.css      # 样式，含 ≤480px 响应式（375px 无横向溢出）
```

## 启动

```bash
python3 server.py --port 8000
```

- 默认监听 `0.0.0.0:8000`（可用 `--host 127.0.0.1` 改为仅本机访问）。
- 默认数据库文件：与 `server.py` 同目录的 `board.db`（文件持久化，刷新与服务重启后数据保留）。
- 浏览器访问 `http://127.0.0.1:8000/`。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | 8000 | 监听端口 |
| `--host` | 0.0.0.0 | 监听地址（测试/预览可保持默认以便网络可达） |
| `--db` | `<server.py 目录>/board.db` | SQLite 数据库文件路径 |

## 临时数据库测试方式

使用独立端口 + 临时数据库文件，不污染正式数据：

```bash
python3 server.py --port 8801 --db /tmp/board-test.db
```

想要干净数据：删除 `/tmp/board-test.db`（及同目录 `board-test.db-wal`/`-shm`，若存在）后重启即可。

## 功能

- 事项字段：标题（去两端空白后必填、≤80 字符）、状态（待办/进行中/完成）、优先级（低/中/高）。
- 新增、编辑、删除；关键词搜索（标题子串、大小写不敏感）+ 状态筛选（可组合）；分页（每页 10 条，新事项在前）。
- 刷新页面与服务重启后数据保留（SQLite 文件持久化）。
- 空态提示、加载中提示、操作成功/失败原因提示；用户文本按文字显示，不执行 HTML。
- 桌面与 375px 宽均可操作，无横向溢出。

## REST API 摘要

- `GET /api/items?q=&status=&page=&page_size=` — 列表；返回 `{items, page, page_size, total, total_pages}`；`page_size` 默认 10、上限 100；排序 `created_at DESC, id DESC`。
- `POST /api/items` — 新增，body `{"title", "status"?, "priority"?}`（默认 todo/medium），成功 201 返回完整 Item。
- `GET /api/items/{id}` — 详情。
- `PUT /api/items/{id}` — 整体更新，`title/status/priority` 均必填，成功 200。
- `DELETE /api/items/{id}` — 删除，成功 204。
- 错误统一为 `{"error": {"code", "message"}}`；错误码：`INVALID_TITLE`、`INVALID_STATUS`、`INVALID_PRIORITY`、`INVALID_JSON`、`INVALID_REQUEST`、`ITEM_NOT_FOUND`（含 id 非正整数）、`METHOD_NOT_ALLOWED`、`INTERNAL_ERROR`。
- Item 字段：`id`（整数）、`title`、`status`（todo/doing/done）、`priority`（low/medium/high）、`created_at`、`updated_at`（ISO 8601 UTC，如 `2026-11-28T05:55:00Z`）。

## 开发自测说明

1. 用临时库启动：`python3 server.py --port 8801 --db /tmp/board-selftest.db`
2. `curl` 自测（示例）：

```bash
# 新增（201）
curl -s -X POST http://127.0.0.1:8801/api/items \
  -H 'Content-Type: application/json' \
  -d '{"title":"自测事项","status":"todo","priority":"high"}'

# 列表 / 搜索 / 筛选 / 分页
curl -s 'http://127.0.0.1:8801/api/items?page=1&page_size=5'
curl -s 'http://127.0.0.1:8801/api/items?q=自测&status=todo'

# 编辑（200）与删除（204）
curl -s -X PUT http://127.0.0.1:8801/api/items/1 \
  -H 'Content-Type: application/json' \
  -d '{"title":"改后标题","status":"doing","priority":"low"}'
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE http://127.0.0.1:8801/api/items/1

# 错误边界（均返回 {"error":{"code","message"}}）
curl -s -X POST http://127.0.0.1:8801/api/items -d '{"title":"   "}'            # 400 INVALID_TITLE
curl -s -X POST http://127.0.0.1:8801/api/items -d '{"title":"x","status":"bad"}' # 400 INVALID_STATUS
curl -s -X POST http://127.0.0.1:8801/api/items -d '{bad'                        # 400 INVALID_JSON
curl -s http://127.0.0.1:8801/api/items/999999                                   # 404 ITEM_NOT_FOUND
curl -s 'http://127.0.0.1:8801/api/items?page=0'                                 # 400 INVALID_REQUEST
```

3. 持久化自测：新增数据后停止服务（Ctrl+C），用同一 `--db` 重启，`GET /api/items` 数据应完整保留。
4. 浏览器自测：访问 `http://127.0.0.1:8801/`，验证新增/编辑/删除、搜索筛选、翻页、空态与错误提示；将浏览器窗口缩到 375px 宽验证无横向溢出；创建标题 `<script>alert(1)</script>` 的事项应按文字显示、不弹窗。

开发自测记录见任务交付物 `self-test-report.md`。
