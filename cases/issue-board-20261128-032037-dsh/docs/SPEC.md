> 本文件为 analysis 节点产出的契约规格副本（入库便于 PR 评审追溯），内容与上游报告一致（原报告 sha256: bb7b6c05a30b722e5556a9de0394fb4c3c6ac5d955434c93227d16f498aaf4ab）。

# 分析报告：事项管理应用（issue-board-20261128-032037-dsh）

- Issue: https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/91
- 仓库: `shiyiyue1102/agentteams-codex-squad-e2e`
- 代码范围: 全部文件限定在 `cases/issue-board-20261128-032037-dsh/`（仓库当前无 `cases/` 目录，由开发节点新建）
- 节点计划: analysis →（development ∥ test_design）→ testing →（ready_for_review）
- 本报告是开发与测试设计的唯一共享契约。两侧都必须以本文的接口、枚举、错误码与验收标准（AC-xx 编号）为准；如需偏离，先回到 Leader 修订，不要各自约定。

---

## 1. 总体设计决策（先读，消除歧义）

| # | 决策 | 内容 |
|---|------|------|
| D1 | 枚举编码 | API 与数据库存储英文枚举码，UI 显示中文标签。状态：`todo`=待办、`in_progress`=进行中、`done`=完成；优先级：`low`=低、`medium`=中、`high`=高 |
| D2 | 默认值 | 创建（POST）时 `status` 缺省为 `todo`，`priority` 缺省为 `medium`；更新（PUT）为全量更新，三个字段全部必填、无默认 |
| D3 | 错误信封 | 所有 `/api/*` 错误响应统一为 `{"error":{"code":"...","message":"...","field":"..."}}`；`code` 是稳定机器码（测试断言 HTTP 状态码 + code），`message` 为中文可读原因（供 UI 展示失败原因，测试不得锁定 message 具体文案），`field` 可选 |
| D4 | 标题规则 | 服务端用 Python `str.strip()` 去除两端空白后：非空且 `len(title) ≤ 80`（Unicode 码点计数，Python `len()`）；存储 trim 后的值，内部空白原样保留。80 合法、81 非法 |
| D5 | 时间戳 | `created_at`/`updated_at` 为 UTC ISO-8601 秒级字符串 `YYYY-MM-DDTHH:MM:SSZ`；PUT 成功必刷新 `updated_at`（同秒内可能等于旧值，测试不得断言严格大于） |
| D6 | 排序 | 列表固定 `ORDER BY created_at DESC, id DESC`，不提供排序参数，保证分页确定性 |
| D7 | 分页越界 | `page` 超出 `total_pages` 时返回 200 且 `items: []`（不是错误）；参数格式非法才是 400 |
| D8 | id 语义 | 正整数由 SQLite `AUTOINCREMENT` 分配；路径参数非纯整数 → 400 `INVALID_PARAM`；整数但无记录（含 ≤0）→ 404 `NOT_FOUND` |
| D9 | 校验顺序 | PUT/DELETE：先解析 id → 再查存在性（404 优先）→ 再校验 body。POST body 校验顺序：JSON 解析 → title → status → priority，首个失败即为返回的错误。列表查询参数校验顺序：status → page → page_size |
| D10 | 宽松项 | 重复查询参数取第一个值；body 中未知字段忽略；POST/PUT 的 body 不论 `Content-Type` 头一律按 JSON 解析（便于 curl 测试）；POST 中 `status`/`priority` 显式为 `null` 视同缺省（用默认值），PUT 中缺省或 `null` 均为非法 |
| D11 | 编码 | 所有 JSON 响应 `Content-Type: application/json; charset=utf-8`；建议加 `X-Content-Type-Options: nosniff`；页面 `text/html; charset=utf-8` |
| D12 | 无 CORS | 前端与 API 同源，前端一律用相对路径 `/api/...`，不需要也不得引入 CORS 配置 |
| D13 | 技术栈 | Python ≥ 3.9，仅标准库（`http.server`、`sqlite3`、`json`、`argparse` 等）；推荐 `ThreadingHTTPServer` + 每请求新建 SQLite 连接；前端 HTML/CSS/原生 JS，禁止任何外部脚本/字体/CSS/CDN |
| D14 | 运行参数 | `server.py --host 0.0.0.0 --port 8000 --db <case目录>/issues.db`（均可选，默认值如左）。`--db` 支持任意路径 → 即“临时数据库测试方式”；删除该文件即重置数据 |
| D15 | JSON 键序 | JSON 对象键顺序不固定语义，测试不得依赖键顺序 |

