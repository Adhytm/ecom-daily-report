"""Agent 专用：执行电商销售日报全流程流水线。
扫描表格 -> 平台识别 -> 归一化落库 -> 聚合计算 -> 输出 Excel 与微信摘要。
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

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


def main():
    parser = argparse.ArgumentParser(description="运行电商销售日报生成流水线")
    parser.add_argument("--input-dir", type=str, default=str(ROOT_DIR / "portable" / "输入表格放这里"),
                        help="包含各平台原始导出文件（CSV/XLSX）的目录")
    parser.add_argument("--output-dir", type=str, default=str(ROOT_DIR / "portable" / "生成的报表在里头"),
                        help="报表与摘要输出目录")
    parser.add_argument("--date", type=str, default=None,
                        help="指定报告日期（YYYY-MM-DD），缺省时自动使用最新销售日期")
    parser.add_argument("--db-path", type=str, default=str(ROOT_DIR / "portable" / "core" / "data" / "portable.db"),
                        help="SQLite 数据库文件路径")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 格式输出结果供 Agent 上下文直接解析")

    args = parser.parse_args()

    input_path = Path(args.input_dir).resolve()
    output_path = Path(args.output_dir).resolve()
    db_path = Path(args.db_path).resolve()

    input_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["ECOM_DB_PATH"] = str(db_path)
    os.environ["EXPORT_DIR"] = str(output_path)
    os.environ["INCOMING_DIR"] = str(input_path)

    from backend.app.db import SessionLocal, init_db
    from backend.app.models.entities import SalesFact, SkuMapping
    from backend.app.core.sku_mapper import apply_suggestion, fuzzy_candidates
    from backend.app.services.auto_ingest_service import scan_and_ingest_incoming
    from backend.app.services.report_service import generate_daily_report

    init_db()

    with SessionLocal() as db:
        # 1. 扫描入库
        ingest_res = scan_and_ingest_incoming(db, input_path)

        # 2. 自动采纳高置信度映射
        unmapped = db.query(SkuMapping).filter(SkuMapping.sku_id.is_(None)).all()
        adopted_count = 0
        for m in unmapped:
            cands = fuzzy_candidates(db, m.platform_product_name, None, limit=1)
            if cands and cands[0].score >= 90.0:
                apply_suggestion(db, m.id, cands[0].sku.id)
                adopted_count += 1

        # 3. 确定日期
        if args.date:
            report_date = args.date
        else:
            latest = db.query(SalesFact.stat_date).order_by(SalesFact.stat_date.desc()).first()
            if not latest:
                err_msg = "数据库中尚无有效销售记录，请检查输入目录是否放置了导出表格。"
                if args.json:
                    print(json.dumps({"ok": False, "error": err_msg}, ensure_ascii=False))
                else:
                    print(f"[错误] {err_msg}")
                sys.exit(1)
            report_date = latest[0].isoformat()

        # 4. 生成日报
        out = generate_daily_report(db, report_date)

        summary_file = output_path / f"群发摘要_{report_date}.txt"
        summary_file.write_text(out.summary_text, encoding="utf-8")

        # 查找生成的 Excel 路径
        excel_files = list(output_path.glob(f"*{report_date}*.xlsx"))
        excel_file_path = str(excel_files[-1]) if excel_files else ""

        result = {
            "ok": True,
            "report_date": report_date,
            "excel_path": excel_file_path,
            "summary_file": str(summary_file),
            "summary_text": out.summary_text,
            "char_count": out.char_count,
            "ingest_stats": {
                "scanned_files": ingest_res["scanned_files"],
                "committed_files": len(ingest_res["committed"]),
                "skipped_duplicates": len(ingest_res["skipped_duplicates"]),
                "failed_files": len(ingest_res["failed"]),
            },
            "adopted_sku_mappings": adopted_count,
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[成功] 报告日期: {report_date}")
            print(f"[文件] Excel 报表: {excel_file_path}")
            print(f"[文件] 摘要文本: {summary_file}")
            print("\n【群发文字摘要】:\n")
            print(out.summary_text)


if __name__ == "__main__":
    main()
