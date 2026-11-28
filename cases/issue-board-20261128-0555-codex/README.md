# 事项管理应用（Issue Board）

多文件事项管理应用：后端为 Python 标准库 `http.server` + `sqlite3`，前端为原生
HTML/CSS/JavaScript，**零外部运行依赖**（无 pip 包、无 npm 包、无 CDN 资源）。
支持事项的新增 / 编辑 / 删除、标题关键词搜索、状态筛选、分页，数据持久化在本地
SQLite 文件中，页面刷新与服务重启后数据仍保留。

对应 Issue：[#107 研发小队复杂验收 20261128-0555 codex：事项管理应用](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/107)

## 1. 环境要求

- Python 3.9 及以上（开发与自测环境：Python 3.11.2 / SQLite 3.40.1）。
- 仅使用标准库，无需 `pip install`，无需 Node 或任何前端构建步骤。
- 现代浏览器（使用 `fetch`、ES5+ 语法，无框架）。

## 2. 启动

```bash
cd cases/issue-board-20261128-0555-codex
python3 server.py --host 0.0.0.0 --port 8000
# 打开 http://127.0.0.1:8000/
```

启动时会向 stdout 打印监听地址与数据库绝对路径，便于记录测试证据：

```text
[issue-board] listening on http://0.0.0.0:8000/
[issue-board] local url  http://127.0.0.1:8000/
[issue-board] lan url    http://172.17.0.2:8000/
[issue-board] database: /abs/path/cases/issue-board-20261128-0555-codex/data/items.sqlite3
```

停止服务：前台运行时按 `Ctrl+C`。

> 浏览器远程验证请使用 `lan url`（或宿主机可达的 `IP:端口`）。远端浏览器无法访问
> 服务所在机器的 `127.0.0.1` / `localhost`。

## 3. 指定数据库

```bash
python3 server.py --port 8000 --db ./data/items.sqlite3
```

数据库父目录会自动创建；库文件不存在时自动建表（`CREATE TABLE IF NOT EXISTS`，
**不会清空已有数据**）。`--db :memory:` 被显式拒绝，因为每个请求使用独立连接。

## 4. 临时数据库（一次性验证，推荐用于测试）

```bash
TMPDB="$(mktemp -d)/items.sqlite3"
python3 server.py --host 0.0.0.0 --port 8123 --db "$TMPDB"
# 验证结束后停止进程，并删除该临时目录（只删除自己创建的精确路径）
```

## 5. 持久化验证方式

1. 启动服务：`python3 server.py --port 8000 --db ./data/items.sqlite3`
2. 在页面或用 `curl` 新增几条事项；
3. `Ctrl+C` 停止进程；
4. 用**同一 `--db` 路径**重新启动；
5. 列表内容与 `created_at` 完整保留（`GET /api/items` 可直接核对 `total`）。

换一个全新的 `--db` 路径启动时列表为空并显示空态，与既有数据互不影响。

## 6. 开发自测

```bash
cd cases/issue-board-20261128-0555-codex
python3 -m unittest discover -s tests -t . -v
```

自测（`tests/test_api.py`，44 个用例）在 `tempfile.mkdtemp()` 创建的临时目录中建立
文件数据库，用 `ThreadingHTTPServer(('127.0.0.1', 0), handler)` 绑定空闲端口，通过
`urllib.request` 与原始 socket 发送真实 HTTP 请求，`tearDown` 中停止服务并只删除自己
创建的临时目录。覆盖：契约与表结构、新增与标题 strip/长度边界、非法状态与优先级、
非法 JSON 与多余字段、校验失败不写库、编辑与删除、404/400/405、空 id 与尾斜杠路径口径、
搜索（含 `%`、`_`、`\` 字面量转义与 ASCII 大小写不敏感）、状态筛选与组合 AND、分页派生字段
与非法分页、默认排序与翻页无重复无遗漏、重启持久化、413、静态穿越防护、UTF-8 往返、
500 统一错误体。

## 7. 接口速览

| 方法 | 路径 | 用途 | 成功码 |
| --- | --- | --- | --- |
| GET | `/api/meta` | 枚举、默认值、标题长度与分页限制 | 200 |
| GET | `/api/items` | 列表：`q` 搜索 + `status` 筛选 + `page` / `page_size` 分页 | 200 |
| POST | `/api/items` | 新增（`title` / `status` / `priority` 三键齐备） | 201 + `Location` |
| GET | `/api/items/{id}` | 单条查询 | 200 |
| PUT | `/api/items/{id}` | 全字段更新 | 200 |
| DELETE | `/api/items/{id}` | 删除 | 200 |
| GET | `/`、`/index.html`、`/styles.css`、`/app.js` | 页面与静态资源 | 200 |

取值：状态 `todo`（待办）/ `in_progress`（进行中）/ `done`（完成），默认 `todo`；
优先级 `low`（低）/ `medium`（中）/ `high`（高），默认 `medium`。标题去两端空白后
必填且不超过 80 个字符（按 Unicode 字符计数，中文按 1 字计）。

```bash
# 新增
curl -sS -X POST http://127.0.0.1:8000/api/items \
  -H 'Content-Type: application/json' \
  -d '{"title":"  买牛奶  ","status":"todo","priority":"medium"}'

# 搜索 + 状态筛选 + 分页
curl -sS 'http://127.0.0.1:8000/api/items?q=%E7%89%9B%E5%A5%B6&status=todo&page=1&page_size=10'

# 编辑 / 删除
curl -sS -X PUT http://127.0.0.1:8000/api/items/1 \
  -H 'Content-Type: application/json' \
  -d '{"title":"买豆奶","status":"in_progress","priority":"high"}'
curl -sS -X DELETE http://127.0.0.1:8000/api/items/1
```

错误响应统一为 JSON（示例）：

```json
{"error": {"code": "TITLE_REQUIRED", "message": "标题去除两端空白后不能为空", "field": "title", "details": {}}}
```

错误码：`INVALID_JSON`、`INVALID_FIELD`、`TITLE_REQUIRED`、`TITLE_TOO_LONG`、
`INVALID_STATUS`、`INVALID_PRIORITY`、`INVALID_ID`、`INVALID_PAGE`、
`INVALID_PAGE_SIZE`、`ITEM_NOT_FOUND`(404)、`NOT_FOUND`(404)、
`METHOD_NOT_ALLOWED`(405，带 `Allow` 头)、`PAYLOAD_TOO_LARGE`(413)、
`INTERNAL_ERROR`(500)。校验顺序：请求体大小 → id 格式 → JSON 格式 → 未知字段 →
标题 → 状态 → 优先级 → 记录是否存在；校验失败不会产生任何写入。

路径匹配口径：路由按 URL 解码后的**字面分段**匹配，不做尾斜杠吞并，也不做 `.`/`..` 折叠。
`/api/items/{id}` 的 `{id}` 必须是单个分段；`/api/items/`（`{id}` 为空）仍按 `{id}` 路由处理，
`GET`/`PUT`/`DELETE` 返回 400 `INVALID_ID`，`POST` 返回 405 + `Allow: GET, PUT, DELETE`
且不产生任何写入。带尾斜杠或多余分段的路径（`/api/items/1/`、`/api/items//1`、
`/api/items/%2f`、`/api/meta/`）属未定义路径，返回 404 `NOT_FOUND`。静态资源穿越
（如 `/../server.py`）由 realpath 校验拦截，返回 404 纯文本。

## 8. 约束声明

- 零外部运行依赖：仅 Python 标准库与浏览器原生能力；前端不引用任何外部
  `http(s)` 资源，可完全离线运行。
- 数据仅保存在本地 SQLite 文件中（默认 `./data/items.sqlite3`，已被 `.gitignore` 忽略）。
- **无鉴权、无加密**：服务默认监听 `0.0.0.0`，同网段可访问，请勿存放敏感信息，
  也不要把该服务直接暴露到公网。
- 用户文本（标题、搜索词）一律以纯文字渲染，页面不执行标题中的 HTML/脚本；
  服务端按原文存储，不做转义或过滤。

## 9. 目录结构

```text
cases/issue-board-20261128-0555-codex/
├── .gitignore
├── README.md
├── server.py              # 启动入口：参数解析 → 建库 → ThreadingHTTPServer
├── issueboard/
│   ├── __init__.py
│   ├── db.py              # SQLite 连接管理、建表、PRAGMA busy_timeout
│   ├── repository.py      # 参数化 SQL（LIKE 通配符转义、固定排序）
│   ├── service.py         # 校验顺序、错误码、业务用例
│   ├── api.py             # 路由、JSON 编解码、统一错误响应、静态穿越防护
│   └── static/
│       ├── index.html     # 页面结构（含 data-testid 稳定选择器）
│       ├── styles.css     # 桌面与 375px 响应式样式
│       └── app.js         # 交互逻辑，全部用 textContent 渲染用户文本
└── tests/
    ├── __init__.py
    └── test_api.py        # 开发自测：真实端口 + 临时目录文件库
```

## 10. 配置优先级

| 参数 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--host` | `ISSUE_BOARD_HOST` | `0.0.0.0` | 监听地址，远程浏览器验证需保持 `0.0.0.0` |
| `--port` | `ISSUE_BOARD_PORT` | `8000` | 端口被占用时打印原因并以退出码 2 结束 |
| `--db` | `ISSUE_BOARD_DB` | `data/items.sqlite3` | 相对当前工作目录，父目录自动创建 |

优先级：命令行参数 > 环境变量 > 默认值；`python3 server.py --help` 可查看完整说明。