## 2. HTTP API 契约

统一前缀 `/api`。端点总表：

| 方法 | 路径 | 用途 | 成功状态码 |
|------|------|------|-----------|
| GET | `/api/issues` | 列表（搜索/筛选/分页） | 200 |
| POST | `/api/issues` | 新增事项 | 201 |
| GET | `/api/issues/{id}` | 单条查询 | 200 |
| PUT | `/api/issues/{id}` | 全量更新 | 200 |
| DELETE | `/api/issues/{id}` | 删除 | 200 |
| GET | `/` | 前端页面 index.html | 200 (text/html) |
| GET | `/static/*` | 前端静态资源 | 200 |

事项对象（Issue Object）在所有响应中结构一致：

```json
{
  "id": 12,
  "title": "修复登录页样式",
  "status": "todo",
  "priority": "high",
  "created_at": "2026-11-28T03:20:37Z",
  "updated_at": "2026-11-28T03:20:37Z"
}
```

### 2.1 GET /api/issues — 列表 / 搜索 / 筛选 / 分页

查询参数（全部可选）：

| 参数 | 类型/取值 | 默认 | 说明 |
|------|----------|------|------|
| `keyword` | 字符串 | 无 | 标题子串匹配（SQL `LIKE '%kw%' ESCAPE '\'`）。服务端必须转义 kw 中的 `\`、`%`、`_`，使其按字面匹配；ASCII 字母大小写不敏感（SQLite LIKE 默认），非 ASCII 精确匹配。空串或纯空白 → 视为未提供 |
| `status` | `todo` \| `in_progress` \| `done` | 无 | 精确小写匹配；其他值（含大写、空串以外的非法值）→ 400 `INVALID_STATUS`；空串 → 视为未提供 |
| `page` | 十进制整数 ≥ 1 | 1 | 0、负数、小数、非数字 → 400 `INVALID_PARAM` |
| `page_size` | 十进制整数 1..100 | 10 | 越界或非法格式 → 400 `INVALID_PARAM` |

`keyword` 与 `status` 同时提供时按 AND 组合；`total`/`total_pages` 均针对过滤后的结果集。

成功响应 200：

```json
{
  "items": [ { "id": 12, "title": "修复登录页样式", "status": "todo", "priority": "high",
               "created_at": "2026-11-28T03:20:37Z", "updated_at": "2026-11-28T03:20:37Z" } ],
  "page": 1,
  "page_size": 10,
  "total": 42,
  "total_pages": 5
}
```

- `total_pages = ceil(total / page_size)`；`total = 0` 时 `total_pages = 0`。
- `page`/`page_size` 回显实际生效值。
- `page > total_pages`（且 ≥1）→ 200，`items: []`，`total`/`total_pages` 照常返回（D7）。

示例：`GET /api/issues?keyword=登录&status=todo&page=2&page_size=5`

### 2.2 POST /api/issues — 新增

请求体（JSON 对象）：

| 字段 | 必填 | 校验 |
|------|------|------|
| `title` | 是 | 必须是字符串；`strip()` 后非空（否则 400 `TITLE_REQUIRED`，含缺失/`null`/非字符串/纯空白）；`strip()` 后 ≤ 80 码点（否则 400 `TITLE_TOO_LONG`） |
| `status` | 否 | 缺失或 `null` → 默认 `todo`；提供则必须是三枚举之一（否则 400 `INVALID_STATUS`，空串也非法） |
| `priority` | 否 | 缺失或 `null` → 默认 `medium`；提供则必须是三枚举之一（否则 400 `INVALID_PRIORITY`） |

成功响应 201，返回完整事项对象（含服务端生成的 `id`、trim 后的 `title`、默认值、两个时间戳）：

```json
{ "issue": { "id": 1, "title": "修复登录页样式", "status": "todo", "priority": "high",
             "created_at": "2026-11-28T03:20:37Z", "updated_at": "2026-11-28T03:20:37Z" } }
```

示例：

```bash
curl -s -X POST http://127.0.0.1:8000/api/issues \
  -d '{"title":"  修复登录页样式  ","priority":"high"}'
