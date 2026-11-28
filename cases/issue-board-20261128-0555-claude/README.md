# 事项管理应用（Issue Board）

可本地运行的多文件事项管理应用，对应 Issue
[#106](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/106)。

- 后端：Python 标准库 `http.server`（ThreadingHTTPServer）+ `sqlite3`，零外部依赖
- 前端：HTML / CSS / 原生 JS（无框架、无 CDN、无外部字体）
- 功能：事项标题/状态（待办/进行中/完成）/优先级；新增、编辑、删除（行内确认）；
  标题关键词搜索 + 状态筛选（AND 组合）；分页；SQLite 落盘，刷新与服务重启后数据保留
- 校验：标题去除两端空白后必填且不超过 80 字；非法状态/优先级、错误输入、
  不存在记录返回结构化 JSON 错误（`{"error":{"code","message"}}`，中文消息）
- 安全：所有用户文本以文字渲染（textContent，防 XSS）；静态服务防目录穿越；
  错误响应不泄露堆栈；服务默认绑定 `0.0.0.0` 便于局域网/预览访问

## 环境要求

Python 3.8+（仅标准库）。无需 pip/npm 安装任何依赖。

## 启动

```bash
cd cases/issue-board-20261128-0555-claude
python3 server.py
```

默认监听 `http://0.0.0.0:8000`，浏览器打开 `http://localhost:8000/` 即可使用。

### 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `8000` | 监听端口 |
| `--db` | 脚本同目录 `items.db` | SQLite 数据库文件路径；**相对路径按本目录解析**（与当前工作目录无关），绝对路径原样使用；父目录不存在时报错退出 |

## 临时数据库测试方式

不污染默认 `items.db`，用一次性临时库启动：

```bash
python3 server.py --db "$(mktemp -d)/items-test.db" --port 8010
```

验证重启持久化：停止进程后，以**同一 `--db` 路径**重新启动，数据完整保留：

```bash
python3 server.py --db /tmp/demo/items.db --port 8010   # 写入数据后 Ctrl-C
python3 server.py --db /tmp/demo/items.db --port 8010   # 数据仍在
```

## 开发自测

```bash
python3 selftest.py
```

自动在临时端口 + 临时数据库上启动服务，覆盖：health、创建（trim/默认值/未知字段忽略）、
标题与枚举校验（含显式 null、80/81 字边界、校验顺序）、非法 JSON、id 格式与 404、
PUT 部分更新与 `no_fields`、405、搜索（大小写不敏感、LIKE 元字符字面匹配、AND 组合）、
分页（排序、翻页无重复遗漏、越界空页、参数错误）、DELETE 204 与重复删除、
静态资源与目录穿越防护，以及**同一 `--db` 重启进程后的持久化**。
全部通过退出码 0；任一失败退出码 1 并打印失败项；运行后不残留数据库文件。

## API 概览

Base：同源 `/api`，JSON UTF-8。Item 结构：
`{"id","title","status","priority","created_at","updated_at"}`（UTC 秒级 ISO-8601）。

| 方法与路径 | 用途 | 成功响应 |
|---|---|---|
| `GET /api/health` | 存活探测 | 200 `{"status":"ok"}` |
| `GET /api/items?page&page_size&q&status` | 列表/搜索/筛选/分页（排序固定 `created_at DESC, id DESC`） | 200 `{items,page,page_size,total,total_pages}` |
| `POST /api/items` | 新增（`title` 必填；`status`/`priority` 可选，默认 `todo`/`medium`） | 201 Item |
| `GET /api/items/{id}` | 查询单条 | 200 Item |
| `PUT /api/items/{id}` | 部分更新（至少一个已知字段） | 200 更新后 Item |
| `DELETE /api/items/{id}` | 删除 | 204 |

错误码：`invalid_json` / `invalid_title` / `invalid_status` / `invalid_priority` /
`invalid_param` / `no_fields` / `not_found`(404) / `method_not_allowed`(405) /
`internal_error`(500)。校验先于存在性检查；单响应只报第一个字段错误
（title → status → priority）。

枚举线上值：`status ∈ todo|in_progress|done`（界面：待办/进行中/完成）、
`priority ∈ high|medium|low`（界面：高/中/低）。

## 文件结构

```
cases/issue-board-20261128-0555-claude/
├── README.md        # 本文件
├── server.py        # HTTP 服务入口（CLI：--host/--port/--db）
├── store.py         # SQLite 数据访问层（建表、CRUD、搜索、分页）
├── selftest.py      # 开发自测（临时端口 + 临时数据库）
└── static/
    ├── index.html   # 单页入口（含 viewport meta）
    ├── app.css      # 样式（桌面与 375px 响应式，无横向溢出）
    └── app.js       # 前端逻辑（原生 JS，textContent 渲染防 XSS）
```
