# 事项管理应用（Issue Board）

可本地运行的多文件事项管理应用：后端为 **Python 标准库 HTTP 服务（`http.server`）+ SQLite（`sqlite3`）**，前端为 **HTML / CSS / 原生 JavaScript**，**零外部运行依赖**（仅需 Python ≥ 3.9，无需 pip 安装任何包）。

关联 Issue：[#94 研发小队复杂验收 20261128-032037 qwen：事项管理应用](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/94)

## 功能

- 事项字段：**标题**、**状态**（待办 `todo` / 进行中 `in_progress` / 完成 `done`）、**优先级**（低 `low` / 中 `medium` / 高 `high`）
- **新增、编辑（部分更新语义）、删除**（删除有 confirm 确认）
- **关键词搜索**（标题、大小写不敏感子串匹配）与**状态筛选**，可组合（AND）
- **分页**（默认每页 10 条，`page_size` 1–100；上一页/下一页 + 页码信息，边界自动禁用）
- **持久化**：SQLite 落盘，每次写操作后立即 commit；浏览器刷新、服务进程重启（同一 DB 文件）后数据完整保留
- **校验**：标题去除两端空白后必填且 ≤ 80 字符（按 Unicode 字符数，80 合法 / 81 非法）；非法状态、非法优先级、错误输入、不存在记录均返回明确错误码与中文说明
- **UI 反馈**：空态（无数据 / 搜索无结果两种文案区分）、加载中提示、每次操作的成功或失败原因（含后端 `error.message` 与错误码）
- **XSS 防护**：用户输入文本一律以 `textContent` 纯文字渲染，前端代码不使用 `innerHTML` 拼接任何用户数据
- **响应式**：桌面（≥1280px）与 375px 宽窄屏均可完整操作，无横向溢出（窄屏下表单/列表纵向堆叠）

不包含（按需求明确排除）：登录/鉴权、云部署、多用户、附件、拖拽看板等。

## 目录结构

```
cases/issue-board-20261128-032037-qwen/
├── server.py        # HTTP 服务入口：API 路由 + 静态文件服务（http.server）
├── db.py            # SQLite 存储层：建表、增删改查、搜索/筛选/分页
├── README.md        # 本文件
├── .gitignore       # 运行时 data/ 与 __pycache__ 不入库
├── static/
│   ├── index.html   # 单页界面
│   ├── style.css    # 样式（含 375px 窄屏媒体查询）
│   └── app.js       # 原生 JS：fetch 相对路径调用 API，textContent 渲染
└── tests/
    └── test_api.py  # 开发自测（stdlib unittest + urllib，自动起停服务）
```

## 启动

```bash
cd cases/issue-board-20261128-032037-qwen
python3 server.py
```

浏览器打开 <http://127.0.0.1:8000/>。停止服务：`Ctrl+C`。

默认监听 `0.0.0.0:8000`（局域网内其他设备/浏览器可通过本机 IP 访问，便于验证响应式与独立测试）。

## 配置

| 配置项 | 环境变量 | CLI 参数 | 默认值 |
|---|---|---|---|
| 监听地址 | `HOST` | `--host` | `0.0.0.0` |
| 监听端口 | `PORT` | `--port` | `8000` |
| 数据库文件 | `ISSUE_BOARD_DB` | `--db` | `./data/items.db`（相对本目录，自动建目录/建表） |

CLI 参数优先于环境变量。示例：

```bash
HOST=127.0.0.1 PORT=9000 python3 server.py --db /tmp/demo.db
```

## 临时数据库测试方式

```bash
ISSUE_BOARD_DB=$(mktemp -d)/test.db python3 server.py
```

- 数据全部写入该临时文件，不污染默认 `data/items.db`；删除该文件即清空数据、恢复初始状态。
- 持久化验证：写入若干事项 → 浏览器刷新（数据保留）→ `kill` 服务进程后用**同一 DB 文件**重新启动 → 数据仍完整保留。

## 开发自测

```bash
cd cases/issue-board-20261128-032037-qwen
python3 -m unittest discover tests -v
# 或
python3 tests/test_api.py
```

自测完全自包含：在系统临时目录创建 SQLite 库、在随机可用端口启动真实服务（线程内服务 + 一次**真实子进程重启**验证持久化），跑完自动清理，无需手工准备任何环境，零第三方依赖。

覆盖范围（对应分析报告 §8 验收清单 #1–#13）：

- 新增：201 回显、标题 strip、默认值（todo/medium）、时间格式
- 校验边界：空/纯空白标题、80/81 字符（ASCII/汉字/emoji）、缺 title、title 非字符串、非法 JSON body
- 非法枚举：status / priority（POST 与 PUT 一致）
- 单条读取、部分更新（updated_at 刷新、created_at 不变）、空对象 `{}` 更新
- 删除：200 `{"success":true,"id":N}`、重复删除 404
- 错误 id：`abc`/`0`/`-1`/`1.5` → 400 `INVALID_ID`；不存在记录 → 404 `ITEM_NOT_FOUND`
- 列表：默认新建在前、搜索大小写不敏感子串（LIKE 通配符按字面处理）、状态筛选、AND 组合、无结果空列表
- 分页：23 条 → 3 页无重复无遗漏、超页返回空 200、非法 page/page_size → 400 `INVALID_PAGE`
- 路由：`DELETE /api/items` → 405、未知 API 路径 → 404、`/` 返回主页面、静态资源、路径穿越拦截
- 持久化：真实子进程 kill 后重启，数据完整保留
- 前端源码级检查：`app.js` 无 `innerHTML`、含 `textContent`；`index.html` 含 viewport

## API 简表

前缀 `/api`，请求/响应均为 `Content-Type: application/json; charset=utf-8`，时间为 UTC ISO-8601（如 `2026-11-28T03:20:37Z`）。

| 方法 | 路径 | 说明 | 成功响应 |
|---|---|---|---|
| GET | `/api/items?keyword=&status=&page=&page_size=` | 列表（搜索/筛选/分页，排序 `created_at DESC, id DESC`） | `200` `{items, page, page_size, total, total_pages}` |
| POST | `/api/items` | 新增（`title` 必填；`status` 默认 `todo`；`priority` 默认 `medium`） | `201` Item 对象 |
| GET | `/api/items/{id}` | 单条 | `200` Item 对象 |
| PUT | `/api/items/{id}` | 编辑（部分更新语义，只更新提供的字段；`{}` 合法） | `200` 更新后 Item 对象 |
| DELETE | `/api/items/{id}` | 删除 | `200` `{"success": true, "id": N}` |
| GET | `/` | 前端页面 | `200` `text/html` |

Item 对象：`{"id", "title", "status", "priority", "created_at", "updated_at"}`

错误统一形态：`{"error": {"code": "...", "message": "..."}}`

| HTTP | code | 触发场景 |
|---|---|---|
| 400 | `TITLE_REQUIRED` | 标题 strip 后为空 |
| 400 | `TITLE_TOO_LONG` | 标题 strip 后 > 80 字符 |
| 400 | `INVALID_STATUS` | status 不在 `todo/in_progress/done`（含列表筛选参数） |
| 400 | `INVALID_PRIORITY` | priority 不在 `low/medium/high` |
| 400 | `INVALID_INPUT` | body 非 JSON / 非对象 / 字段类型错误 / POST 缺 title |
| 400 | `INVALID_ID` | 路径 id 非正整数 |
| 400 | `INVALID_PAGE` | page/page_size 非法（非正整数、page_size > 100） |
| 404 | `ITEM_NOT_FOUND` | id 合法但记录不存在 |
| 404 | `NOT_FOUND` | 未知路径（API 或静态资源） |
| 405 | `METHOD_NOT_ALLOWED` | 已知路径非法方法（如 `DELETE /api/items`） |

## 实现约定说明（契约歧义处的取舍）

- 标题长度 = `len(title.strip())`（Python 字符数），全角/emoji 均按 1 字符计。
- 列表筛选参数 `status=`（空值）视为不筛选；`keyword=`（空值）视为不搜索。
- `page` 超出总页数不是错误：返回 `200` 且 `items` 为空；`total=0` 时 `total_pages=0`。
- PUT 先检查记录存在性（不存在 → 404 `ITEM_NOT_FOUND`），再解析/校验请求体；提供的字段校验规则与 POST 完全一致。
- 非字符串的 `status`/`priority` 值视为“不在枚举内”，返回 `INVALID_STATUS`/`INVALID_PRIORITY`。
- keyword 中的 `%`、`_`、`\` 按字面字符匹配（LIKE ESCAPE），不做通配符解释。
- 删除成功返回 `200 + JSON`（非 204），便于前端统一处理。
- SQLite 每请求短连接 + `PRAGMA busy_timeout=5000`，写后立即 commit。

## 独立测试阶段环境提示

服务默认绑定 `0.0.0.0`，测试时以显式端口启动（如 `PORT=8xxx python3 server.py --db $(mktemp -d)/test.db`），通过网络可达的预览地址用真实浏览器访问（浏览器环境无法访问 Worker 的 localhost）；测试完成后停止服务进程并删除临时 DB 文件即可。
