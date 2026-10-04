"""本地 Drop Folder 监听脚本（数据自动接入）。

用法：
- 单次扫描（适合脚本或批处理、断档批量回填）：
    python -m backend.scripts.watch_incoming --once
- 循环守护监听（默认每 5 秒扫描一次，适合本地挂起）：
    python -m backend.scripts.watch_incoming --interval 5
"""

from __future__ import annotations

import argparse
import time

from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.services.auto_ingest_service import scan_and_ingest_incoming


def main() -> None:
    parser = argparse.ArgumentParser(description="监听 data/incoming/ 并自动解析落库")
    parser.add_argument(
        "--once", action="store_true", help="仅执行单次扫描，处理完成后退出"
    )
    parser.add_argument(
        "--interval", type=int, default=5, help="守护模式下的轮询间隔秒数（默认 5 秒）"
    )
    args = parser.parse_args()

    incoming_path = settings.incoming_dir
    print(f"[DropWatcher] 启动监听目录: {incoming_path}")

    if args.once:
        with SessionLocal() as db:
            res = scan_and_ingest_incoming(db, incoming_path)
            print(f"[DropWatcher] 扫描完成: 发现 {res['scanned_files']} 个文件, "
                  f"已入库 {len(res['committed'])}, "
                  f"跳过重复 {len(res['skipped_duplicates'])}, "
                  f"失败 {len(res['failed'])}")
            for c in res["committed"]:
                print(f"  ✓ {c['filename']} -> [{c['platform']}] 有效行: {c['valid_rows']}")
            for f in res["failed"]:
                print(f"  ✗ {f['filename']} 失败: {f['error']}")
        return

    print(f"[DropWatcher] 守护模式运行中 (轮询间隔 {args.interval}s, 按 Ctrl+C 停止)...")
    try:
        while True:
            with SessionLocal() as db:
                res = scan_and_ingest_incoming(db, incoming_path)
                if res["scanned_files"] > 0:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] 处理 {res['scanned_files']} 个文件: "
                        f"入库 {len(res['committed'])}, "
                        f"跳过重复 {len(res['skipped_duplicates'])}, "
                        f"失败 {len(res['failed'])}"
                    )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[DropWatcher] 退出监听。")


if __name__ == "__main__":
    main()
