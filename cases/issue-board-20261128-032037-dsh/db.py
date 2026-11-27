"""SQLite storage layer for the issue-board demo app.

Standard library only. Every public function opens a fresh connection and
closes it before returning, so the module is safe to use from a threading
HTTP server (one connection per request/operation).
"""

import sqlite3
from datetime import datetime, timezone

VALID_STATUS = ("todo", "in_progress", "done")
VALID_PRIORITY = ("low", "medium", "high")

# Schema per section 3 of the shared analysis contract (docs/SPEC.md).
SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'in_progress', 'done')),
    priority   TEXT NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_created ON issues (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues (status);
"""

_SELECT_COLUMNS = "id, title, status, priority, created_at, updated_at"


def utcnow_iso():
    """UTC ISO-8601 second-precision timestamp, e.g. 2026-11-28T03:20:37Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    """Idempotent migration: create table/indexes if missing; never drops data."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row_to_issue(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_issue(db_path, title, status, priority):
    """Insert a new issue and return the stored object."""
    now = utcnow_iso()
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO issues (title, status, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title, status, priority, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT " + _SELECT_COLUMNS + " FROM issues WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return _row_to_issue(row)
    finally:
        conn.close()


def get_issue(db_path, issue_id):
    """Return the issue dict for issue_id, or None when absent."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT " + _SELECT_COLUMNS + " FROM issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
        return _row_to_issue(row) if row is not None else None
    finally:
        conn.close()


def update_issue(db_path, issue_id, title, status, priority):
    """Full update; refreshes updated_at. Returns None when the id is absent."""
    now = utcnow_iso()
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE issues SET title = ?, status = ?, priority = ?, updated_at = ?"
            " WHERE id = ?",
            (title, status, priority, now, issue_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT " + _SELECT_COLUMNS + " FROM issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
        return _row_to_issue(row) if row is not None else None
    finally:
        conn.close()


def delete_issue(db_path, issue_id):
    """Physical delete. Returns True when a row was removed, else False."""
    conn = connect(db_path)
    try:
        cur = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def escape_like(keyword):
    """Escape \\, % and _ so LIKE matches them literally (ESCAPE '\\')."""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_issues(db_path, keyword=None, status=None, page=1, page_size=10):
    """Return (items, total) ordered by created_at DESC, id DESC.

    keyword: substring match on title, %/_ escaped literally, ASCII
    case-insensitive (SQLite LIKE default). status: exact match. Both filters
    combine with AND. page is 1-based.
    """
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if keyword:
        clauses.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + escape_like(keyword) + "%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    offset = (page - 1) * page_size
    conn = connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM issues" + where, params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT " + _SELECT_COLUMNS + " FROM issues" + where
            + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return [_row_to_issue(row) for row in rows], total
    finally:
        conn.close()
