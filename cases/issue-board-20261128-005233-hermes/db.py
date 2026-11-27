"""SQLite initialization and data access for the Issue Board app.

Standard library only (sqlite3). One table `issues`, created with
IF NOT EXISTS so restarts keep existing data.
"""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo','doing','done')),
    priority   TEXT    NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low','medium','high')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_updated ON issues(updated_at DESC, id DESC);
"""


def now_iso():
    """UTC ISO 8601 timestamp with microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connect(db_path):
    """Open a new connection (per-request threading model)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path):
    """Create schema if missing. Existing data is preserved."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_issues(conn, keyword=None, status=None, page=1, page_size=10):
    """Return (items, total) filtered by keyword/status, paginated.

    Ordering: updated_at DESC, id DESC (stable).
    keyword: substring match on title, case-insensitive for ASCII
    (SQLite LIKE default); % _ \\ are escaped.
    """
    where = []
    args = []
    if keyword:
        esc = (
            keyword.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        where.append("title LIKE ? ESCAPE '\\'")
        args.append("%" + esc + "%")
    if status:
        where.append("status = ?")
        args.append(status)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM issues" + where_sql, args
    ).fetchone()["c"]

    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT * FROM issues" + where_sql
        + " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        args + [page_size, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def create_issue(conn, title, status="todo", priority="medium"):
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO issues (title, status, priority, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, ts, ts),
    )
    conn.commit()
    return get_issue(conn, cur.lastrowid)


def get_issue(conn, issue_id):
    row = conn.execute(
        "SELECT * FROM issues WHERE id = ?", (issue_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_issue(conn, issue_id, fields):
    """fields: dict with any subset of title/status/priority."""
    sets = ", ".join(k + " = ?" for k in fields)
    ts = now_iso()
    conn.execute(
        "UPDATE issues SET " + sets + ", updated_at = ? WHERE id = ?",
        list(fields.values()) + [ts, issue_id],
    )
    conn.commit()
    return get_issue(conn, issue_id)


def delete_issue(conn, issue_id):
    cur = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
    conn.commit()
    return cur.rowcount > 0
