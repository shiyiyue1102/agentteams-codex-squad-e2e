"""参数化 SQL 数据访问层：只做读写，不做业务校验。"""

DEFAULT_ORDER = "ORDER BY created_at DESC, id DESC"


def escape_like(term):
    """把 LIKE 通配符转义成字面量，配合 ``ESCAPE '\\'`` 使用。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_filters(query=None, status=None):
    clauses = []
    params = []
    if query:
        clauses.append("title LIKE ? ESCAPE '\\'")
        params.append("%" + escape_like(query) + "%")
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def row_to_item(row):
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_item(connection, title, status, priority, timestamp):
    cursor = connection.execute(
        "INSERT INTO items (title, status, priority, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, status, priority, timestamp, timestamp),
    )
    connection.commit()
    return int(cursor.lastrowid)


def get_item(connection, item_id):
    cursor = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    return row_to_item(row) if row is not None else None


def update_item(connection, item_id, title, status, priority, timestamp):
    cursor = connection.execute(
        "UPDATE items SET title = ?, status = ?, priority = ?, updated_at = ?"
        " WHERE id = ?",
        (title, status, priority, timestamp, item_id),
    )
    connection.commit()
    return cursor.rowcount > 0


def delete_item(connection, item_id):
    cursor = connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    connection.commit()
    return cursor.rowcount > 0


def count_items(connection, query=None, status=None):
    where, params = build_filters(query, status)
    cursor = connection.execute("SELECT COUNT(*) AS total FROM items" + where, params)
    return int(cursor.fetchone()["total"])


def fetch_items(connection, query=None, status=None, limit=10, offset=0):
    where, params = build_filters(query, status)
    sql = "SELECT * FROM items" + where + " " + DEFAULT_ORDER + " LIMIT ? OFFSET ?"
    cursor = connection.execute(sql, params + [int(limit), int(offset)])
    return [row_to_item(row) for row in cursor.fetchall()]
