# 事项管理应用（issue-board-20261128-032037-dsh）

可本地运行的多文件事项管理应用：Python 标准库 HTTP 服务 + SQLite 后端，
HTML/CSS/原生 JS 前端，**零外部运行依赖**。

关联 Issue：[#91 研发小队复杂验收 20261128-032037 dsh：事项管理应用](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/91)
契约规格：[docs/SPEC.md](docs/SPEC.md)（analysis 节点产出副本）

## 功能

- 事项字段：标题（trim 后必填且 ≤ 80 字）、状态（待办/进行中/完成）、优先级（低/中/高）
- 新增、编辑（全量更新）、删除（原生 `confirm()` 确认对话框）
- 关键词搜索（标题子串、ASCII 大小写不敏感、`%`/`_`/`\` 按字面匹配）与状态筛选，可 AND 组合
- 分页：固定每页 10 条，`page`/`page_size`（1..100）可传参；越界页返回空列表
- 持久化：数据落在 SQLite 文件（`--db` 指定），页面刷新与服务重启后数据保留
- 统一错误信封 `{"error":{"code","message","field"?}}`，页面展示失败原因
- 用户文本一律按文字显示（`textContent` 渲染，不执行 HTML）
- 桌面与 375px 宽均可操作且无横向溢出

## 依赖

仅需 Python ≥ 3.9（标准库），无需 pip 安装任何包。

## 启动

```bash
cd cases/issue-board-20261128-032037-dsh
python3 server.py
# 默认监听 0.0.0.0:8000，浏览器访问 http://127.0.0.1:8000/
```

参数（均可选）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `8000` | 监听端口（`0` 表示随机可用端口，实际端口见启动输出） |
| `--db` | case 目录下 `issues.db` | SQLite 数据库文件路径 |

## 临时数据库测试方式

```bash
python3 server.py --db /tmp/issues-test.db --port 8080
```

- 数据全部写入 `--db` 指定的文件，与默认库互不影响；
- **删除该文件即清空数据**（重置）；默认库文件为 case 目录下 `issues.db`，
  同样直接删除即可重置（`*.db` 已加入 `.gitignore`，不会入库）。

## 开发自测

在 case 目录下执行（临时库 + 随机端口，不污染默认 `issues.db`）：

```bash
python3 -m unittest discover -s tests -v
```

覆盖：CRUD 正常流、标题校验（空/纯空白/非字符串/80 与 81 边界/CJK 码点）、
非法状态/优先级、非法 JSON/空 body、404 与非整数 id、PUT 404 优先于 body 校验、
分页计算与越界页、keyword 通配符（`%`/`_`/`\`）字面转义、status 筛选与 AND 组合、
参数校验顺序、重复参数取第一个、路由 404/405（含 Allow 头）、静态资源与目录穿越拦截、
进程内重建与**真实子进程 kill 后同库重启**的持久化、表结构 DDL 校验。

## API 摘要

统一前缀 `/api`；错误响应一律 `{"error":{"code","message","field"?}}`。

| 方法 | 路径 | 用途 | 成功码 |
|------|------|------|--------|
| GET | `/api/issues` | 列表；参数 `keyword`、`status`、`page`（默认1）、`page_size`（默认10，1..100） | 200 |
| POST | `/api/issues` | 新增；body `{"title","status"?,"priority"?}`，缺省 `todo`/`medium` | 201 |
| GET | `/api/issues/{id}` | 单条查询 | 200 |
| PUT | `/api/issues/{id}` | 全量更新；`title`/`status`/`priority` 全部必填 | 200 |
| DELETE | `/api/issues/{id}` | 删除（物理删除），返回 `{"ok":true,"id":...}` | 200 |
| GET | `/` 与 `/static/*` | 前端页面与静态资源 | 200 |

错误码：`TITLE_REQUIRED`、`TITLE_TOO_LONG`、`INVALID_STATUS`、`INVALID_PRIORITY`、
`INVALID_BODY`、`INVALID_PARAM`（400）；`NOT_FOUND`（404）；
`METHOD_NOT_ALLOWED`（405，带 `Allow` 头）；`INTERNAL_ERROR`（500）。
详细语义与校验顺序见 [docs/SPEC.md](docs/SPEC.md)。

示例：

```bash
curl -s -X POST http://127.0.0.1:8000/api/issues \
  -d '{"title":"  修复登录页样式  ","priority":"high"}'
curl -s 'http://127.0.0.1:8000/api/issues?keyword=登录&status=todo&page=1&page_size=10'
curl -s -X PUT http://127.0.0.1:8000/api/issues/1 \
  -d '{"title":"修复登录页样式","status":"in_progress","priority":"high"}'
curl -s -X DELETE http://127.0.0.1:8000/api/issues/1
```

## 删除确认交互方式

删除按钮触发**浏览器原生 `confirm()` 对话框**（文案含事项标题）。
自动化/浏览器测试需要用工具的对话框处理能力接受或取消该对话框
（如 Playwright `page.on("dialog")` / browser-dev-team `browser_handle_dialog`）。

## 目录结构

```
cases/issue-board-20261128-032037-dsh/
├── README.md          # 本文件
├── .gitignore         # *.db、__pycache__/ 等运行产物不入库
├── server.py          # HTTP 服务 + API（仅标准库；create_server 可绑定端口 0）
├── db.py              # SQLite 建表与数据访问封装
├── static/
│   ├── index.html     # 单页应用
│   ├── app.css        # 样式（375px 响应式，无横向溢出）
│   └── app.js         # 原生 JS（textContent 渲染，无 innerHTML）
├── tests/
│   └── test_api.py    # 开发自测（unittest + urllib，临时库 + 随机端口）
└── docs/
    └── SPEC.md        # analysis 节点契约副本
```

## 实现说明（与契约的对应）

- 排序固定 `created_at DESC, id DESC`，分页确定性（D6）；越界页 200 空 items（D7）。
- 时间戳为 UTC ISO-8601 秒级 `YYYY-MM-DDTHH:MM:SSZ`（D5）；PUT 成功必刷新
  `updated_at`（同秒内可能等于旧值）。
- 校验顺序（D9）：PUT/DELETE 先 id 解析（400）→ 存在性（404 优先）→ body 校验；
  POST body：JSON 解析 → title → status → priority；列表参数：status → page → page_size。
- body 不论 `Content-Type` 一律按 JSON 解析；重复查询参数取第一个值；
  未知字段忽略；POST 中 `status`/`priority` 为 `null` 视同缺省，PUT 中缺省或
  `null` 均非法（D10/D2）。
- 每请求新建 SQLite 连接（ThreadingHTTPServer 并发安全）；启动时
  `CREATE TABLE IF NOT EXISTS` 幂等迁移，不清空既有数据。
- 无 CORS 配置：前端与 API 同源，一律相对路径 `/api/...`（D12）。
