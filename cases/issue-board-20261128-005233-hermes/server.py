#!/usr/bin/env python3
"""Issue Board — HTTP server (Python standard library only).

Serves the REST API under /api and static frontend files.

Usage:
    python3 server.py [--port 8000] [--db data.db] [--host 0.0.0.0]

The server binds 0.0.0.0 by default so browser tests can reach it via a
network address (Worker localhost is not reachable from test browsers).

Concurrency note: ThreadingHTTPServer is used; every request opens its own
SQLite connection (per-request connect model), avoiding cross-thread
connection sharing. Writes are short-lived; SQLite's own locking resolves
occasional write contention.
"""

import argparse
import json
import os
import posixpath
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

VALID_STATUS = ("todo", "doing", "done")
VALID_PRIORITY = ("low", "medium", "high")
MAX_TITLE_LEN = 80
ID_RE = re.compile(r"^\d+$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class ApiError(Exception):
    def __init__(self, http_status, code, message):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


def err(http_status, code, message):
    return ApiError(http_status, code, message)


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def validate_title(value, required=False):
    """Return stripped title or raise ApiError.

    required=True: missing/empty -> TITLE_REQUIRED (used by POST).
    required=False: field absent -> None (used by PUT partial update).
    """
    if value is None:
        if required:
            raise err(400, "TITLE_REQUIRED", "title is required")
        return None
    if not isinstance(value, str):
        if required:
            raise err(400, "TITLE_REQUIRED", "title must be a string")
        raise err(400, "TITLE_REQUIRED", "title must be a string")
    stripped = value.strip()
    if not stripped:
        raise err(400, "TITLE_REQUIRED",
                  "title must not be empty after trimming")
    if len(stripped) > MAX_TITLE_LEN:
        raise err(400, "TITLE_TOO_LONG",
                  "title must be at most %d characters after trimming"
                  % MAX_TITLE_LEN)
    return stripped


def validate_enum(value, allowed, code, field, default=None):
    if value is None:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise err(400, code,
                  "%s must be one of: %s" % (field, ", ".join(allowed)))
    return value


def parse_path_id(raw_id):
    """Positive integer id or INVALID_ID."""
    if not ID_RE.match(raw_id):
        raise err(400, "INVALID_ID", "id must be a positive integer")
    issue_id = int(raw_id)
    if issue_id < 1:
        raise err(400, "INVALID_ID", "id must be a positive integer")
    return issue_id


def parse_int_param(params, name, default, lo, hi, code):
    if name not in params:
        return default
    raw = params[name][0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise err(400, code, "%s must be an integer" % name)
    if value < lo or value > hi:
        raise err(400, code, "%s must be between %d and %d" % (name, lo, hi))
    return value


def parse_json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        raise err(400, "INVALID_JSON", "request body must be valid JSON")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise err(400, "INVALID_JSON", "request body must be valid JSON")
    if not isinstance(body, dict):
        raise err(400, "INVALID_JSON", "request body must be a JSON object")
    return body


# --------------------------------------------------------------------------
# Request handler
# --------------------------------------------------------------------------

class BoardHandler(BaseHTTPRequestHandler):
    server_version = "IssueBoard/1.0"
    protocol_version = "HTTP/1.1"
    db_path = "data.db"  # overridden in main()

    # -- low-level response helpers ---------------------------------------

    def send_json(self, http_status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, http_status, code, message):
        self.send_json(http_status, {"error": {"code": code, "message": message}})

    def log_message(self, fmt, *args):
        # Quieter logs; keep errors only.
        pass

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def do_PATCH(self):
        self.route("PATCH")

    def do_HEAD(self):
        self.route("HEAD")

    def route(self, method):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self.handle_api(method, path, parse_qs(parsed.query))
            elif method in ("GET", "HEAD"):
                self.handle_static(path)
            else:
                self.send_error_json(405, "METHOD_NOT_ALLOWED",
                                     "method %s not allowed on %s" % (method, path))
        except ApiError as e:
            self.send_error_json(e.http_status, e.code, e.message)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            self.send_error_json(500, "INTERNAL_ERROR", "internal server error")

    # -- static ---------------------------------------------------------

    def handle_static(self, path):
        if path == "/":
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            rel = path.lstrip("/")
        # normalize and prevent traversal
        rel = posixpath.normpath(rel)
        if rel.startswith("..") or rel.startswith("/"):
            self.send_error_json(404, "NOT_FOUND", "resource not found")
            return
        file_path = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(file_path):
            self.send_error_json(404, "NOT_FOUND", "resource not found")
            return
        ctype = CONTENT_TYPES.get(os.path.splitext(file_path)[1].lower(),
                                  "application/octet-stream")
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- API ----------------------------------------------------------------

    def handle_api(self, method, path, params):
        # normalize: strip trailing slash except root of /api/issues
        if path != "/api/issues" and path.endswith("/"):
            path = path.rstrip("/")

        if path == "/api/issues":
            if method == "GET":
                return self.api_list(params)
            if method == "POST":
                return self.api_create()
            raise err(405, "METHOD_NOT_ALLOWED",
                      "method %s not allowed on /api/issues" % method)

        m = re.match(r"^/api/issues/([^/]+)$", path)
        if m:
            issue_id = parse_path_id(m.group(1))
            if method == "GET":
                return self.api_get_one(issue_id)
            if method == "PUT":
                return self.api_update(issue_id)
            if method == "DELETE":
                return self.api_delete(issue_id)
            raise err(405, "METHOD_NOT_ALLOWED",
                      "method %s not allowed on /api/issues/{id}" % method)

        raise err(404, "NOT_FOUND", "unknown API path %s" % path)

    # collection --------------------------------------------------------

    def api_list(self, params):
        keyword = params.get("keyword", [""])[0].strip()
        status = params.get("status", [None])[0]
        if status is not None and status != "":
            validate_enum(status, VALID_STATUS, "INVALID_STATUS", "status")
        elif status == "":
            status = None
        page = parse_int_param(params, "page", 1, 1, 10 ** 9, "INVALID_PAGE")
        page_size = parse_int_param(params, "page_size", 10, 1, 100,
                                    "INVALID_PAGE_SIZE")

        conn = db.connect(self.db_path)
        try:
            items, total = db.list_issues(conn, keyword or None, status,
                                          page, page_size)
        finally:
            conn.close()

        total_pages = (total + page_size - 1) // page_size if total else 0
        self.send_json(200, {"data": {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }})

    def api_create(self):
        body = parse_json_body(self)
        # validation order: title -> status -> priority
        title = validate_title(body.get("title"), required=True)
        status = validate_enum(body.get("status"), VALID_STATUS,
                               "INVALID_STATUS", "status", default="todo")
        priority = validate_enum(body.get("priority"), VALID_PRIORITY,
                                 "INVALID_PRIORITY", "priority",
                                 default="medium")

        conn = db.connect(self.db_path)
        try:
            issue = db.create_issue(conn, title, status, priority)
        finally:
            conn.close()
        self.send_json(201, {"data": issue})

    # single item -----------------------------------------------------

    def api_get_one(self, issue_id):
        conn = db.connect(self.db_path)
        try:
            issue = db.get_issue(conn, issue_id)
        finally:
            conn.close()
        if issue is None:
            raise err(404, "NOT_FOUND", "issue %d not found" % issue_id)
        self.send_json(200, {"data": issue})

    def api_update(self, issue_id):
        # validation order: id format (done) -> JSON -> fields -> existence
        body = parse_json_body(self)
        fields = {}
        if "title" in body:
            fields["title"] = validate_title(body.get("title"))
        if "status" in body:
            fields["status"] = validate_enum(body.get("status"), VALID_STATUS,
                                             "INVALID_STATUS", "status")
        if "priority" in body:
            fields["priority"] = validate_enum(body.get("priority"),
                                               VALID_PRIORITY,
                                               "INVALID_PRIORITY", "priority")
        if not fields:
            raise err(400, "EMPTY_UPDATE",
                      "at least one of title/status/priority is required")

        conn = db.connect(self.db_path)
        try:
            existing = db.get_issue(conn, issue_id)
            if existing is None:
                raise err(404, "NOT_FOUND", "issue %d not found" % issue_id)
            issue = db.update_issue(conn, issue_id, fields)
        finally:
            conn.close()
        self.send_json(200, {"data": issue})

    def api_delete(self, issue_id):
        conn = db.connect(self.db_path)
        try:
            deleted = db.delete_issue(conn, issue_id)
        finally:
            conn.close()
        if not deleted:
            raise err(404, "NOT_FOUND", "issue %d not found" % issue_id)
        self.send_json(200, {"data": {"deleted": True, "id": issue_id}})


def main():
    parser = argparse.ArgumentParser(description="Issue Board server")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--db",
                        default=os.environ.get("BOARD_DB", "data.db"),
                        help="SQLite database file path")
    args = parser.parse_args()

    db_path = args.db
    if not os.path.isabs(db_path):
        db_path = os.path.join(BASE_DIR, db_path)

    db.init(db_path)
    BoardHandler.db_path = db_path

    httpd = ThreadingHTTPServer((args.host, args.port), BoardHandler)
    print("Issue Board listening on http://%s:%d (db: %s)"
          % (args.host, args.port, db_path))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
