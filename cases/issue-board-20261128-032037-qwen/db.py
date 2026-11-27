"""SQLite 存储层（仅 Python 标准库）。

数据表 items 结构与分析报告契约一致：
    id / title / status(todo|in_progress|done) / priority(low|medium|high)
    / created_at / updated_at（UTC ISO-8601，如 2026-11-28T03:20:37Z）

线程安全策略：ThreadingHTTPServer 下每请求新建短连接并立即 close，
写操作后立即 commit，并设置 busy_timeout，保证刷新/重启后数据完整保留。
"""

import os
import sqlite3
from datetime import datetime, timezone

STATUS_VALUES = ("todo", "in_progress", "done")
PRIORITY_VALUES = ("low", "medium", "high")
ALLOWED_UPDATE_FIELDS = ("title", "status", "priority")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'in_progress', 'done')),
    priority   TEXT    NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
"""


def utc_now_iso():
    """当前 UTC 时间，固定宽度 ISO-8601（字符串序 == 时间序）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def row_to_item(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_item(db_path, title, status="todo", priority="medium"):
    now = utc_now_iso()
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO items (title, status, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title, status, priority, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return row_to_item(row)
    finally:
        conn.close()


def get_item(db_path, item_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return row_to_item(row) if row is not None else None
    finally:
        conn.close()


def update_item(db_path, item_id, fields):
    """部分更新：仅更新 fields 中给出的键（调用方已完成校验与白名单）。

    返回更新后的 Item；记录不存在返回 None；fields 为空时原样返回当前对象。
    """
    unknown = set(fields) - set(ALLOWED_UPDATE_FIELDS)
    if unknown:
        raise ValueError("unsupported fields: %s" % sorted(unknown))
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        if fields:
            keys = list(fields)
            assignments = ", ".join("%s = ?" % k for k in keys)
            values = [fields[k] for k in keys]
            values.append(utc_now_iso())
            values.append(item_id)
            conn.execute(
                "UPDATE items SET %s, updated_at = ? WHERE id = ?" % assignments,
                values,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        return row_to_item(row)
    finally:
        conn.close()


def delete_item(db_path, item_id):
    conn = connect(db_path)
    try:
        cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _like_escape(keyword):
    """转义 LIKE 通配符，保证 keyword 按字面子串匹配。"""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_items(db_path, keyword=None, status=None, page=1, page_size=10):
    """列表查询：keyword 大小写不敏感子串匹配，status 精确匹配，二者 AND。

    排序 created_at DESC, id DESC（新建在前，稳定）。page 超出总页数返回空 items。
    """
    clauses = []
    params = []
    if keyword:
        clauses.append("lower(title) LIKE ? ESCAPE '\\'")
        params.append("%" + _like_escape(keyword.lower()) + "%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM items" + where_sql, params
        ).fetchone()["c"]
        total_pages = (total + page_size - 1) // page_size
        offset = (page - 1) * page_size
        rows = conn.execute(
            "SELECT * FROM items" + where_sql
            + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return {
            "items": [row_to_item(r) for r in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }
    finally:
        conn.close()
