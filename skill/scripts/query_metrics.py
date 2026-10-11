"""Agent 专用：查询电商多维聚合指标与异常预警数据。
支持按大盘概览、异常列表、分平台分布、分类目/SKU排行进行结构化查询。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# 编码适配
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ROOT_DIR = SKILL_DIR.parent
SOURCE_DIR = ROOT_DIR / "source"

if (SKILL_DIR / "backend").exists():
    if str(SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SKILL_DIR))
elif SOURCE_DIR.exists():
    if str(SOURCE_DIR) not in sys.path:
        sys.path.insert(0, str(SOURCE_DIR))


def main():
    parser = argparse.ArgumentParser(description="查询电商聚合指标与异常预警")
    parser.add_argument("--date", type=str, default=None,
                        help="查询日期（YYYY-MM-DD），缺省时使用最新一天")
    parser.add_argument("--section", type=str, default="all",
                        choices=["all", "overview", "anomalies", "platforms", "skus"],
                        help="查询区块：all(全量), overview(大盘概览), anomalies(异常预警), platforms(分平台), skus(SKU排行)")
    parser.add_argument("--db-path", type=str, default=str(ROOT_DIR / "portable" / "core" / "data" / "portable.db"),
                        help="SQLite 数据库文件路径")

    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        print(json.dumps({"ok": False, "error": f"数据库不存在: {db_path}"}, ensure_ascii=False))
        sys.exit(1)

    os.environ["ECOM_DB_PATH"] = str(db_path)

    from backend.app.db import SessionLocal
    from backend.app.models.entities import SalesFact
    from backend.app.core.aggregator import build_daily_report

    with SessionLocal() as db:
        if args.date:
            target_date_str = args.date
        else:
            latest = db.query(SalesFact.stat_date).order_by(SalesFact.stat_date.desc()).first()
            if not latest:
                print(json.dumps({"ok": False, "error": "库中无销售数据"}, ensure_ascii=False))
                sys.exit(1)
            target_date_str = latest[0].isoformat()

        d = date.fromisoformat(target_date_str)
        report_data = build_daily_report(db, d, compare_enabled=True)

        res = {"ok": True, "date": target_date_str}

        if args.section in ["all", "overview"]:
            res["overview"] = report_data.get("overview", {})
            res["attribution"] = report_data.get("attribution_hint", "")
        if args.section in ["all", "anomalies"]:
            res["anomalies"] = report_data.get("anomalies", [])
        if args.section in ["all", "platforms"]:
            res["by_platform"] = report_data.get("by_platform", [])
        if args.section in ["all", "skus"]:
            res["by_sku_top10"] = report_data.get("by_sku", [])[:10]

        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
