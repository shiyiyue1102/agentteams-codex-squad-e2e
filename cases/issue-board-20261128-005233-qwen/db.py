"""事项管理应用的 SQLite 数据访问层。

仅使用 Python 标准库（sqlite3）。每次读写使用独立连接、用完即关；
写请求在响应返回前 commit，保证服务重启后数据不丢失。
"""

import sqlite3
from datetime import datetime, timezone

# 契约枚举（API 与存储一律使用英文枚举值，中文仅用于前端展示）
VALID_STATUS = ("todo", "doing", "done")        # 待办 / 进行中 / 完成
VALID_PRIORITY = ("high", "medium", "low")      # 高 / 中 / 低

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'doing', 'done')),
    priority   TEXT NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
"""

ITEM_FIELDS = ("id", "title", "status", "priority", "created_at", "updated_at")


def utc_now():
    """返回当前 UTC 时间戳，秒级精度，如 2026-11-28T00:52:33Z。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    """打开一个独立的 SQLite 连接（行可按列名访问）。"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    """建表与索引（IF NOT EXISTS），服务启动时自动调用，无需初始化脚本。"""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _to_item(row):
    """把 sqlite3.Row 转成契约规定的 Item 对象（恰好 6 个字段）。"""
    return {key: row[key] for key in ITEM_FIELDS}


def _like_escape(keyword):
    """转义 LIKE 关键词中的 \\ % _，使其一律按字面量匹配。"""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_items(db_path, q=None, status=None, page=1, page_size=10):
    """关键词搜索 + 状态筛选（AND 组合）+ 分页，固定按 id DESC 排序。

    返回契约规定的列表结构：
    {items, page, page_size, total, total_pages}
    """
    conditions = []
    params = []
    if q:
        conditions.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + _like_escape(q) + "%")
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM items" + where, params
        ).fetchone()["total"]
        rows = conn.execute(
            "SELECT * FROM items" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
            list(params) + [page_size, (page - 1) * page_size],
        ).fetchall()
    finally:
        conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": [_to_item(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def create_item(db_path, title, status="todo", priority="medium"):
    """新增事项，返回完整 Item 对象。created_at == updated_at。"""
    now = utc_now()
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO items (title, status, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title, status, priority, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    finally:
        conn.close()
    return _to_item(row)


def get_item(db_path, item_id):
    """按 id 查询单条；不存在返回 None。"""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    return _to_item(row) if row is not None else None


def update_item(db_path, item_id, fields):
    """部分更新：只修改 fields 中出现的（已通过校验的）字段，并刷新 updated_at。

    返回更新后的完整 Item 对象；记录不存在时返回 None。
    """
    allowed = [key for key in ("title", "status", "priority") if key in fields]
    if not allowed:
        raise ValueError("update_item 需要至少一个可更新字段")

    conn = connect(db_path)
    try:
        exists = conn.execute(
            "SELECT id FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if exists is None:
            return None
        assignments = ", ".join(key + " = ?" for key in allowed)
        params = [fields[key] for key in allowed] + [utc_now(), item_id]
        conn.execute(
            "UPDATE items SET " + assignments + ", updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    return _to_item(row)


def delete_item(db_path, item_id):
    """删除记录；实际删除了记录返回 True，记录不存在返回 False。"""
    conn = connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
