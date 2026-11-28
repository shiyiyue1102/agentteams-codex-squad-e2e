"""SQLite 数据访问层（仅标准库）。

契约来源：Issue #106 分析报告第 3 节。
- 单库单表 items，启动时幂等建表；
- 每个操作使用短生命周期连接（ThreadingHTTPServer 多线程安全）；
- 时间戳为 ISO-8601 UTC 秒级（...Z）；
- 搜索为标题不区分大小写子串匹配，LIKE 元字符按字面转义；
- 排序固定 created_at DESC, id DESC。
"""

import os
import sqlite3
from datetime import datetime, timezone

STATUS_VALUES = ("todo", "in_progress", "done")
PRIORITY_VALUES = ("high", "medium", "low")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL CHECK (length(title) >= 1 AND length(title) <= 80),
  status     TEXT NOT NULL DEFAULT 'todo'
             CHECK (status IN ('todo','in_progress','done')),
  priority   TEXT NOT NULL DEFAULT 'medium'
             CHECK (priority IN ('high','medium','low')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status     ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC, id DESC);
"""


def utc_now_iso():
    """返回 UTC 秒级 ISO-8601 时间戳，如 2026-11-28T02:00:00Z。"""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _escape_like(text):
    """转义 LIKE 元字符 \\ % _，配合 ESCAPE '\\' 按字面匹配。"""
    return (
        text.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _row_to_item(row):
    return {
        "id": row[0],
        "title": row[1],
        "status": row[2],
        "priority": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


_ITEM_COLUMNS = "id, title, status, priority, created_at, updated_at"
_ORDER_BY = "ORDER BY created_at DESC, id DESC"


class ItemStore:
    """每次操作打开独立连接；调用方负责传入可写磁盘路径（禁止 :memory:）。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self._connect().close()  # 立即建表并验证可写

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.executescript(_SCHEMA)
        return conn

    def create_item(self, title, status="todo", priority="medium"):
        now = utc_now_iso()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO items (title, status, priority, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (title, status, priority, now, now),
            )
            conn.commit()
            item_id = cur.lastrowid
        finally:
            conn.close()
        return self.get_item(item_id)

    def get_item(self, item_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT {} FROM items WHERE id = ?".format(_ITEM_COLUMNS),
                (item_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_item(row) if row else None

    def update_item(self, item_id, fields):
        """部分更新；fields 只含 title/status/priority 中已通过校验的键。

        返回更新后的 Item；记录不存在返回 None。
        """
        allowed = [k for k in ("title", "status", "priority") if k in fields]
        if not allowed:
            return self.get_item(item_id)
        assignments = ", ".join("{} = ?".format(k) for k in allowed)
        params = [fields[k] for k in allowed]
        params.append(utc_now_iso())
        params.append(item_id)
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE items SET {}, updated_at = ? WHERE id = ?".format(assignments),
                params,
            )
            conn.commit()
            touched = cur.rowcount
        finally:
            conn.close()
        if not touched:
            return None
        return self.get_item(item_id)

    def delete_item(self, item_id):
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            conn.close()
        return deleted

    def list_items(self, q=None, status=None, page=1, page_size=10):
        """返回 (items, total)。q 为不区分大小写子串（元字符字面匹配）；
        status 精确筛选；二者 AND；排序 created_at DESC, id DESC。
        """
        where = []
        params = []
        if q:
            where.append("title LIKE ? ESCAPE '\\'")
            params.append("%" + _escape_like(q) + "%")
        if status:
            where.append("status = ?")
            params.append(status)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM items {}".format(where_sql), params
            ).fetchone()[0]
            offset = (page - 1) * page_size
            rows = conn.execute(
                "SELECT {} FROM items {} {} LIMIT ? OFFSET ?".format(
                    _ITEM_COLUMNS, where_sql, _ORDER_BY
                ),
                params + [page_size, offset],
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_item(r) for r in rows], total


def resolve_db_path(db_arg):
    """相对路径按本脚本所在目录解析；绝对路径原样使用。"""
    if os.path.isabs(db_arg):
        return db_arg
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, db_arg)
