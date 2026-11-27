# 事项管理板（Issue Board）

一个可本地运行的多文件事项管理应用：事项标题、状态（待办/进行中/完成）与优先级；支持新增、编辑、删除、关键词搜索、状态筛选、分页；数据持久化在 SQLite，刷新页面与服务重启后数据保留。

- **关联 Issue**: [shiyiyue1102/agentteams-codex-squad-e2e#83](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/83)
- **技术栈**: 后端仅 Python 标准库（`http.server` + `sqlite3`）；前端 HTML/CSS/原生 JS。**无任何外部运行依赖**，无需 pip install / npm install。

## 目录结构

```
cases/issue-board-20261128-005233-hermes/
├── server.py          # HTTP 服务 + API 路由（标准库）
├── db.py              # SQLite 建表与数据访问
├── static/
│   ├── index.html     # 单页前端
│   ├── style.css      # 响应式样式（桌面 + 375px 无横向溢出）
│   └── app.js         # 原生 JS（用户文本一律 textContent 渲染，防 XSS）
├── tests/
│   └── test_api.py    # 开发自测（标准库 unittest，真实起服务测试）
└── README.md
```

## 启动

需要 Python 3.8+（标准库即可）。

```bash
cd cases/issue-board-20261128-005233-hermes
python3 server.py
```

- 默认监听 `0.0.0.0:8000`，浏览器访问 `http://<主机IP>:8000/`（或本机 `http://127.0.0.1:8000/`）。
- **默认绑定 `0.0.0.0`**，以便浏览器自动化测试从网络可达地址访问（Worker 容器内的 localhost 对测试浏览器不可达）。
- 可用参数或环境变量覆盖：

```bash
python3 server.py --port 9000 --db board.db --host 0.0.0.0
# 等价环境变量：PORT / BOARD_DB / HOST
```

## 临时数据库测试方式

数据库文件路径完全可配置，用临时文件即可得到干净环境、测完删除即还原：

```bash
python3 server.py --db /tmp/board-test.db --port 8000
# 测试结束后：
rm /tmp/board-test.db
```

默认数据库文件为项目目录下的 `data.db`（首次启动自动建表；已有数据在重启后保留，不会被清空）。

## 开发自测

自测脚本会自行选取空闲端口、创建临时数据库、真实启动 `server.py` 子进程并用 `urllib` 打 HTTP 请求，结束后自动清理，不污染默认数据：

```bash
python3 -m unittest discover tests/ -v
# 或
python3 tests/test_api.py
```

覆盖范围（对应需求分析验收标准 A/B/C 核心路径）：

- **A1–A10**：创建（默认值/strip/显式枚举）、列表分页结构、关键词搜索（大小写不敏感）、状态筛选与组合、分页切片无重叠、单查、部分更新（updated_at 刷新）、删除后 404、空结果 200。
- **B1–B10**：TITLE_REQUIRED / TITLE_TOO_LONG（80/81 边界）/ INVALID_STATUS / INVALID_PRIORITY / INVALID_ID / NOT_FOUND / INVALID_JSON / INVALID_PAGE(_SIZE) / EMPTY_UPDATE / 统一错误结构 / 校验顺序（字段 400 先于存在性 404）/ 405 与未知 API 路径。
- **C1**：停止服务后以同一 db 文件重启，数据仍在。
- 静态资源：`/`、`/static/style.css`、`/static/app.js` 返回 200。

## API 摘要

| 接口 | 说明 |
|---|---|
| `GET /api/issues?keyword=&status=&page=&page_size=` | 列表（搜索/筛选/分页，默认 page=1、page_size=10） |
| `POST /api/issues` | 创建：`{"title": "...", "status": "todo", "priority": "medium"}`（status/priority 可缺省） |
| `GET /api/issues/{id}` | 单条查询 |
| `PUT /api/issues/{id}` | 部分更新（title/status/priority 任意子集） |
| `DELETE /api/issues/{id}` | 删除（硬删除，id 不复用） |

- 成功响应统一包裹在 `data` 字段；错误响应统一为 `{"error": {"code", "message"}}`。
- 枚举：status ∈ `todo`/`doing`/`done`，priority ∈ `low`/`medium`/`high`（大小写敏感）。
- title 去除两端空白后必填且 ≤80 个 Unicode 字符。
- 校验顺序：id 格式 → JSON 合法性 → 字段校验（400）→ 存在性（404）。

## 实现说明（并发模型）

服务端使用 `ThreadingHTTPServer`，**每个请求新建一个 SQLite 连接**（per-request connect），避免跨线程共享连接；写操作短小，偶发写竞争由 SQLite 自身锁机制解决。建表使用 `CREATE TABLE IF NOT EXISTS`，服务重启自动初始化且保留既有数据。

## 前端安全与展示

- 所有用户文本（标题等）一律通过 `textContent` 渲染，**不使用 `innerHTML` 拼接用户数据**，XSS 载荷（如 `<img src=x onerror=alert(1)>`）按字面文字显示、不会执行。
- 页面提供：空态提示、加载中状态、操作成功/失败 toast（失败展示服务端 error.code 与 message）、表单同规则校验（服务端校验为最终防线）。
- 状态/优先级以中文标签展示（待办/进行中/完成；低/中/高），API 传输英文枚举值。
- 响应式：桌面（≥1280px）全功能；375px 宽度下控件纵向堆叠、长标题自动换行，无横向溢出。
