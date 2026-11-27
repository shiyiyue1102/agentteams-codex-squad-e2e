# 事项管理应用（Issue Board）

一个可本地运行的多文件事项管理应用：后端使用 **Python 3 标准库**（`http.server` + `sqlite3`），前端使用 **HTML / CSS / 原生 JS**。**零外部运行依赖**：无需 `pip install`，无任何第三方包、框架或 CDN 资源。

- 关联 Issue：[研发小队复杂验收 20261128-005233 qwen：事项管理应用（#88）](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/88)
- 代码目录（硬边界）：`cases/issue-board-20261128-005233-qwen/`，目录外零改动

## 功能

- 事项三要素：标题、状态（待办 `todo` / 进行中 `doing` / 完成 `done`）、优先级（高 `high` / 中 `medium` / 低 `low`）
- 新增、编辑（预填当前值，可取消）、删除（二次确认）
- 关键词搜索（标题不区分大小写子串匹配，`%` `_` 按字面量处理）与状态筛选，两者可叠加（AND）
- 分页（每页 10 条，上一页 / 下一页 + 页码信息，首末页按钮自动禁用）
- 数据存储在 SQLite 单文件：**浏览器刷新与服务重启后数据均保留**
- 校验与错误提示：标题去除两端空白后必填且 ≤80 字；非法状态 / 优先级、错误输入、不存在记录均返回契约规定的明确错误码，并在页面上展示失败原因
- 页面提供两种可区分的空态（暂无事项 / 无匹配结果）、加载态、操作成功提示
- XSS 安全：所有用户可控文本一律经 `textContent` 按文字渲染，`<script>` 等标签只会以字面文字显示，不会执行
- 响应式：桌面（≥1280px）与 375px 窄屏均可完整操作且无横向滚动

## 文件布局

```
cases/issue-board-20261128-005233-qwen/
├── README.md          # 本文档：启动说明、临时数据库测试方式、开发自测
├── server.py          # HTTP 入口：路由、静态文件、API handler
├── db.py              # SQLite 建表、连接管理与数据访问函数
├── .gitignore         # 忽略运行产物（*.db、__pycache__/ 等）
├── static/
│   ├── index.html     # 单页应用
│   ├── app.css        # 样式（含 375px 响应式）
│   └── app.js         # 原生 JS 逻辑（textContent 渲染，无 innerHTML）
└── tests/
    └── test_api.py    # 开发自测（unittest + urllib + tempfile，纯标准库）
```

## 快速启动

```sh
cd cases/issue-board-20261128-005233-qwen
python3 server.py
# 输出：Issue Board 已启动: http://127.0.0.1:8000/
# 浏览器打开 http://127.0.0.1:8000/ 即可使用，Ctrl+C 停止
```

首次启动自动建表（`CREATE TABLE IF NOT EXISTS`），无需任何初始化脚本。

### 命令行参数与环境变量

| CLI 参数 | 环境变量（等价） | 默认值 | 说明 |
|---|---|---|---|
| `--host` | `ISSUE_BOARD_HOST` | `127.0.0.1` | 监听地址；需要被其他机器 / 远程浏览器访问时用 `0.0.0.0` |
| `--port` | `ISSUE_BOARD_PORT` | `8000` | 监听端口 |
| `--db` | `ISSUE_BOARD_DB` | `issue_board.db` | SQLite 数据库文件路径（相对启动时工作目录） |

CLI 参数优先于环境变量。示例：

```sh
python3 server.py --host 0.0.0.0 --port 8123 --db /tmp/issue_board_test.db
```

## 临时数据库测试方式

测试时使用一次性临时数据库，不污染默认数据，测完删除临时目录即完全清理：

```sh
cd cases/issue-board-20261128-005233-qwen
TMPDB="$(mktemp -d)/test_issues.db"
python3 server.py --port 8123 --db "$TMPDB"     # 全新空库启动，端口可自选
# …测试…
# 结束后 Ctrl+C 停止服务，并删除该临时目录：
rm -rf "$(dirname "$TMPDB")"
```

- `--db` 指向新路径时自动创建全新空库。
- 默认 `issue_board.db` 属运行产物，已由本目录 `.gitignore` 忽略，不会提交进仓库。

## 开发自测

### 后端 API 自动化自测（纯标准库）

在 case 根目录运行：

```sh
cd cases/issue-board-20261128-005233-qwen
python3 -B -m unittest discover -s tests -v
```

说明：

- 测试在**进程内子线程**启动服务于临时端口，数据库使用 `tempfile.mkdtemp()` 临时目录，结束后自动清理；`-B` 避免生成 `__pycache__`。**不在仓库目录残留任何文件**。
- 覆盖范围（对应分析报告第 6 节最小集合）：
  - 创建成功与默认值（`todo`/`medium`、trim 存储、`created_at == updated_at`、`Location` 头）
  - title 必填（缺失 / `null` / 非字符串 / 空串 / 纯空白）、80/81 字边界（含中文按码点计数）、trim 后判长
  - 非法 status / priority（含类型错误 `status: 1`、`priority: null`）
  - 非法 JSON（非 JSON、JSON 数组、JSON 标量、空请求体）
  - `GET/PUT/DELETE /api/items/{id}`：存在 / 不存在（404 `item_not_found`）/ 非法 id（`abc`、`0`、`-1` → 400 `invalid_id`）
  - PUT 部分更新（未传字段不变、`updated_at` 刷新）、`empty_update`、PUT 各校验错误
  - DELETE → 204 空体、随后 GET 404、重复 DELETE 404
  - 搜索：不区分大小写子串、`%` 与 `_` 按字面量匹配（`LIKE ... ESCAPE`）、空 `q` 等同不传
  - 状态筛选、与 `q` AND 组合、过滤后 `total`/`total_pages`
  - 分页：默认 `page=1&page_size=10`、id DESC 排序、page 超界返回 200 空数组、`page_size=100` 上限合法、`page=0/abc/-1/1.5`、`page_size=0/101` → 400 `invalid_param`
  - 路由：`DELETE /api/items`、`POST /api/items/{id}` 等 → 405 `method_not_allowed`；未知路径 → 404 `not_found`；静态文件服务与路径穿越拦截
  - 持久化：**关闭服务后用同一 DB 文件重启**，id / created_at / 修改与删除结果全部保留；新 `--db` 路径为全新空库
- 全部错误响应统一断言 `{"error": {"code", "message"}}` 结构、`message` 非空、`Content-Type: application/json; charset=utf-8`。

### 前端手工自测步骤

1. 按上文「临时数据库测试方式」启动服务，浏览器打开 `http://127.0.0.1:8123/`。
2. 空库首次打开：显示空态「暂无事项，使用上方表单新增第一条吧」。
3. 新增：直接点「新增」（空标题）→ 提示区显示失败原因「标题去除两端空白后必填」；输入正常标题选择状态 / 优先级 → 成功提示 + 列表出现新事项，表单标题清空。
4. XSS：新增标题 `<script>alert(1)</script>` 与 `<img src=x onerror=alert(1)>` → 页面按字面文字显示，无弹窗、无对应活动元素。
5. 编辑：点「编辑」→ 面板预填当前标题 / 状态 / 优先级；修改后「保存」→ 列表即时反映新值；「取消」可关闭面板。
6. 删除：点「删除」→ 原生确认框 → 确认后列表移除并提示成功；取消则不删除。
7. 搜索 / 筛选：输入关键词回车或点「搜索」；切换状态筛选下拉（「全部状态」不传参数）；两者叠加为 AND；无结果时显示「无匹配结果…」空态（与「暂无事项」文案可区分）。
8. 分页：新增 >10 条后出现第 2 页；首页「上一页」禁用、末页「下一页」禁用；页码信息「第 X / Y 页 · 共 N 条」。
9. 持久化：刷新页面 → 数据保留；Ctrl+C 停止服务后用**同一 `--db`** 重启 → 数据完整保留。
10. 响应式：浏览器开发者工具切换 375px 宽（及桌面 ≥1280px），检查以上全部操作可完成、无横向滚动（`document.documentElement.scrollWidth <= window.innerWidth`）。

## API 概览

契约细节（字段、错误码、边界）以分析报告为准，此处为速查：

| 方法与路径 | 说明 |
|---|---|
| `GET /api/items?q=&status=&page=&page_size=` | 列表：搜索 + 状态筛选（AND）+ 分页，固定 `ORDER BY id DESC`；返回 `{items, page, page_size, total, total_pages}` |
| `POST /api/items` | 新增：`{title, status?, priority?}`；成功 201 + 完整对象 + `Location` 头 |
| `GET /api/items/{id}` | 单条查询：200 / 400 `invalid_id` / 404 `item_not_found` |
| `PUT /api/items/{id}` | 部分更新：只校验并更新出现的字段；成功刷新 `updated_at`；空更新 → 400 `empty_update` |
| `DELETE /api/items/{id}` | 删除：成功 204 空体；随后 GET → 404 |
| `GET /`、`/app.css`、`/app.js` | 静态前端（仅允许 `static/` 目录内文件，已防路径穿越） |

错误码（统一错误体 `{"error": {"code", "message"}}`）：

| HTTP | code | 触发条件 |
|---|---|---|
| 400 | `invalid_json` | 请求体不是合法 JSON 对象 |
| 400 | `invalid_id` | 路径 `{id}` 不匹配 `^[1-9][0-9]*$` |
| 400 | `title_required` | 标题缺失 / 非字符串 / trim 后为空 |
| 400 | `title_too_long` | 标题 trim 后 >80 字（Unicode 码点，恰好 80 合法） |
| 400 | `invalid_status` | `status` 不在 `{todo, doing, done}` |
| 400 | `invalid_priority` | `priority` 不在 `{high, medium, low}` |
| 400 | `invalid_param` | `page` / `page_size` / `status` 查询参数非法 |
| 400 | `empty_update` | PUT 请求体不含任何可更新字段 |
| 404 | `item_not_found` | id 合法但记录不存在 |
| 404 | `not_found` | 未知路径 |
| 405 | `method_not_allowed` | 已知路径不允许该 HTTP 方法 |
| 500 | `internal_error` | 服务端未捕获异常 |

## 数据存储

单表 `items`（SQLite 单文件，`--db` 指定路径；每个写请求在响应前 `commit`）：

```sql
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'doing', 'done')),
    priority   TEXT NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL,   -- ISO 8601 UTC 秒级，如 2026-11-28T00:52:33Z
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
```

## 已知约定（测试注意事项）

- 时间戳为 **UTC 秒级精度**：同一秒内的两次写入 `updated_at` 值可能相同；断言「更新后 `updated_at` 变化」需间隔 ≥1 秒。
- 请求体中的未知字段一律忽略（不报错）。
- `GET /api/items/`（带尾斜杠）宽松地等同 `GET /api/items`。
- API 仅接受 `GET/POST/PUT/DELETE`；其余方法（含 `HEAD`/`OPTIONS`/`PATCH`）在 API 路径上返回 405，在静态已知路径上除 `GET/HEAD` 外返回 405。
- 前端未使用 localStorage / sessionStorage / cookie 存任何业务数据。
