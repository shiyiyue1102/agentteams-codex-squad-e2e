"""命令行入口：解析 --host/--port/--db，初始化数据库并启动服务（契约第 9 节）。"""

import argparse
import sys

from .db import init_db
from .handler import IssueBoardServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DB = "data/issues.db"


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="issue-board",
        description="本地事项管理应用：Python 标准库 HTTP 服务 + SQLite，零外部运行依赖。",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="监听地址，默认 %(default)s；浏览器验证需使用 0.0.0.0",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 %(default)s")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="SQLite 文件路径，相对路径以当前工作目录解析，父目录不存在时自动创建，默认 %(default)s",
    )
    return parser


def build_server(host, port, db_path):
    """初始化数据库并创建（尚未启动的）服务实例，供 CLI 与自测复用。"""
    return IssueBoardServer((host, port), init_db(db_path))


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    server = build_server(args.host, args.port, args.db)
    host, port = server.server_address[0], server.server_address[1]
    sys.stderr.write("issue-board listening on http://%s:%d db=%s\n" % (host, port, server.db_path))
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("issue-board stopped\n")
        sys.stderr.flush()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
