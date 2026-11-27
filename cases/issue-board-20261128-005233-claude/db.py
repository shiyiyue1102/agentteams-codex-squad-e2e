"""SQLite data access for the issue board (stdlib only)."""
import sqlite3
from datetime import datetime, timezone

VALID_STATUS = ("todo", "doing", "done")
VALID_PRIORITY = ("low", "medium", "high")

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'todo'
             CHECK (status IN ('todo','doing','done')),
  priority   TEXT NOT NULL DEFAULT 'medium'
             CHECK (priority IN ('low','medium','high')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def now_iso():
    """Current UTC time as ISO-8601 with millisecond precision, e.g. 2026-11-28T09:00:00.123Z"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def connect(db_path):
    """Open a new connection (one per request) and make sure the schema exists."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _row_to_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_issue(conn, title, status="todo", priority="medium"):
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO issues (title, status, priority, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, ts, ts),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM issues WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return _row_to_dict(row)


def _escape_like(keyword):
    """Escape LIKE wildcards so % and _ match literally (ESCAPE '\\')."""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_issues(conn, keyword=None, status=None, page=1, page_size=10):
    """Return (items, total) ordered by id DESC with deterministic pagination."""
    where = []
    params = []
    if keyword:
        where.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(keyword) + "%")
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM issues" + where_sql, params
    ).fetchone()["c"]
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT * FROM issues" + where_sql + " ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def get_issue(conn, issue_id):
    row = conn.execute(
        "SELECT * FROM issues WHERE id = ?", (issue_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_issue(conn, issue_id, fields):
    """fields: non-empty subset of {title, status, priority}. Caller validates."""
    assignments = ", ".join(k + " = ?" for k in fields)
    ts = now_iso()
    conn.execute(
        "UPDATE issues SET " + assignments + ", updated_at = ? WHERE id = ?",
        list(fields.values()) + [ts, issue_id],
    )
    conn.commit()
    return get_issue(conn, issue_id)


def delete_issue(conn, issue_id):
    cur = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
    conn.commit()
    return cur.rowcount > 0
