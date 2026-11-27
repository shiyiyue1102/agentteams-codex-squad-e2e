# 事项管理应用（Issue Board）

可本地运行的多文件事项管理应用：Python 标准库 HTTP 服务 + SQLite 后端，
HTML/CSS/原生 JS 前端，**零外部运行依赖**（Python ≥ 3.9 即可）。

对应 Issue: https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/93

## 功能

- 事项字段：标题、状态（待办 / 进行中 / 完成）、优先级（低 / 中 / 高）
- 新增、编辑（部分更新）、删除（页面删除为两步确认，防误删）
- 关键词搜索（标题大小写不敏感子串）+ 状态筛选（可组合，AND 语义）
- 分页（默认每页 20 条，`id DESC` 新记录在前）
- 持久化：数据存于文件型 SQLite，页面刷新与服务重启后均保留
- 空态提示（区分「暂无事项」与「无匹配结果」）、加载指示、操作成功/失败
  原因展示（服务端 `error.message` 直接可见）
- XSS 防护：用户可控文本一律以 `textContent` 纯文字渲染，不执行 HTML
- 响应式：桌面与 375px 宽视口均可完成全部操作，无横向溢出

## 目录结构

```
cases/issue-board-20261128-032037-claude/
├── README.md        # 本文件
├── server.py        # 服务入口（API + 静态页面，单进程）
├── selftest.py      # 开发自测脚本（仅标准库，临时库 + 临时端口）
└── static/
    ├── index.html
    ├── styles.css
    └── app.js
```

## 启动

```bash
cd cases/issue-board-20261128-032037-claude
python3 server.py --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

CLI 参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址；需要网络可达（如浏览器自动化测试）时用 `0.0.0.0` |
| `--port` | `8000` | 监听端口 |
| `--db` | `data/issue_board.sqlite3` | SQLite 文件路径，相对运行目录，父目录自动创建 |

## 临时数据库测试方式

将 `--db` 指向任意临时文件即得到一个隔离环境；**删除该文件即重置全部数据**：

```bash
python3 server.py --db /tmp/ib-test.sqlite3 --port 8010
# 测试完毕后：
rm -f /tmp/ib-test.sqlite3 /tmp/ib-test.sqlite3-wal /tmp/ib-test.sqlite3-shm
```

## 开发自测

```bash
python3 selftest.py
```

自测脚本自动选择临时端口与临时数据库启动服务子进程，覆盖：创建成功
（201 与字段回显、时间戳格式、trim）、标题边界（空 / 全空白 / 80 字 /
81 字 / emoji 码点计数）、非法枚举与类型错误、未知字段、非法 JSON、
404 / 405（含 Allow 头）、PUT 部分更新与空对象 400、搜索（含大小写
不敏感）、筛选、q+status 组合、分页参数校验与越界页、DELETE 204 与
删除后 404、静态资源与路径穿越防护，以及**同一 --db 重启子进程后的
持久化**。全部通过打印摘要并以退出码 0 结束；结束自动清理临时文件。

## API 概要与 curl 示例

统一错误格式：`{"error": {"code": ..., "message": ..., "fields"?}}`；
状态码 ↔ code：400 `INVALID_JSON` / 400 `VALIDATION_ERROR` /
404 `NOT_FOUND` / 405 `METHOD_NOT_ALLOWED`（带 Allow 头）/ 500 `INTERNAL_ERROR`。

Item 结构：`{id, title, status, priority, created_at, updated_at}`，
时间为 UTC ISO-8601（`YYYY-MM-DDTHH:MM:SSZ`）。
枚举：`status ∈ todo|in_progress|done`（默认 todo），
`priority ∈ low|medium|high`（默认 medium），大小写敏感。

```bash
# 1) 创建事项（201；标题必填，trim 后 ≤80 字）
curl -s -X POST http://127.0.0.1:8000/api/items \
  -H 'Content-Type: application/json' \
  -d '{"title": "写周报", "priority": "high"}'

# 2) 列表：关键词搜索 + 状态筛选 + 分页（200，id DESC）
curl -s 'http://127.0.0.1:8000/api/items?q=%E5%91%A8%E6%8A%A5&status=todo&page=1&page_size=10'

# 3) 更新（部分更新，200；空对象 → 400 VALIDATION_ERROR）
curl -s -X PUT http://127.0.0.1:8000/api/items/1 \
  -H 'Content-Type: application/json' \
  -d '{"status": "done"}'

# 4) 删除（204 无响应体；再删或再查 → 404 NOT_FOUND）
curl -s -i -X DELETE http://127.0.0.1:8000/api/items/1

# 5) 错误示例：非法 JSON → 400 INVALID_JSON
curl -s -X POST http://127.0.0.1:8000/api/items \
  -H 'Content-Type: application/json' -d '{bad json'
```

列表响应：`{"items": [...], "page": 1, "page_size": 20, "total": N,
"total_pages": max(1, ceil(total/page_size))}`；越界页返回 200 与空
`items`（非错误）。`GET /api/items/{id}` 的 id 非正整数或不存在统一
404 `NOT_FOUND`。

## 约束说明

- 不含登录、云部署或其他额外功能。
- 数据库启用 WAL 模式（同目录会出现 `-wal`/`-shm` 辅助文件，属正常现象）。
