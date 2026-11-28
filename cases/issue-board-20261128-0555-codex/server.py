#!/usr/bin/env python3
"""事项管理应用启动入口。

用法::

    python3 server.py --host 0.0.0.0 --port 8000 --db ./data/items.sqlite3

配置优先级：命令行参数 > 环境变量 > 默认值。
仅使用 Python 标准库，无任何第三方运行依赖。
"""

import argparse
import os
import socket
import sys
from http.server import ThreadingHTTPServer

from issueboard import api
from issueboard.db import Database

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_DB = os.path.join("data", "items.sqlite3")


class IssueBoardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default, None
    try:
        return int(raw.strip()), None
    except ValueError:
        return default, "环境变量 {0} 必须是整数，当前值：{1!r}".format(name, raw)


def build_parser(default_port):
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="事项管理应用：Python 标准库 HTTP 服务 + SQLite，零外部依赖。",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ISSUE_BOARD_HOST") or DEFAULT_HOST,
        help="监听地址（环境变量 ISSUE_BOARD_HOST，默认 %(default)s；浏览器远程访问需 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help="监听端口（环境变量 ISSUE_BOARD_PORT，默认 %(default)s）",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("ISSUE_BOARD_DB") or DEFAULT_DB,
        help="SQLite 数据库文件路径（环境变量 ISSUE_BOARD_DB，默认 %(default)s，父目录自动创建）",
    )
    return parser


def create_server(host, port, database):
    handler = api.make_handler(database)
    return IssueBoardServer((host, port), handler)


def main(argv=None):
    env_port, port_error = _env_int("ISSUE_BOARD_PORT", DEFAULT_PORT)
    parser = build_parser(env_port)
    args = parser.parse_args(argv)
    if port_error:
        parser.error(port_error)
    if args.db.strip() in (":memory:", ""):
        parser.error("--db 不支持 :memory:（每请求新建连接会丢数据），请使用临时目录中的文件库")

    database = Database(args.db)
    database.initialize()

    try:
        httpd = create_server(args.host, args.port, database)
    except OSError as error:
        print(
            "启动失败：无法监听 {0}:{1} — {2}".format(args.host, args.port, error),
            file=sys.stderr,
        )
        return 2

    bound_host, bound_port = httpd.server_address[0], httpd.server_address[1]
    local_host = "127.0.0.1" if bound_host in ("0.0.0.0", "::", "") else bound_host
    print("[issue-board] listening on http://{0}:{1}/".format(bound_host, bound_port))
    print("[issue-board] local url  http://{0}:{1}/".format(local_host, bound_port))
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        print(
            "[issue-board] lan url    http://{0}:{1}/".format(lan_ip, bound_port),
        )
    except OSError:
        pass
    print("[issue-board] database: {0}".format(database.path))
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[issue-board] 已停止服务")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
