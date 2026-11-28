"""SQLite 连接管理与建表。

约定（见分析报告 §3 / §4.1）：
- 每个请求新建连接，用完立即关闭，避免跨线程复用；
- ``PRAGMA busy_timeout=5000`` 处理并发写等待；
- 建表只用 ``CREATE TABLE IF NOT EXISTS``，启动时绝不清空既有数据。
"""

import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    status     TEXT    NOT NULL CHECK (status   IN ('todo','in_progress','done')),
    priority   TEXT    NOT NULL CHECK (priority IN ('low','medium','high')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_status     ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created_id ON items(created_at DESC, id DESC);
"""


class Database:
    """持有一个 SQLite 文件路径，为每个请求提供独立连接。"""

    def __init__(self, path):
        self.raw_path = path
        self.path = os.path.abspath(path)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        """创建父目录并建表（幂等，不删除既有数据）。"""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            connection.commit()
        finally:
            connection.close()
