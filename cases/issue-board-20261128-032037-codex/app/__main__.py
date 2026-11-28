"""支持 python3 -m app 启动（等价于 python3 -m app.server）。"""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
