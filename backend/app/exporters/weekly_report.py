"""Excel 周报导出：多天聚合周度复盘报表。

包含 4 个核心 Sheet：
1. 周报总览：本周核心指标合计、上周环比、周度经营健康度
2. 7天趋势明细：周一至周日每日 GMV、实际成交、退款率、毛利、ROI
3. 分平台周汇总：各大平台本周 GMV 规模、占比、毛利率及环比
4. 本周爆款榜：本周销售前 10 核心 SKU 与退款率
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')

HEADER_BG = "1F4E79"
BAND_BG = "F2F7FB"
POS_COLOR = "006100"
NEG_COLOR = "9C0006"

FMT_MONEY = "#,##0.00"
FMT_INT = "#,##0"
FMT_PCT = "0.0%"
FMT_PCT_SIGNED = "+0.0%;-0.0%;0.0%"
FMT_PP_SIGNED = '+0.0"pp";-0.0"pp";0.0"pp"'

_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _header_cell(cell, text: str) -> None:
    cell.value = text
    cell.fill = PatternFill("solid", fgColor=HEADER_BG)
    cell.font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    cell.alignment = Alignment(horizontal="center", vertical="center")


def _data_cell(cell, value: Any, fmt: str | None = None, banded: bool = False, align: str = "right") -> None:
    cell.value = value
    cell.font = Font(name="微软雅黑", size=10)
    cell.border = _BORDER
    cell.alignment = Alignment(horizontal=align, vertical="center")
    if fmt:
        cell.number_format = fmt
    if banded:
        cell.fill = PatternFill("solid", fgColor=BAND_BG)


def _delta_cell(cell, value: float | None, is_pp: bool = False, banded: bool = False) -> None:
    cell.border = _BORDER
    cell.alignment = Alignment(horizontal="right", vertical="center")
    if banded:
        cell.fill = PatternFill("solid", fgColor=BAND_BG)
    if value is None:
        cell.value = "—"
        cell.font = Font(name="微软雅黑", size=10, color="999999")
        return
    color = POS_COLOR if value > 0 else (NEG_COLOR if value < 0 else "000000")
    cell.value = value
    cell.font = Font(name="微软雅黑", size=10, bold=abs(value) >= 0.2, color=color)
    cell.number_format = FMT_PP_SIGNED if is_pp else FMT_PCT_SIGNED


def _autofit(ws) -> None:
    for col in ws.columns:
        max_len = 0
        for cell in col:
            v = str(cell.value or "")
            max_len = max(max_len, len(v.encode("gbk", "ignore")))
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)


def generate_weekly_excel_report(data: dict, out_dir: Path, company: str = "XX") -> str:
    """生成周度销售复盘 Excel，返回绝对路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    # 公司名可能含 / \ 等非法文件名字符（来自环境变量），必须清洗否则 wb.save 500
    safe_company = _UNSAFE_FILENAME_RE.sub("_", company).strip() or "XX"
    filename = f"{safe_company}_电商销售周报_{data['week_range_str']}.xlsx"
    dest = out_dir / filename

    wb = Workbook()
    # 默认 Sheet 命名为周报总览
    ws_ov = wb.active
    ws_ov.title = "周报总览"
    ws_ov.views.sheetView[0].showGridLines = True

    # 1. Sheet 1: 周报总览
    ws_ov.merge_cells("A1:E1")
    title_cell = ws_ov["A1"]
    title_cell.value = f"{company} 电商全渠道销售周报（{data['week_range_str']}）"
    title_cell.font = Font(name="微软雅黑", size=14, bold=True, color="1F4E79")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws_ov.row_dimensions[1].height = 36

    headers_ov = ["核心经营指标", "本周合计", "上周同期", "周环比增长", "指标说明"]
    ws_ov.row_dimensions[3].height = 24
    for c_idx, h in enumerate(headers_ov, start=1):
        _header_cell(ws_ov.cell(row=3, column=c_idx), h)

    metric_rows = [
        ("GMV（拍下总流水）", data["total_gmv"], data["prev_gmv"], data["wow_gmv"], FMT_MONEY, False, "全渠道未剔除退款拍下总金额"),
        ("实际成交额（扣除退款）", data["total_net_amount"], data["prev_net_amount"], data["wow_net_amount"], FMT_MONEY, False, "剔除退款后实际沉淀成交金额"),
        ("退款总金额", data["total_refund_amount"], data["prev_refund_amount"], data["wow_refund_amount"], FMT_MONEY, False, "本周全平台申请并成立的退款总额"),
        ("平均退款率", data["total_refund_rate"], data["prev_refund_rate"], data["wow_refund_rate_pp"], FMT_PCT, True, "退款金额 / GMV，环比为百分点差值"),
        ("到手综合毛利", data["total_gross_profit"], data["prev_gross_profit"], data["wow_gross_profit"], FMT_MONEY, False, "实际成交额 − 货品成本 − 平台扣点 − 运费险"),
        ("综合毛利率", data["total_gross_margin"], data["prev_gross_margin"], data["wow_gross_margin_pp"], FMT_PCT, True, "毛利 / 实际成交额，环比为百分点差值"),
        ("累计支付件数", data["total_paid_qty"], data["prev_paid_qty"], data["wow_paid_qty"], FMT_INT, False, "本周全渠道订单售出总件数"),
        ("周平均客单价", data["avg_order_value"], data["prev_avg_order_value"], data["wow_avg_order_value"], FMT_MONEY, False, "实际成交额 / 支付件数"),
        ("全店推广 ROI", data["total_roi"], data["prev_roi"], data["wow_roi"], "0.00", False, "实际成交额 / 推广总花费"),
    ]

    for r_idx, (name, val, p_val, wow, fmt, is_pp, tip) in enumerate(metric_rows, start=4):
        ws_ov.row_dimensions[r_idx].height = 20
        band = (r_idx % 2 == 1)
        _data_cell(ws_ov.cell(row=r_idx, column=1), name, banded=band, align="left")
        _data_cell(ws_ov.cell(row=r_idx, column=2), val, fmt=fmt, banded=band)
        _data_cell(ws_ov.cell(row=r_idx, column=3), p_val, fmt=fmt, banded=band)
        _delta_cell(ws_ov.cell(row=r_idx, column=4), wow, is_pp=is_pp, banded=band)
        _data_cell(ws_ov.cell(row=r_idx, column=5), tip, banded=band, align="left")

    _autofit(ws_ov)

    # 2. Sheet 2: 7 天趋势明细
    ws_daily = wb.create_sheet(title="7天趋势明细")
    ws_daily.views.sheetView[0].showGridLines = True
    daily_headers = ["日期", "星期", "GMV (元)", "实际成交 (元)", "退款率", "毛利润 (元)", "毛利率", "支付件数", "客单价 (元)", "推广 ROI"]
    ws_daily.row_dimensions[1].height = 24
    for c_idx, h in enumerate(daily_headers, start=1):
        _header_cell(ws_daily.cell(row=1, column=c_idx), h)

    for r_idx, d_item in enumerate(data.get("daily_breakdown", []), start=2):
        ws_daily.row_dimensions[r_idx].height = 20
        band = (r_idx % 2 == 1)
        _data_cell(ws_daily.cell(row=r_idx, column=1), d_item["date"], banded=band, align="center")
        _data_cell(ws_daily.cell(row=r_idx, column=2), d_item["weekday"], banded=band, align="center")
        _data_cell(ws_daily.cell(row=r_idx, column=3), d_item["gmv"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=4), d_item["net_amount"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=5), d_item["refund_rate"], fmt=FMT_PCT, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=6), d_item["gross_profit"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=7), d_item["gross_margin"], fmt=FMT_PCT, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=8), d_item["paid_qty"], fmt=FMT_INT, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=9), d_item["avg_order_value"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_daily.cell(row=r_idx, column=10), d_item["roi"], fmt="0.00", banded=band)

    _autofit(ws_daily)

    # 3. Sheet 3: 分平台周汇总
    ws_plat = wb.create_sheet(title="分平台周汇总")
    ws_plat.views.sheetView[0].showGridLines = True
    plat_headers = ["电商平台", "本周 GMV (元)", "GMV 占比", "实际成交 (元)", "退款率", "综合毛利 (元)", "GMV 周环比"]
    ws_plat.row_dimensions[1].height = 24
    for c_idx, h in enumerate(plat_headers, start=1):
        _header_cell(ws_plat.cell(row=1, column=c_idx), h)

    for r_idx, p_item in enumerate(data.get("platforms_summary", []), start=2):
        ws_plat.row_dimensions[r_idx].height = 20
        band = (r_idx % 2 == 1)
        _data_cell(ws_plat.cell(row=r_idx, column=1), p_item["platform_name"], banded=band, align="left")
        _data_cell(ws_plat.cell(row=r_idx, column=2), p_item["gmv"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_plat.cell(row=r_idx, column=3), p_item["share"], fmt=FMT_PCT, banded=band)
        _data_cell(ws_plat.cell(row=r_idx, column=4), p_item["net_amount"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_plat.cell(row=r_idx, column=5), p_item["refund_rate"], fmt=FMT_PCT, banded=band)
        _data_cell(ws_plat.cell(row=r_idx, column=6), p_item["gross_profit"], fmt=FMT_MONEY, banded=band)
        _delta_cell(ws_plat.cell(row=r_idx, column=7), p_item["wow_gmv"], banded=band)

    _autofit(ws_plat)

    # 4. Sheet 4: 本周爆款榜 Top 10
    ws_sku = wb.create_sheet(title="本周爆款榜 Top 10")
    ws_sku.views.sheetView[0].showGridLines = True
    sku_headers = ["排名", "商品名称 / SKU", "归属平台", "本周 GMV (元)", "销量件数", "退款率", "GMV 贡献占比"]
    ws_sku.row_dimensions[1].height = 24
    for c_idx, h in enumerate(sku_headers, start=1):
        _header_cell(ws_sku.cell(row=1, column=c_idx), h)

    for r_idx, s_item in enumerate(data.get("top_skus", []), start=2):
        ws_sku.row_dimensions[r_idx].height = 20
        band = (r_idx % 2 == 1)
        _data_cell(ws_sku.cell(row=r_idx, column=1), f"第 {r_idx - 1} 名", banded=band, align="center")
        _data_cell(ws_sku.cell(row=r_idx, column=2), s_item["name"], banded=band, align="left")
        _data_cell(ws_sku.cell(row=r_idx, column=3), s_item["platform"], banded=band, align="center")
        _data_cell(ws_sku.cell(row=r_idx, column=4), s_item["gmv"], fmt=FMT_MONEY, banded=band)
        _data_cell(ws_sku.cell(row=r_idx, column=5), s_item["paid_qty"], fmt=FMT_INT, banded=band)
        _data_cell(ws_sku.cell(row=r_idx, column=6), s_item["refund_rate"], fmt=FMT_PCT, banded=band)
        _data_cell(ws_sku.cell(row=r_idx, column=7), s_item["share"], fmt=FMT_PCT, banded=band)

    _autofit(ws_sku)

    wb.save(dest)
    return str(dest)
