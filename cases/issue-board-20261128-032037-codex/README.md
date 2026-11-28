# 事项管理应用（Issue Board）

对应 Issue：[#92 研发小队复杂验收 20261128-032037 codex：事项管理应用](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/92)

一个**本地运行**的多文件事项管理应用：后端为 Python 标准库 HTTP 服务 + SQLite，
前端为原生 HTML/CSS/JS，**零外部运行依赖**（无需 pip / npm 安装，无需联网）。

---

## 1. 功能简介

- 事项包含**标题**、**状态**（待办 / 进行中 / 完成）与**优先级**（低 / 中 / 高）。
- 支持**新增、编辑、删除**（删除为页面内二次确认，不使用浏览器原生弹窗）。
- 支持**关键词搜索**、**状态筛选**、**分页**（每页 10 / 20 / 50 条）。
- 数据写入 SQLite 文件，**页面刷新与服务重启后数据仍保留**。
- 校验：标题去除两端空白后必填且不超过 80 个字符（按 Unicode 码点计数）；
  非法状态 / 优先级、非法 id、非法分页参数、不存在的记录均返回明确的错误码与中文提示。
- 界面：加载中、空态（`暂无事项，添加第一条吧`）、筛选无匹配（`没有符合条件的事项`）、
  操作成功与失败原因均可见；用户输入一律按**纯文字**渲染，不会执行 HTML。
- 响应式：桌面（1280px）与手机（375px）宽度下均可正常操作，无横向溢出。

页面布局（自上而下）：标题区 → 新增/编辑表单 → 列表工具条（搜索、状态筛选、每页条数）→
提示区 / 加载中 / 空态 → 事项卡片列表 → 分页区。

## 2. 环境要求

- Python **≥ 3.9**（仅使用标准库：`http.server`、`socketserver`、`sqlite3`、`json`、
  `argparse`、`urllib.parse`、`datetime`、`os`、`math`、`re`、`unittest`）。
- 任意现代浏览器（Chromium / Firefox / Safari）。
- 无需构建步骤、无需安装任何第三方包、首次启动不访问网络。

## 3. 启动命令与参数

```bash
cd cases/issue-board-20261128-032037-codex
python3 -m app.server --host 127.0.0.1 --port 8000 --db data/issues.db
```

启动后访问 <http://127.0.0.1:8000/>；服务会在 stderr 打印一行就绪信息：

```
issue-board listening on http://127.0.0.1:8000 db=/绝对路径/data/issues.db
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址。需要被**其它机器/容器中的浏览器**访问时请用 `0.0.0.0` |
| `--port` | `8000` | 监听端口。端口被占用时请换一个端口（服务端不自动重试） |
| `--db` | `data/issues.db` | SQLite 文件路径；相对路径以**当前工作目录**解析，父目录不存在时自动创建 |

停止服务：前台运行时按 `Ctrl-C`；后台运行时 `kill <pid>`。数据已逐次提交，停止不会丢数据。

> `python3 -m app` 与 `python3 -m app.server` 等价。请在 case 目录内用 `-m` 方式启动
> （直接 `python3 app/server.py` 会因包内相对导入而失败）。

## 4. 快速上手

1. **新增**：在「标题」输入框填写内容，选择状态与优先级，点击「新增事项」
   （或在标题框内按 Enter）。成功后表单清空并提示「已新增：…」，新事项出现在列表首位。
2. **搜索**：在「关键词」输入框输入文字，点击「搜索」或按 Enter；页码会重置为第 1 页。
   关键词按标题子串匹配，ASCII 字母大小写不敏感，`%` 与 `_` 按字面量匹配。
3. **筛选**：在「状态筛选」下拉框选择 全部 / 待办 / 进行中 / 完成，立即生效并回到第 1 页。
   搜索与筛选为 **AND** 关系。
4. **翻页**：使用「上一页 / 下一页」按钮；`第 X / Y 页，共 Z 条` 显示当前位置。
   切换「每页」条数会回到第 1 页；翻页保留搜索与筛选条件。
5. **编辑**：点击某条事项的「编辑」，表单切换为编辑态并回填三个字段，
   修改后点击「保存修改」；点击「取消」放弃修改回到新增态。
6. **删除**：点击「删除」，在页面内确认框中点击「确认删除」；点击「取消」则数据不变。

## 5. 临时数据库测试方式（推荐，不污染默认数据）

```bash
cd cases/issue-board-20261128-032037-codex
TMPDIR_DB="$(mktemp -d)"
python3 -m app.server --host 127.0.0.1 --port 8000 --db "$TMPDIR_DB/issues.db"

# 另开一个终端：
curl -s -X POST localhost:8000/api/v1/items -d '{"title":"买牛奶"}'
curl -s 'localhost:8000/api/v1/items?page=1&page_size=20'
curl -s -X PUT  localhost:8000/api/v1/items/1 -d '{"title":"买豆浆","status":"done","priority":"high"}'
curl -s -X DELETE localhost:8000/api/v1/items/1

# 结束后清理：Ctrl-C 停止服务，再删除自己创建的临时目录
rm -rf "$TMPDIR_DB"
```

需要让**其它机器/容器中的浏览器**访问时（例如远程浏览器自动化），改用：

```bash
python3 -m app.server --host 0.0.0.0 --port 8123 --db "$TMPDIR_DB/issues.db" &
until curl -fsS http://127.0.0.1:8123/api/v1/health >/dev/null; do sleep 0.5; done
```

## 6. 开发自测

```bash
cd cases/issue-board-20261128-032037-codex
python3 -B -m unittest discover -s tests -v
```

- 共 **42** 个用例，覆盖新增/列表/详情/编辑/删除、搜索与筛选、分页边界、标题边界
  （空 / 纯空白 / 80 / 81 / emoji）、非法枚举（字符串非法值 → `INVALID_STATUS`/
  `INVALID_PRIORITY`；非字符串值 → `INVALID_BODY`）、非法请求体、1 MiB 体积上限、
  非法与不存在 id、PUT 校验顺序、路由与 405 `Allow` 头、静态资源白名单与路径穿越、
  错误响应体不泄漏内部信息、**重启后数据保留**。
- 每个用例都在 `tempfile.TemporaryDirectory()` 内建独立数据库，并用
  `ThreadingHTTPServer((host, 0))` 取随机端口，**不会**读写默认 `data/issues.db`，可重复运行。
- `-B` 用于避免生成 `__pycache__/`。

## 7. API 摘要

基础路径 `/api/v1`；请求体为 JSON（UTF-8）；响应统一为
`{"ok": true, "data": …}` 或 `{"ok": false, "error": {"code", "message", "field?", "details?"}}`。

| 方法与路径 | 说明 | 成功状态码 |
|---|---|---|
| `GET /api/v1/health` | 就绪探测，返回 `{"status":"ok","version":"1.0.0"}` | 200 |
| `GET /api/v1/meta` | 状态/优先级枚举、每页选项、标题长度上限 | 200 |
| `GET /api/v1/items` | 列表；查询参数 `q`、`status`、`page`(默认 1)、`page_size`(默认 20，1–100)；排序固定 `created_at DESC, id DESC` | 200 |
| `POST /api/v1/items` | 新增；`title` 必填（strip 后 1–80 字符），`status` 默认 `todo`，`priority` 默认 `medium` | 201 |
| `GET /api/v1/items/{id}` | 详情 | 200 |
| `PUT /api/v1/items/{id}` | 编辑（部分更新语义）：`title` 必填；`status`/`priority` 缺省或 `null` 表示保持原值 | 200 |
| `DELETE /api/v1/items/{id}` | 硬删除，返回 `{"id":N,"deleted":true}`；重复删除返回 404 | 200 |

Item 对象：

```json
{
  "id": 12,
  "title": "编写接口契约",
  "status": "todo",
  "priority": "medium",
  "created_at": "2026-11-28T03:20:37Z",
  "updated_at": "2026-11-28T03:20:37Z"
}
```

列表响应还包含 `page`、`page_size`、`total`、`total_pages`、`q`、`status`
（未筛选时 `status` 为 `null`）。`page` 超出总页数时返回 200 且 `items` 为空数组。

### 错误码

| HTTP | `code` | 触发场景 | `field` |
|---|---|---|---|
| 400 | `INVALID_BODY` | 空 body / 非法 JSON / 顶层非对象 / `title` 不是字符串（含 `null`）/ `status`、`priority` 不是字符串（如 `123`、`true`、`[]`、`{}`；显式 `null` 表示走默认值） | 出错字段或省略 |
| 400 | `TITLE_REQUIRED` | `title` 缺失、`""`、纯空白（含全角空格） | `title` |
| 400 | `TITLE_TOO_LONG` | strip 后长度 > 80（按 Unicode 码点计） | `title` |
| 400 | `INVALID_STATUS` | `status` 是字符串但不在 `todo`/`doing`/`done` 内（大小写敏感），含显式空串 | `status` |
| 400 | `INVALID_PRIORITY` | `priority` 是字符串但不在 `low`/`medium`/`high` 内（大小写敏感），含显式空串 | `priority` |
| 400 | `INVALID_ID` | 路径 id 不匹配 `^[1-9][0-9]*$`（如 `abc`、`0`、`-1`、`1.5`） | `id` |
| 400 | `INVALID_PAGE` | `page` 非十进制整数或 < 1（含空串、`1.5`、`+1`） | `page` |
| 400 | `INVALID_PAGE_SIZE` | `page_size` 非整数或不在 1–100 | `page_size` |
| 404 | `ITEM_NOT_FOUND` | id 合法但记录不存在；重复删除 | `id` |
| 404 | `NOT_FOUND` | 未知 API 路径；未提供的静态文件；路径穿越 | — |
| 405 | `METHOD_NOT_ALLOWED` | 已知路径 + 不支持的方法（响应头含 `Allow`） | — |
| 413 | `PAYLOAD_TOO_LARGE` | 请求体 > 1 MiB | — |
| 500 | `INTERNAL_ERROR` | 未预期异常（详情只写 stderr，不进响应体） | — |

`PUT` 的校验顺序为：`INVALID_ID` → `INVALID_BODY` → `ITEM_NOT_FOUND` → 字段校验，
即对不存在的记录提交非法标题时返回 **404**（存在性优先）。字段校验内部同样遵循
「先类型、后取值」：`status`/`priority` 不是字符串时返回 `INVALID_BODY`，是字符串但
不在枚举内时返回 `INVALID_STATUS`/`INVALID_PRIORITY`。

## 8. 目录结构

```
cases/issue-board-20261128-032037-codex/
├── README.md            # 本文件
├── .gitignore           # 忽略 data/、__pycache__/、*.db*
├── app/                 # 后端（Python 标准库）
│   ├── __init__.py      # 版本号
│   ├── __main__.py      # 支持 python3 -m app
│   ├── server.py        # CLI 入口：--host/--port/--db，启动与优雅退出
│   ├── handler.py       # HTTP 层：路由、静态白名单、请求体读取、统一响应/错误封装
│   ├── api.py           # 端点业务：health/meta/items 列表/新增/详情/编辑/删除
│   ├── db.py            # SQLite：幂等建表建索引、每请求独立连接、CRUD
│   ├── validation.py    # 标题/枚举/id/分页参数校验
│   └── errors.py        # 错误码常量、ApiError、HTTP 状态映射与中文消息
├── web/                 # 前端（原生）
│   ├── index.html       # 页面结构与稳定选择器
│   ├── styles.css       # 桌面与 375px 响应式样式
│   └── app.js           # 交互逻辑；用户文本一律 textContent 渲染
└── tests/
    ├── __init__.py      # 把 case 根目录加入 sys.path
    └── test_api.py      # 42 个 unittest 自测用例
```

## 9. 数据目录与清理

- 默认数据文件：`cases/issue-board-20261128-032037-codex/data/issues.db`（由 `--db` 覆盖）。
- `data/`、`__pycache__/`、`*.db*` 已被 case 内 `.gitignore` 忽略，不会进入版本库。
- 清理：`rm -rf data/`（或删除 `--db` 指向的临时目录）；重新启动服务会自动建空库。
- 未启用 WAL，正常运行期数据目录内只有一个 `.db` 文件（无 `-wal` / `-shm`）。

## 10. 已知限制

- **无鉴权、无多用户**：任何能访问该端口的人都能读写数据，仅用于本机/内网验收。
- 单机本地运行，不含云部署、容器化、HTTPS 与反向代理配置。
- 不支持附件、拖拽排序、标签、截止日期、多语言切换等超出需求的功能。
- 每请求一个 SQLite 连接，适合本地小规模使用，不为高并发写负载做调优。
- 请求体上限 1 MiB；不支持分块（`Transfer-Encoding: chunked`）请求体。
