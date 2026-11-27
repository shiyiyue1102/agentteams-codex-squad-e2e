# 事项管理应用（Issue Board）

可本地运行的多文件事项管理应用：Python 标准库 HTTP 服务 + SQLite 后端，HTML/CSS/原生 JS 前端。**零外部运行依赖**（无需 pip install、无 CDN），Python ≥ 3.8 单命令启动。

对应 Issue: shiyiyue1102/agentteams-codex-squad-e2e #95

## 启动

```bash
cd cases/issue-board-20261128-032037-hermes
python3 server.py --port 8000 --db items.db
```

浏览器打开 `http://127.0.0.1:8000/` 即可使用。

参数：

- `--port` 监听端口，默认 `8000`
- `--db` SQLite 数据库文件路径，默认 `items.db`（相对当前目录；数据写入文件并 COMMIT，服务重启后保留）
- `--host` 监听地址，默认 `0.0.0.0`

## 临时数据库测试方式

独立测试建议使用一次性临时数据库，避免污染正式数据：

```bash
python3 server.py --port 8123 --db "$(mktemp -d)/board-test.db"
```

## 开发自测

在本目录下运行（会自动在临时数据库上启动真实服务进程，覆盖 API 契约、错误码、标题边界、重启持久化与并发写入）：

```bash
python3 -m unittest discover -s tests -v
```

## 功能

- 事项：标题（strip 后必填、≤80 字符）、状态（待办/进行中/完成）、优先级（低/中/高）
- 新增 / 编辑（部分更新）/ 删除（确认框）
- 关键词搜索（标题子串，不区分大小写）+ 状态筛选（可组合）
- 分页（每页 10 条，`id DESC` 新建在前）
- 页面展示空态、加载中、操作成功与失败原因（后端 `error.message`）
- XSS 防护：所有用户文本经 `textContent` 渲染，按文字原样显示，不执行 HTML
- 响应式：桌面与 375px 宽均可完成全部操作，无横向溢出

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/items` | 列表；`keyword`/`status`/`page`/`page_size` 查询参数 |
| POST | `/api/items` | 新建，201 返回完整 Item |
| GET | `/api/items/{id}` | 详情 |
| PUT | `/api/items/{id}` | 部分更新 title/status/priority |
| DELETE | `/api/items/{id}` | 删除，204 无响应体 |

错误体统一为 `{"error": {"code": "...", "message": "..."}}`，错误码含 `JSON_PARSE_ERROR`、`INVALID_INPUT`、`TITLE_REQUIRED`、`TITLE_TOO_LONG`、`INVALID_STATUS`、`INVALID_PRIORITY`、`ITEM_NOT_FOUND`、`NOT_FOUND`、`METHOD_NOT_ALLOWED`、`INTERNAL_ERROR`。

## 目录结构

```
cases/issue-board-20261128-032037-hermes/
├── server.py          # 后端入口：HTTP API + 静态文件服务
├── db.py              # SQLite 建表与行转换
├── static/
│   ├── index.html     # 前端单页
│   ├── style.css
│   └── app.js
├── tests/
│   └── test_api.py    # 开发自测（unittest，端到端）
└── README.md
```

`items.db` 为运行时生成，不入库（见 `.gitignore`）。
