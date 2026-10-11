"""电商销售日报生成器 —— 小白便携版运行核心脚本。
支持无头一键自动跑批、启动网页看板、一键生成演示测试数据。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import date
from pathlib import Path

# 确保在 Windows 控制台下支持 UTF-8 打印输出，防止 gbk 编码崩溃
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 定位路径
CORE_DIR = Path(__file__).resolve().parent
PORTABLE_DIR = CORE_DIR.parent
ROOT_DIR = PORTABLE_DIR.parent
SOURCE_DIR = ROOT_DIR / "source"

# 智能加入模块搜索路径（优先独立包内的 backend，其次单仓开发时的 source/backend）
if (CORE_DIR / "backend").exists():
    if str(CORE_DIR) not in sys.path:
        sys.path.insert(0, str(CORE_DIR))
elif SOURCE_DIR.exists():
    if str(SOURCE_DIR) not in sys.path:
        sys.path.insert(0, str(SOURCE_DIR))

# 便携版默认自包含目录
DATA_DIR = PORTABLE_DIR / "core" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "portable.db"

INCOMING_DIR = PORTABLE_DIR / "输入表格放这里"
EXPORT_DIR = PORTABLE_DIR / "生成的报表在里头"

INCOMING_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# 注入环境变量，确保后端使用便携版的目录
os.environ["ECOM_DB_PATH"] = str(DB_PATH)
os.environ["EXPORT_DIR"] = str(EXPORT_DIR)
os.environ["INCOMING_DIR"] = str(INCOMING_DIR)


def ensure_db_initialized():
    """确保便携版数据库已初始化建表与默认预警规则。"""
    from backend.app.db import engine, init_db
    init_db()


def run_pipeline(target_date: str | None = None) -> int:
    """核心一键流水线：扫描表格 -> 识别入库 -> 自动聚合 -> 导出 Excel 与文字摘要。"""
    print("==================================================")
    print("      多平台电商销售日报生成器 - 正在处理中...      ")
    print("==================================================")
    ensure_db_initialized()

    from backend.app.core.sku_mapper import apply_suggestion, fuzzy_candidates
    from backend.app.db import SessionLocal
    from backend.app.models.entities import SalesFact, SkuMapping
    from backend.app.services.auto_ingest_service import scan_and_ingest_incoming
    from backend.app.services.report_service import generate_daily_report

    with SessionLocal() as db:
        # 1. 扫描输入目录
        print(f"\n[1/4] 扫描输入表格目录: {INCOMING_DIR}")
        files = [p for p in INCOMING_DIR.glob("*") if p.suffix.lower() in [".csv", ".xlsx", ".xls"]]
        if not files:
            print("  提示：【输入表格放这里】文件夹中暂无新表格文件！")
            has_data = db.query(SalesFact).first() is not None
            if not has_data:
                print("  数据库中也无历史数据，请先将导出的 CSV/XLSX 放入文件夹中。")
                print("  （或者双击【一键生成测试模拟数据.bat】快速体验效果）")
                return 1

        ingest_res = scan_and_ingest_incoming(db, INCOMING_DIR)
        print(f"  扫描文件: {ingest_res['scanned_files']} 个 | 成功入库: {len(ingest_res['committed'])} 个 | 重复跳过: {len(ingest_res['skipped_duplicates'])} 个")
        for c in ingest_res["committed"]:
            print(f"    [成功] [{c['platform']}] {c['filename']} ({c['valid_rows']} 行有效销售记录)")
        for f in ingest_res["failed"]:
            print(f"    [失败] {f['filename']} 入库失败: {f['error']}")

        # 2. 自动采纳高置信度 SKU 映射（≥90分模糊匹配）
        unmapped = db.query(SkuMapping).filter(SkuMapping.sku_id.is_(None)).all()
        adopted_count = 0
        for m in unmapped:
            cands = fuzzy_candidates(db, m.platform_product_name, None, limit=1)
            if cands and cands[0].score >= 90.0:
                apply_suggestion(db, m.id, cands[0].sku.id)
                adopted_count += 1
        if adopted_count > 0:
            print(f"\n[2/4] SKU 跨平台对齐: 自动匹配了 {adopted_count} 个高置信度 SKU")
        else:
            print("\n[2/4] SKU 跨平台对齐: 检查完成")

        # 3. 确定报告日期
        if target_date:
            report_date = target_date
        else:
            latest_fact = db.query(SalesFact.stat_date).order_by(SalesFact.stat_date.desc()).first()
            if not latest_fact:
                print("\n错误：库中没有有效的销售日期数据，无法生成日报。")
                return 1
            report_date = latest_fact[0].isoformat()

        print(f"\n[3/4] 正在计算聚合与预警指标（销售日期: {report_date}）...")
        out = generate_daily_report(db, report_date)

        # 4. 保存群发摘要文件
        summary_file = EXPORT_DIR / f"群发摘要_{report_date}.txt"
        summary_file.write_text(out.summary_text, encoding="utf-8")

        print(f"\n[4/4] 生成成功！")
        print("--------------------------------------------------")
        print(f"★ Excel 日报文件: {EXPORT_DIR}")
        print(f"★ 微信/钉钉群发摘要: {summary_file.name} ({out.char_count} 字符)")
        print("--------------------------------------------------")
        print("\n【群发文字摘要预览】：\n")
        print(out.summary_text)
        print("\n--------------------------------------------------")

        # 自动弹出记事本打开摘要文件，方便小白一键全选复制
        if sys.platform == "win32":
            try:
                os.startfile(str(summary_file))
            except Exception:
                pass

    return 0


def run_web(port: int = 8000) -> int:
    """启动本地可视化 Web 看板，并自动打开浏览器。"""
    ensure_db_initialized()
    import uvicorn
    from backend.app.main import app

    url = f"http://localhost:{port}"
    print(f"\n正在启动电商数据分析看板: {url}")
    print("按 Ctrl + C 可停止运行。\n")

    # 延迟 1.5 秒自动弹出默认浏览器
    import threading
    import time
    def _open():
        time.sleep(1.5)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def generate_mock(days: int = 7, skus: int = 20) -> int:
    """一键生成模拟测试数据到【输入表格放这里】目录。"""
    print(f"\n正在生成 {days} 天跨度、{skus} 个 SKU 的 4 大平台测试数据...")
    from backend.scripts.gen_mock_data import generate
    out_manifest = generate(days=days, skus=skus, outdir=INCOMING_DIR)
    print(f"[成功] 模拟数据已成功放入: {INCOMING_DIR}")
    print("现在您可以直接双击【一键生成今日报表.bat】进行处理！\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="电商销售日报便携运行工具")
    subparsers = parser.add_subparsers(dest="command")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="执行报表生成流水线")
    run_parser.add_argument("--date", type=str, default=None, help="指定报告日期（格式 YYYY-MM-DD，默认最新一天）")

    # web 子命令
    web_parser = subparsers.add_parser("web", help="启动网页看板")
    web_parser.add_argument("--port", type=int, default=8000, help="服务端口（默认 8000）")

    # mock 子命令
    mock_parser = subparsers.add_parser("mock", help="生成测试模拟数据")
    mock_parser.add_argument("--days", type=int, default=7, help="模拟天数")
    mock_parser.add_argument("--skus", type=int, default=20, help="模拟SKU数量")

    args = parser.parse_args()

    cmd = args.command or "run"
    if cmd == "run":
        sys.exit(run_pipeline(args.date if hasattr(args, "date") else None))
    elif cmd == "web":
        sys.exit(run_web(args.port))
    elif cmd == "mock":
        sys.exit(generate_mock(args.days, args.skus))


if __name__ == "__main__":
    main()
