"""SQLite 访问层：建表与行转换。仅标准库 sqlite3。"""

import sqlite3

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


def init_db(db_path):
    """初始化数据库文件（幂等）。数据写入文件并 COMMIT，重启后保留。"""
    conn = sqlite3.connect(db_path)
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