# → 201，title 已 trim，status 为默认 "todo"
```

### 2.3 GET /api/issues/{id} — 单条查询

- `{id}` 非纯十进制整数 → 400 `INVALID_PARAM`。
- 记录不存在（含 id ≤ 0）→ 404 `NOT_FOUND`。
- 成功 200：`{ "issue": { ...事项对象... } }`。

### 2.4 PUT /api/issues/{id} — 全量更新

请求体：`title`、`status`、`priority` 三字段全部必填，校验规则与 POST 相同但无默认值（缺失或 `null` 即对应 400：`TITLE_REQUIRED` / `TITLE_TOO_LONG` / `INVALID_STATUS` / `INVALID_PRIORITY`）。

处理顺序（D9）：id 解析（400）→ 存在性（404 优先于 body 校验）→ body 校验（400）→ 更新。

成功 200：返回更新后的完整事项对象 `{ "issue": {...} }`，`updated_at` 刷新为本次时间，`id`/`created_at` 不变。

```bash
curl -s -X PUT http://127.0.0.1:8000/api/issues/1 \
  -d '{"title":"修复登录页样式","status":"in_progress","priority":"high"}'
```

### 2.5 DELETE /api/issues/{id} — 删除

- id 非纯整数 → 400；记录不存在 → 404（重复删除同 id 第二次必为 404）。
- 成功 200：`{ "ok": true, "id": 12 }`。删除为物理删除。

### 2.6 页面与静态资源

- `GET /` → 200 返回 `static/index.html`（`text/html; charset=utf-8`）。
- `GET /static/app.css`、`GET /static/app.js` 等 → 对应资源；目录穿越（`..`）必须被拒绝（404 或 403）。
- 静态资源 404 不要求 JSON 信封；JSON 错误信封仅保证覆盖 `/api/*`。

### 2.7 路由与方法错误

- `/api/*` 下未知路径（如 `GET /api/foo`）→ 404 `NOT_FOUND`（JSON 信封）。
- 已知路径不支持的方法（如 `POST /api/issues/1`、`DELETE /api/issues`、`PUT /api/issues`）→ 405 `METHOD_NOT_ALLOWED`（JSON 信封，建议带 `Allow` 头）。

## 3. 数据结构（SQLite）

单表 `issues`，建表 DDL（开发必须按此结构，CHECK 约束为兜底；应用层校验先于写库，正常情况下不得触发 CHECK 而返回 500）：

```sql
CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,                -- trim 后 1..80 个 Unicode 码点
    status     TEXT NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'in_progress', 'done')),
    priority   TEXT NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL,                -- UTC ISO-8601 'YYYY-MM-DDTHH:MM:SSZ'
    updated_at TEXT NOT NULL                 -- 同上；每次 PUT 成功刷新
);
CREATE INDEX IF NOT EXISTS idx_issues_created ON issues (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues (status);
```

要点：

1. 数据库是普通 SQLite 文件，路径由 `--db` 决定（默认 `cases/issue-board-20261128-032037-dsh/issues.db`）。文件持久 = 页面刷新与服务重启后数据保留（AC-12/AC-18 的依据）。
2. 启动时对已存在的库执行 `CREATE TABLE IF NOT EXISTS`（幂等迁移），不得清空既有数据。
3. 运行时生成的 `*.db`、`__pycache__/` 不得进入 PR：case 目录内提供 `.gitignore`（至少含 `*.db`、`__pycache__/`）。
4. 建议（非强制）：`server.py` 暴露 `create_server(host, port, db_path) -> HTTPServer`，便于自测绑定端口 0 并读取实际端口。

## 4. 错误码清单

错误响应统一结构（HTTP 状态码 + 稳定 `code`）：

```json
{ "error": { "code": "TITLE_TOO_LONG", "message": "标题去除两端空白后不能超过 80 字", "field": "title" } }
```

| HTTP | code | 触发场景（示例） | field |
|------|------|------------------|-------|
| 400 | `INVALID_BODY` | POST/PUT body 不是合法 JSON、不是 JSON 对象、body 缺失 | - |
| 400 | `TITLE_REQUIRED` | title 缺失/`null`/非字符串/trim 后为空（如 `"   "`） | title |
| 400 | `TITLE_TOO_LONG` | title trim 后 > 80 码点（81 即触发；80 合法） | title |
| 400 | `INVALID_STATUS` | body 中 status 非法（含空串、`"TODO"` 大写、任意其他值）；查询参数 `status` 非法 | status |
| 400 | `INVALID_PRIORITY` | body 中 priority 非法（同上规则） | priority |
| 400 | `INVALID_PARAM` | `page`/`page_size` 非法（0、负数、小数、非数字、page_size>100）；路径 id 非纯整数 | 视参数 |
| 404 | `NOT_FOUND` | 事项 id 不存在（GET/PUT/DELETE，含重复删除）；`/api/*` 未知路径 | - |
| 405 | `METHOD_NOT_ALLOWED` | 已知路径 + 不支持的方法 | - |
| 500 | `INTERNAL_ERROR` | 未捕获服务端异常（正常用例不得出现） | - |

补充规则：

- 多个字段同时非法时按 D9 顺序返回第一个错误（测试可对“多错 payload”断言确定性的单一 code）。
- PUT/DELETE 对不存在 id：404 优先于 body 校验错误。
- 测试断言 = HTTP 状态码 + `error.code`；`message` 只断言非空，不锁定文案。
- `message` 必须能作为 UI 失败原因直接展示（中文、人类可读）。

错误示例：

```bash
curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/api/issues -d '{"title":"  "}'
# 400
curl -s -X POST http://127.0.0.1:8000/api/issues -d '{"title":"  "}'
# {"error":{"code":"TITLE_REQUIRED","message":"标题去除两端空白后不能为空","field":"title"}}
curl -s -X GET http://127.0.0.1:8000/api/issues/999999
# 404 {"error":{"code":"NOT_FOUND","message":"事项不存在"}}
```

## 5. 前端与 UI 规范

页面为单页应用（`static/index.html` + `app.css` + `app.js`），功能区块与行为契约：

1. **列表区**：每条事项显示标题（原文文字）、状态中文标签（待办/进行中/完成）、优先级中文标签（低/中/高），以及每条的「编辑」「删除」操作。时间戳展示可选。
2. **新建**：「新建事项」入口打开表单（内联或弹层均可）：标题文本框（`maxlength=80`）、状态选择、优先级选择、保存/取消。保存成功 → 列表刷新（回到第 1 页、保留当前搜索/筛选条件；新事项按 D6 排序在最前，若不匹配当前筛选则不出现，属正常）。
3. **编辑**：预填当前值，提交 PUT；成功后列表原地刷新。
4. **删除**：删除前应有确认步骤（`confirm()` 或页内确认均可，二选一并在 README 说明；浏览器测试需按 README 处理对话框）；成功后该项从列表消失。
5. **搜索与筛选**：关键词输入（回车或「搜索」按钮触发，提供清空/重置）+ 状态筛选（全部状态/待办/进行中/完成）。二者映射到 `keyword`、`status` 查询参数并可组合；结果变化时同步刷新分页条。
6. **分页条**：上一页/下一页 + 位置信息（如「第 X / Y 页，共 N 条」）；首页禁用「上一页」、末页禁用「下一页」；UI 固定 `page_size=10`。
7. **空态（两种，必须可区分）**：库中无任何事项 → 「暂无事项」类文案；搜索/筛选无匹配 → 「无匹配结果」类文案。
8. **加载态**：每次拉取列表期间显示「加载中…」类提示。
9. **操作结果与失败原因**：增/改/删成功显示成功提示（如「已创建/已保存/已删除」）；任何请求失败必须把服务端 `error.message`（或网络错误描述）展示给用户。前端可做同规则客户端预校验，但服务端是权威，服务端错误必须可见。
10. **XSS 防护（硬性）**：所有用户可控文本（标题、搜索词回显、错误 message）只能经 `textContent`/`createTextNode`（或表单 `.value`）写入 DOM；**禁止**对用户数据使用 `innerHTML`/`insertAdjacentHTML`/`document.write`。含 `<script>alert(1)</script>`、`<img src=x onerror=alert(1)>` 的标题必须按字面文字显示，不弹窗、不产生注入节点。
11. **响应式（硬性）**：`<meta name="viewport" content="width=device-width, initial-scale=1">`；`box-sizing: border-box`；375px 宽与桌面（≥1280px）下均无横向溢出：`document.documentElement.scrollWidth <= window.innerWidth`。长标题（含 80 字 CJK）必须换行（`overflow-wrap`/`word-break`）或省略号截断，不得撑破容器；375px 下所有功能（增改删/搜索/筛选/翻页）可完成，建议窄屏单列卡片式布局。
12. **同源请求**：`fetch` 一律相对路径 `/api/...`，无 CORS、无外部域名请求。

## 6. 启动说明、临时数据库与开发自测（README 与 tests 要求）

case 目录建议结构（多文件为硬性要求，具体拆分可微调）：

```
cases/issue-board-20261128-032037-dsh/
├── README.md          # 启动/参数/临时库测试/自测/API 摘要/重置数据/删除确认方式说明
├── .gitignore         # *.db、__pycache__/
├── server.py          # HTTP 服务 + API（仅标准库）
├── db.py              # 可选：SQLite 建表与查询封装
├── static/
│   ├── index.html
│   ├── app.css
│   └── app.js
├── tests/
│   └── test_api.py    # 开发自测（unittest，仅标准库）
└── docs/
    └── SPEC.md        # 建议：本规格副本入库，便于 PR 评审追溯
```

README 必须包含：

1. 依赖：仅 Python ≥ 3.9，无需 pip 安装任何包。
2. 启动：`cd cases/issue-board-20261128-032037-dsh && python3 server.py`，默认监听 `0.0.0.0:8000`，浏览器访问 `http://127.0.0.1:8000/`；`--host/--port/--db` 参数说明。
3. 临时数据库测试方式：`python3 server.py --db /tmp/issues-test.db --port 8080`；删除该文件即清空数据；默认库文件位置与重置方法。
4. 开发自测运行方式（单条命令）：`python3 -m unittest discover -s tests -v`。
5. API 端点摘要（与第 2 节一致）与删除确认交互方式的说明。

开发自测（tests/test_api.py）要求：

- 仅标准库（`unittest` + `urllib.request` 等）；使用临时文件数据库与临时端口（绑定 0 或随机高位端口），不得写入/依赖默认 `issues.db`，不得依赖固定网络环境。
- 覆盖至少：CRUD 正常流；标题校验（空/纯空白/80 边界/81）；非法状态/优先级；404 与非整数 id；分页计算与越界页；keyword 通配符转义（`%`/`_` 字面匹配）；status 筛选与组合；重启/重建服务后数据保留（同一 `--db` 文件重建 server 实例即可）。
- 全部用例必须在开发环境通过后才允许提交草稿 PR（开发节点的自证，不替代独立测试）。

## 7. 验收标准（AC 编号，供测试设计与最终验收引用）

**功能 / API**

- AC-01 新增：合法 title 的 POST → 201，返回含正整数 id、trim 后 title、默认 `status=todo`、`priority=medium`、两个 ISO-8601Z 时间戳；GET 单条与之一致。
- AC-02 新增（显式枚举）：POST 携带合法 `status`/`priority` → 201 且原样存储返回。
- AC-03 列表：GET `/api/issues` → 200，信封 `{items,page,page_size,total,total_pages}`，默认 page=1、page_size=10，按 `created_at DESC, id DESC` 排序。
- AC-04 更新：PUT 全字段合法 → 200，返回更新后对象，`updated_at` 刷新、`id`/`created_at` 不变；后续 GET 反映修改。
- AC-05 删除：DELETE → 200 `{ok:true,id}`；随后 GET 该 id → 404；列表不再包含；重复 DELETE → 404。
- AC-06 搜索：`keyword` 对标题子串匹配，ASCII 大小写不敏感；`%`、`_` 按字面匹配（`keyword=100%` 不匹配 `1001`）；与 `status` 组合为 AND。
- AC-07 筛选：`status=todo|in_progress|done` 精确过滤；非法值 → 400 `INVALID_STATUS`；空串视为未过滤。
- AC-08 分页：`page`/`page_size` 生效且回显；`total` 为过滤后总数；`total_pages=ceil(total/page_size)`（total=0 时为 0）；越界页 → 200 空 items；`page=0`/`page_size=0`/`page_size=101`/非数字 → 400 `INVALID_PARAM`。
- AC-09 标题校验：缺失/`null`/非字符串/纯空白 → 400 `TITLE_REQUIRED`；trim 后 81 码点 → 400 `TITLE_TOO_LONG`；trim 后恰 80 → 201；两端空白被 trim 后存储；多字段同错时按 D9 顺序返回单一确定错误。
- AC-10 枚举校验：非法 status → 400 `INVALID_STATUS`；非法 priority → 400 `INVALID_PRIORITY`；PUT 缺字段/`null` 同样触发对应 400；POST 缺省走默认值。
- AC-11 路由与输入错误：不存在 id（GET/PUT/DELETE）→ 404 `NOT_FOUND`（PUT 对不存在 id 时 404 优先于 body 校验）；非整数 id → 400 `INVALID_PARAM`；非法 JSON body → 400 `INVALID_BODY`；未知 `/api/*` 路径 → 404；已知路径错误方法 → 405；错误一律为 D3 JSON 信封。
- AC-12 重启持久化：写入数据 → 停止服务进程 → 以同一 `--db` 重新启动 → GET 列表数据完整保留（id/title/status/priority/时间戳不变）。

**UI（浏览器）**

- AC-13 UI 增改删：通过页面完成新建（表单）、编辑（改标题/状态/优先级）、删除（含确认步骤），每步列表即时更新，无需手动刷新。
- AC-14 UI 搜索/筛选/分页/空态：关键词搜索与状态筛选（含组合）正确更新列表与分页条；筛选无结果显示「无匹配」空态；空库显示「暂无事项」空态，两种空态可区分。
- AC-15 加载与结果反馈：列表拉取期间出现加载提示；增/改/删成功有成功提示；请求失败时页面展示服务端 `error.message` 等失败原因（可通过双标签页删除后再编辑同一事项触发 404 验证）。
- AC-16 XSS 文字显示：标题 `<script>alert(1)</script>` 与 `<img src=x onerror=alert(1)>` 创建成功后，列表与编辑框中按字面文字显示，无弹窗、无注入的 script/img 节点。
- AC-17 响应式：375×667 与桌面 ≥1280px 宽度下均可完成增改删/搜索/筛选/翻页操作，且 `scrollWidth <= innerWidth`（无横向滚动条/溢出）。
- AC-18 刷新持久化：创建数据后浏览器刷新（F5），数据完整显示。

**技术约束 / 交付物**

- AC-19 零外部依赖：后端源码 import 仅限 Python 标准库；前端无 CDN/外部脚本/字体/样式引用；在仅装 Python 3.9+ 的干净环境可运行。
- AC-20 范围限定：应用全部文件位于 `cases/issue-board-20261128-032037-dsh/`，经唯一草稿 PR 交付；PR 不含 `*.db`、`__pycache__` 等运行时产物。
- AC-21 README：包含第 6 节要求的启动说明、参数、临时数据库测试方式、自测命令、API 摘要、数据重置与删除确认方式说明，且照做可复现。
- AC-22 开发自测：`python3 -m unittest discover -s tests -v` 在 case 目录一次通过，覆盖第 6 节列出的最小范围，且使用临时库不污染默认数据。
- AC-23 存储与结构：数据落在 `--db` 指定的 SQLite 文件；表结构与第 3 节 DDL 一致（列、默认值、CHECK、主键自增）。
- AC-24 无越界功能：无登录/鉴权、无云部署配置、无第 1-6 节之外的额外功能面。

## 8. 测试设计指引（建议矩阵；test_design 节点拥有最终用例，数量下限为独立测试节点要求：≥10 API 用例、≥5 浏览器场景）

### 8.1 API / 持久化 / 错误边界（建议 12+，映射 AC）

| # | 用例 | 期望 | AC |
|---|------|------|----|
| A1 | POST 默认值创建 | 201，todo/medium，id>0，时间戳格式 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | AC-01 |
| A2 | POST 显式枚举 + 两端空白标题 | 201，title 已 trim，枚举原样 | AC-02/09 |
| A3 | POST 标题边界：80 字 / 81 字 / 纯空白 / 缺失 / 非字符串 | 201 / 400 TITLE_TOO_LONG / 400 TITLE_REQUIRED ×3 | AC-09 |
| A4 | POST 非法 status、非法 priority、多字段同错 | 400 INVALID_STATUS、400 INVALID_PRIORITY、按 D9 单一 code | AC-09/10 |
| A5 | POST 非法 JSON / 空 body | 400 INVALID_BODY | AC-11 |
| A6 | PUT 修改三字段 / PUT 不存在 id / PUT 缺字段 | 200 且 updated_at 刷新 / 404 / 400 | AC-04/10/11 |
| A7 | DELETE → GET → 再 DELETE | 200 / 404 / 404 | AC-05 |
| A8 | GET 非整数 id（`abc`、`1.5`、`-1a`） | 400 INVALID_PARAM | AC-11 |
| A9 | keyword：子串、ASCII 大小写、`100%` 与 `a_c` 字面匹配、与 status 组合 | 命中集正确、无通配符误匹配 | AC-06 |
| A10 | status 筛选合法三值 + 非法值 + 空串 | 过滤正确 / 400 INVALID_STATUS / 视为未过滤 | AC-07 |
| A11 | 分页：造 25 条，page_size=10 三页 total_pages=3；page_size=0/101/page=0/page=x → 400；page=99 → 200 空 items | 信封字段全部正确 | AC-08 |
| A12 | 重启持久化：创建→杀进程→同 db 重启→列表比对（含字段级一致） | 数据完整保留 | AC-12 |
| A13 | 路由：GET /api/foo → 404；DELETE /api/issues → 405 | JSON 信封 code 正确 | AC-11 |

### 8.2 浏览器场景（建议 6，覆盖需求的 5 项必测：增改删、搜索/筛选、刷新持久化、375px、XSS）

| # | 场景 | 覆盖 |
|---|------|------|
| B1 | 页面新建事项（含中文与特殊字符标题）→ 列表出现、成功提示、默认标签「待办/中」 | AC-13/15 |
| B2 | 编辑改状态为「进行中」、优先级「高」、改标题 → 列表即时更新；再删除（处理确认对话框）→ 消失 | AC-13 |
| B3 | 关键词搜索 + 状态筛选 + 组合 + 清空；翻页到第 2 页再返回；筛选无匹配显示「无匹配」空态 | AC-14 |
| B4 | 创建数据 → F5 刷新 → 数据在；（可选加强）测试侧重启服务进程后再刷新 → 数据仍在 | AC-18/12 |
| B5 | 375×667 视口：完成一次新增+搜索+翻页，`scrollWidth <= innerWidth`；桌面 1280px 同断言 | AC-17 |
| B6 | XSS：创建 `<script>alert(1)</script>` 与 `<img src=x onerror=alert(1)>` 标题 → 页面按文字显示、监听无 dialog、DOM 无注入节点；编辑框回显同为字面值 | AC-16 |

失败原因展示（AC-15）建议随 B2 附加：双标签页删除某事项后，在另一标签页编辑保存它 → 页面显示 404 失败原因。

### 8.3 浏览器测试环境前提（testing 节点执行约束）

1. 独立测试必须固定草稿 PR 的远端 head SHA，在独立工作区检出运行；报告记录真实命令、结果与该 SHA，并区分「已通过 / 失败 / 未验证」。
2. browser-dev-team 浏览器无法访问 Worker 的 localhost：被测服务须以网络可达地址提供（server 默认绑 `0.0.0.0`，用 `http://<主机可达IP>:<端口>/` 作为预览地址）。
3. 测试期间自行启动服务、使用临时 `--db`，测试结束停止进程并清理临时文件。
4. 浏览器安装属平台前提；若环境缺失，如实上报 Leader，不得虚构测试结果。
5. 测试用例是 test_design 节点的正式交付物；testing 不得为了塞入测试文件而改动已测的 PR 提交（测试脚本放在独立测试工作区，不进 PR）。

## 9. 开发节点交付清单（development 据此实现并自测）

1. `cases/issue-board-20261128-032037-dsh/` 下：README.md、.gitignore、server.py（可拆分 db.py）、static/index.html、static/app.css、static/app.js、tests/test_api.py；建议附 docs/SPEC.md（本报告副本）。
2. 实现第 2-5 节全部契约与规则；第 6 节 README/自测要求。
3. 自测通过后经 GitHub 网关 MCP：建分支 → 推送 → 创建唯一草稿 PR（关联 Issue #91）；不使用宿主机 git/gh 凭证，Token 不出现在任何输入/文件/报告中。
4. PR 描述建议注明：实现范围、启动方式、自测命令与结果、对应 Issue。
5. 本验收 PR 暂不合并；独立测试通过后由原开发将同一 PR 转 ready_for_review 并回执。

## 10. 范围外（明确不做）

登录/鉴权、多用户、云部署、标签/描述/截止日期等额外字段、排序参数、批量操作、导入导出、国际化、PWA、任何第三方运行依赖、数据库文件之外的存储。

—— 以上契约自本报告提交起对 development 与 test_design 同时生效；实现或用例需要变更契约时，通过 Leader 走任务修订，不做口头偏离。
