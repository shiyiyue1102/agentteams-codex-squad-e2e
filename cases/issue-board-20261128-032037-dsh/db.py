"""SQLite 存储层：建表与数据访问（仅 Python 标准库）。

契约见 docs/SPEC.md 第 3 节：单表 issues，UTC ISO-8601 秒级时间戳，
列表固定 ORDER BY created_at DESC, id DESC。
"""

import sqlite3
from datetime import datetime, timezone

STATUS_VALUES = ("todo", "in_progress", "done")
PRIORITY_VALUES = ("low", "medium", "high")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,                -- trim 后 1..80 个 Unicode 码点
    status     TEXT NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'in_progress', 'done')),
    priority   TEXT NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL,                -- UTC ISO-8601 'YYYY-MM-DDTHH:MM:SSZ'
    updated_at TEXT NOT NULL                 -- 同上；每次 PUT 成功刷新
);
CREATE INDEX IF NOT EXISTS idx_issues_created ON issues (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues (status);
"""


def utc_now_iso():
    """当前 UTC 时间，ISO-8601 秒级字符串（YYYY-MM-DDTHH:MM:SSZ）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    """每请求新建连接（配合 ThreadingHTTPServer，避免跨线程共享连接）。"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    """幂等迁移：对已存在的库仅补建缺失的表/索引，不清空既有数据。"""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def row_to_issue(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_issue(conn, title, status, priority):
    now = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO issues (title, status, priority, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, now, now),
    )
    conn.commit()
    return get_issue_by_id(conn, cur.lastrowid)


def get_issue_by_id(conn, issue_id):
    row = conn.execute(
        "SELECT id, title, status, priority, created_at, updated_at"
        " FROM issues WHERE id = ?",
        (issue_id,),
    ).fetchone()
    return row_to_issue(row) if row is not None else None


def update_issue(conn, issue_id, title, status, priority):
    """全量更新；成功必刷新 updated_at（同秒内可能等于旧值）。"""
    now = utc_now_iso()
    conn.execute(
        "UPDATE issues SET title = ?, status = ?, priority = ?, updated_at = ?"
        " WHERE id = ?",
        (title, status, priority, now, issue_id),
    )
    conn.commit()
    return get_issue_by_id(conn, issue_id)


def delete_issue(conn, issue_id):
    """物理删除；返回是否确实删除了一行（False 即记录不存在）。"""
    cur = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
    conn.commit()
    return cur.rowcount > 0


def escape_like(keyword):
    r"""转义 LIKE 通配符，使 \ % _ 按字面匹配（配合 ESCAPE '\'）。"""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_issues(conn, keyword=None, status=None, page=1, page_size=10):
    """按条件过滤 + 固定排序 + 分页。

    返回 (items, total, total_pages)；total/total_pages 针对过滤后的结果集，
    total=0 时 total_pages=0；越界页返回空 items（不是错误）。
    """
    where = []
    params = []
    if keyword:
        where.append(r"title LIKE ? ESCAPE '\'")
        params.append("%" + escape_like(keyword) + "%")
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM issues" + where_sql, params
    ).fetchone()["c"]
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT id, title, status, priority, created_at, updated_at FROM issues"
        + where_sql
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()
    return [row_to_issue(r) for r in rows], total, total_pages
