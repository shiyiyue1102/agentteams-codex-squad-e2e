# 事项管理应用（Issue Board）

一个可本地运行的多文件事项管理应用：后端为 **Python 3 标准库 `http.server` + `sqlite3`**，
前端为 **HTML / CSS / 原生 JS**，**不引入任何外部运行依赖**（无 pip 包、无 npm、无 CDN 资源）。

关联 Issue：[shiyiyue1102/agentteams-codex-squad-e2e#108](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/108)

## 功能

- 事项字段：标题、状态（待办 `todo` / 进行中 `in_progress` / 完成 `done`）、优先级（高 `high` / 中 `medium` / 低 `low`，创建缺省 `medium`）
- 新增、编辑（部分更新）、删除、单条查询
- 标题关键词搜索（子串、ASCII 不区分大小写）与状态筛选，可 AND 组合
- 分页列表（默认每页 10 条，`id DESC` 新建在前；越界页返回空列表并展示空态）
- SQLite 文件持久化：**页面刷新与服务重启后数据均保留**（每次写操作即时 commit）
- 校验与错误：标题去除两端空白后必填且不超过 80 字；非法状态/优先级、错误输入、
  不存在记录均返回统一 JSON 错误信封 `{"error": {"code", "message", "details"?}}`
- 页面展示空态、加载状态、操作成功反馈与失败原因；用户文本一律按**字面文字**渲染
  （`textContent`/`createElement`，不执行 HTML，防 XSS）
- 响应式：桌面与 375px 宽视口均可操作，无横向溢出

不包含：登录/鉴权、云部署、多用户等非目标功能。

## 目录结构

```
cases/issue-board-20261128-0555-dsh/
├── README.md          # 本文件：启动说明、临时数据库测试方式、开发自测说明
├── server.py          # 后端入口：http.server + sqlite3，同时服务静态前端与 JSON API
├── selftest.py        # 开发自测脚本（仅标准库）：临时 DB 起服务并跑 API 冒烟断言
├── .gitignore         # 忽略 *.db、*.db-journal、__pycache__/
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## 启动说明

要求：Python 3.8+（仅标准库）。在本目录（`cases/issue-board-20261128-0555-dsh/`）下执行：

```
python3 server.py [--host HOST] [--port PORT] [--db DB_PATH]
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址。**独立测试/局域网预览须以 `--host 0.0.0.0` 启动**，再用 `http://<主机IP>:<端口>/` 访问 |
| `--port` | `8000` | 监听端口 |
| `--db` | 脚本所在目录下 `items.db` | SQLite 文件路径；相对路径按脚本所在目录解析，文件不存在时自动建库建表 |

服务为单进程前台运行，`Ctrl-C` 停止。启动成功后 stdout 会打印监听地址与 DB 路径，例如：

```
Issue Board 已启动：http://127.0.0.1:8000/  (db: /path/to/cases/issue-board-20261128-0555-dsh/items.db)
```

随后浏览器打开 `http://127.0.0.1:8000/` 即可使用。

## 临时数据库测试方式

用一次性临时库启动，不污染默认 `items.db`：

```
python3 server.py --db "$(mktemp -d)/items_test.db" --port 8000
```

- 重置数据：停止服务并删除该临时 `.db` 文件即可，不影响默认库。
- 网络可达预览（供远程浏览器测试）：追加 `--host 0.0.0.0`，用 `http://<主机IP>:<端口>/` 访问；
  测试结束后停止服务并清理临时 DB。

## 开发自测

```
python3 selftest.py
```

自测脚本仅用标准库：自动选择空闲端口 + 临时数据库启动 `server.py`，跑通
「创建 → 查询 → 更新 → 删除 → 搜索 → 状态筛选 → 分页 → 错误码 → 重启持久化」
冒烟断言（覆盖验收清单 §9A 的 A1–A16 冒烟子集，并静态自查前端无外链资源、
无非纯文本渲染 API），结束后自动清理子进程与临时目录。**退出码 0 = 全部通过**。

## API 概览

统一前缀 `/api`，响应均为 `application/json; charset=utf-8`（UTF-8）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/items?q=&status=&page=&page_size=` | 分页列表 + 关键词搜索 + 状态筛选（AND 组合），`id DESC` 排序；返回 `{items, page, page_size, total, total_pages}`；越界页返回 200 空列表 |
| `GET` | `/api/items/{id}` | 单条查询 |
| `POST` | `/api/items` | 新增，请求体如 `{"title": "买菜", "status": "todo", "priority": "high"}`；仅 `title` 必填（trim 后 1–80 字），`status` 缺省 `todo`，`priority` 缺省 `medium`；成功 201 返回完整 Item |
| `PUT` | `/api/items/{id}` | 编辑（部分更新）：`title` / `status` / `priority` 至少提供一个；成功 200 返回更新后 Item（`updated_at` 已刷新） |
| `DELETE` | `/api/items/{id}` | 删除；成功 200 返回 `{"id": N, "deleted": true}` |

Item JSON：

```json
{
  "id": 1,
  "title": "买菜",
  "status": "todo",
  "priority": "medium",
  "created_at": "2026-11-28T05:55:00Z",
  "updated_at": "2026-11-28T05:55:00Z"
}
```

查询参数规则：`q` 子串匹配、ASCII 不区分大小写，`%`、`_`、`\` 按字面量处理，`q=` 空等同未提供；
`status` 只接受 `todo` / `in_progress` / `done`；`page` ≥ 1（默认 1）；`page_size` 1–100（默认 10）。

### 错误码

所有错误返回统一信封：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "标题去除空白后不能为空",
    "details": [ { "field": "title", "message": "标题去除空白后不能为空" } ]
  }
}
```

| HTTP 状态 | code | 触发场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | 请求体不是合法 JSON，或不是 JSON 对象 |
| 400 | `VALIDATION_ERROR` | 标题缺失/非字符串/trim 后为空/超 80 字；非法 `status`/`priority`；路径 `{id}` 非正整数；`page`/`page_size` 非法；查询参数 `status` 非法；PUT 体不含任何可更新字段 |
| 404 | `NOT_FOUND` | 记录不存在；未知路由 |
| 405 | `METHOD_NOT_ALLOWED` | 已知路径使用了不支持的 HTTP 方法 |
| 500 | `INTERNAL_ERROR` | 服务端未预期异常（正常运行不应出现） |

校验优先级：`BAD_REQUEST`（body 解析）→ `VALIDATION_ERROR`（字段/参数校验）→ `NOT_FOUND`（存在性）。

## 数据持久化说明

- 数据保存在 `--db` 指定的 SQLite 文件中（默认 `items.db`，已被 `.gitignore` 排除，不入库）。
- 每次写操作（POST/PUT/DELETE）即时 `commit`，因此刷新页面、重启服务后数据均保留。
- 前端不依赖 localStorage/内存作为数据源，每次加载通过 `GET /api/items` 重新拉取。
