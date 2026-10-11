"""初始化数据库：建表 + 默认预警规则。

用法：``python -m backend.scripts.init_db``
"""

from __future__ import annotations

from backend.app.db import engine, init_db


def main() -> None:
    init_db()
    print(f"数据库初始化完成: {engine.url}")


if __name__ == "__main__":
    main()
