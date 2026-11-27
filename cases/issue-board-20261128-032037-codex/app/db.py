"""SQLite 持久化：幂等建表建索引、每请求独立连接、CRUD（契约第 5 节）。"""

import os
import sqlite3
from datetime import datetime, timezone

ITEM_COLUMNS = ("id", "title", "status", "priority", "created_at", "updated_at")
_SELECT_COLUMNS = ", ".join(ITEM_COLUMNS)
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status   IN ('todo','doing','done')),
    priority   TEXT    NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low','medium','high')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_status        ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created_at_id ON items(created_at DESC, id DESC);
"""


def connect(db_path):
    """打开一个新的 SQLite 连接（D9：不跨线程共享）。"""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path):
    """幂等初始化数据库文件，返回其绝对路径。"""
    absolute = os.path.abspath(db_path)
    parent = os.path.dirname(absolute)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = connect(absolute)
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    return absolute


def utc_now():
    """UTC 秒级时间戳，字典序等于时间序（D8）。"""
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def row_to_item(row):
    return {name: row[name] for name in ITEM_COLUMNS}


def escape_like(keyword):
    """转义 LIKE 通配符，使 % 与 _ 按字面量匹配（契约 4.3）。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_filter(keyword, status):
    clauses = []
    params = []
    if keyword:
        clauses.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + escape_like(keyword) + "%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def count_items(connection, keyword="", status=None):
    """满足筛选条件的总行数（分页前）。"""
    where, params = _build_filter(keyword, status)
    row = connection.execute("SELECT COUNT(*) AS total FROM items" + where, params).fetchone()
    return int(row["total"])


def list_items(connection, keyword="", status=None, limit=20, offset=0):
    """按 created_at DESC, id DESC 固定排序取一页。"""
    where, params = _build_filter(keyword, status)
    sql = (
        "SELECT " + _SELECT_COLUMNS + " FROM items" + where
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    )
    rows = connection.execute(sql, list(params) + [int(limit), int(offset)]).fetchall()
    return [row_to_item(row) for row in rows]


def insert_item(connection, title, status, priority):
    """写入后立即 commit（契约 5.3），返回入库后的完整 Item。"""
    now = utc_now()
    cursor = connection.execute(
        "INSERT INTO items (title, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, now, now),
    )
    connection.commit()
    return fetch_item(connection, int(cursor.lastrowid))


def fetch_item(connection, item_id):
    row = connection.execute(
        "SELECT " + _SELECT_COLUMNS + " FROM items WHERE id = ?", (int(item_id),)
    ).fetchone()
    return None if row is None else row_to_item(row)


def update_item(connection, item_id, title, status, priority):
    """整体更新可变字段并刷新 updated_at，created_at 与 id 不变。"""
    connection.execute(
        "UPDATE items SET title = ?, status = ?, priority = ?, updated_at = ? WHERE id = ?",
        (title, status, priority, utc_now(), int(item_id)),
    )
    connection.commit()
    return fetch_item(connection, int(item_id))


def delete_item(connection, item_id):
    """硬删除（D7 非幂等），返回受影响行数。"""
    cursor = connection.execute("DELETE FROM items WHERE id = ?", (int(item_id),))
    connection.commit()
    return cursor.rowcount
