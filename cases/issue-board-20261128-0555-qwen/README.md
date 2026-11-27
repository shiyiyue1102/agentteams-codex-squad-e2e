# 事项管理应用（Issue Board）

可本地运行的多文件事项管理应用：后端为 **Python 标准库 HTTP 服务（`http.server`）+ SQLite（`sqlite3`）**，前端为 **HTML / CSS / 原生 JavaScript**。**零外部运行依赖**，只需 Python 3.8+（仅标准库，无需任何 pip 安装）。

对应 Issue：[#99 研发小队复杂验收 20261128-0555 qwen：事项管理应用](https://github.com/shiyiyue1102/agentteams-codex-squad-e2e/issues/99)

## 功能

- 事项字段：标题、状态（待办 `todo` / 进行中 `doing` / 完成 `done`）、优先级（低 `low` / 中 `medium` / 高 `high`）
- 新增、编辑（部分更新合并语义）、删除（带确认步骤）
- 关键词搜索（标题大小写不敏感子串匹配，LIKE 通配符按字面处理）与状态筛选，可 AND 组合
- 分页（每页 10 条，上一页 / 下一页 + 当前页 / 总页数 / 总条数）
- 数据存于 SQLite 文件并逐次提交落盘，**页面刷新与服务进程重启后数据完整保留**
- 标题去两端空白后必填且 ≤ 80 字符；非法状态 / 优先级、错误输入、不存在记录均返回明确的统一错误体
- 页面展示空态、加载中、每次操作的成功 / 失败原因；用户文本一律按字面文字显示（`textContent` 渲染，不执行 HTML）
- 桌面与 375px 宽度均可完成全部操作，无横向溢出；不引用任何外部 CDN 资源

## 启动说明

```bash
cd cases/issue-board-20261128-0555-qwen
python3 server.py --port 8000
```

浏览器访问 <http://127.0.0.1:8000/> 。停止服务：`Ctrl+C`。

可选参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址；需要网络可达的预览地址时用 `0.0.0.0` |
| `--port` | `8000` | 监听端口；`0` 表示随机可用端口（实际端口打印到 stdout） |
| `--db` | `<本目录>/data/items.db` | SQLite 数据库文件路径，目录不存在时自动创建 |

## 临时数据库测试方式

`--db` 指向任意路径即可使用一次性数据库；删除该文件即清空全部数据，例如：

```bash
python3 server.py --port 8080 --db /tmp/items-test.db
# ... 测试 ...
rm -f /tmp/items-test.db   # 清空数据
```

## 开发自测

```bash
python3 selftest.py        # 汇总输出；退出码 0 = 全部通过
python3 selftest.py -v     # 逐条输出每个用例
```

自测脚本自动使用**临时目录中的一次性数据库**与随机可用端口启动服务，覆盖 CRUD 正常路径、标题 80/81 字符边界、非法枚举 / 非法 JSON / PUT 空对象、404/405 与统一错误体格式、搜索（含 LIKE 通配符转义）/ 筛选 / 分页与排序稳定性、服务重启后持久化、静态资源与前端 XSS 纪律（无 HTML 注入 API、无外部资源）等 84 项断言，结束后自动清理，不在仓库内留下任何数据库或缓存文件。

等效 curl 手工验证示例（服务已按上文启动）：

```bash
curl -s http://127.0.0.1:8000/api/items
curl -s -X POST http://127.0.0.1:8000/api/items \
  -H 'Content-Type: application/json' \
  -d '{"title": "示例事项", "priority": "high"}'
curl -s 'http://127.0.0.1:8000/api/items?page=1&page_size=10&q=示例&status=todo'
curl -s http://127.0.0.1:8000/api/items/1
curl -s -X PUT http://127.0.0.1:8000/api/items/1 \
  -H 'Content-Type: application/json' -d '{"status": "done"}'
curl -s -i -X DELETE http://127.0.0.1:8000/api/items/1
```

## API 摘要

| 方法 | 路径 | 成功 | 说明 |
|---|---|---|---|
| GET | `/api/items` | 200 | 列表；查询参数 `page`(≥1，默认1)、`page_size`(1–100，默认10)、`q`(标题子串，大小写不敏感)、`status`(todo/doing/done)；排序固定 `created_at DESC, id DESC`；返回 `{items, page, page_size, total, total_pages}` |
| POST | `/api/items` | 201 | 新增；`title` 必填（去空白后非空且 ≤80 字符），`status`/`priority` 可选（缺省 `todo`/`medium`，`null` 视为缺省） |
| GET | `/api/items/{id}` | 200 | 单条查询；`{id}` 非正整数按 404 处理 |
| PUT | `/api/items/{id}` | 200 | 部分更新：`title`/`status`/`priority` 至少一个，未提供字段保持原值，`updated_at` 刷新 |
| DELETE | `/api/items/{id}` | 204 | 删除，无响应体 |

所有 `/api/*` 错误统一返回 `{"error": {"code", "message", "field"?}}`：

| code | HTTP | 触发条件 |
|---|---|---|
| `VALIDATION_ERROR` | 400 | 标题缺失 / 去空白后为空 / 超过 80 字符；非法状态或优先级；请求体非法 JSON 或非对象；PUT 空对象或无有效字段；`page`/`page_size`/`status` 查询参数非法 |
| `NOT_FOUND` | 404 | 记录不存在；未知 `/api/*` 路径；`{id}` 非正整数 |
| `METHOD_NOT_ALLOWED` | 405 | 已定义路径使用不支持的方法（如 `DELETE /api/items`） |
| `INTERNAL_ERROR` | 500 | 未预期服务端异常 |

## 目录结构

```
cases/issue-board-20261128-0555-qwen/
├── README.md          # 本文档：启动说明、临时数据库测试方式、开发自测
├── server.py          # 入口：标准库 HTTP 服务 + 静态资源 + /api/* 分发
├── api.py             # /api/* 路由、参数与请求体校验、统一错误体
├── db.py              # SQLite 建表、连接与数据访问（写操作逐次提交落盘）
├── selftest.py        # 开发自测脚本（临时数据库 + 随机端口，自动清理）
├── .gitignore         # 忽略运行时数据库与 Python 缓存
└── static/
    ├── index.html     # 单页入口（viewport meta、语义化结构）
    ├── app.js         # 原生 JS：列表/搜索/筛选/分页/增改删；用户文本仅 textContent 渲染
    └── style.css      # 响应式样式（桌面 / 375px 无横向溢出）
```

## 实现说明（与契约的对应关系）

- 标题长度按 Unicode 字符计（Python `len(str)`），80 字符成功、81 字符返回 400。
- `q` 参数去除两端空白后匹配；`%`、`_` 等 LIKE 通配符经转义按字面匹配（`ESCAPE '\'`）。
- PUT 为部分更新（合并语义）；请求中值为 `null` 的字段视为缺省（保持原值）；无任何有效字段时返回 400。字段校验先于存在性检查，两者同时非法时返回 400。
- `GET /api/items` 的 `status` 查询参数一经提供必须为合法枚举（空串也返回 400）；不筛选时请省略该参数。
- 分页超出末页不是错误：返回空 `items` 与正确的 `total`/`total_pages`；`total=0` 时 `total_pages=0`。
- 服务为多线程（`ThreadingHTTPServer`），每请求独立 SQLite 连接，写事务逐次提交，重启不丢数据。
