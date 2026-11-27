"""SQLite 存储层：建表、连接管理与数据访问（仅 Python 标准库）。

数据结构契约（分析报告 §1.2）：
    items(id INTEGER PK AUTOINCREMENT,
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'todo'   CHECK in todo/doing/done,
          priority TEXT NOT NULL DEFAULT 'medium' CHECK in low/medium/high,
          created_at TEXT NOT NULL,  -- UTC ISO-8601 'YYYY-MM-DDTHH:MM:SSZ'
          updated_at TEXT NOT NULL)

持久化：每次写操作立即 COMMIT，数据落盘到 SQLite 文件，
页面刷新与服务进程重启后完整保留。
"""

import os
import sqlite3
from datetime import datetime, timezone

VALID_STATUS = ("todo", "doing", "done")
VALID_PRIORITY = ("low", "medium", "high")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'doing', 'done')),
    priority   TEXT    NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC, id DESC);
"""


def utcnow_iso():
    """当前 UTC 时间，格式 'YYYY-MM-DDTHH:MM:SSZ'。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    """打开（并按需初始化）SQLite 数据库；父目录不存在时自动创建。"""
    db_path = os.path.abspath(db_path)
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _row_to_item(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_item(conn, title, status="todo", priority="medium"):
    """插入一条事项并返回完整 Item dict。"""
    now = utcnow_iso()
    cursor = conn.execute(
        "INSERT INTO items (title, status, priority, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, now, now),
    )
    conn.commit()
    return get_item(conn, cursor.lastrowid)


def get_item(conn, item_id):
    """按 id 查询单条；不存在返回 None。"""
    row = conn.execute(
        "SELECT id, title, status, priority, created_at, updated_at"
        " FROM items WHERE id = ?",
        (item_id,),
    ).fetchone()
    return _row_to_item(row) if row is not None else None


def update_item(conn, item_id, fields):
    """部分更新 title/status/priority 子集，刷新 updated_at，返回更新后的 Item。"""
    assignments = []
    params = []
    for key in ("title", "status", "priority"):
        if key in fields:
            assignments.append("%s = ?" % key)
            params.append(fields[key])
    assignments.append("updated_at = ?")
    params.append(utcnow_iso())
    params.append(item_id)
    conn.execute(
        "UPDATE items SET %s WHERE id = ?" % ", ".join(assignments), params
    )
    conn.commit()
    return get_item(conn, item_id)


def delete_item(conn, item_id):
    """删除一条事项；返回是否确实删除了记录。"""
    cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_items(conn, page=1, page_size=10, q=None, status=None):
    """分页列表：q 为大小写不敏感子串匹配（LIKE 通配符转义为字面量），
    status 为精确筛选，二者 AND 组合；固定排序 created_at DESC, id DESC。
    返回 {"items", "page", "page_size", "total", "total_pages"}。
    """
    conditions = []
    params = []
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + escaped + "%")
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(
        "SELECT COUNT(*) AS total FROM items " + where, params
    ).fetchone()["total"]
    total_pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT id, title, status, priority, created_at, updated_at FROM items "
        + where
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()
    return {
        "items": [_row_to_item(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }
