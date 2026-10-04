"""周报聚合计算与服务编排。

- 自动对齐自然周（周一至周日 7 天）。
- 聚合多维大盘、每日走势、分平台份额、Top 爆款。
- 与上周同期完整 7 天进行周环比（WoW）对比。
- 一键生成周报 Excel 与群发文字复盘。

口径红线（与日报 aggregator 对齐）：
- 金额全程 Decimal，仅出口转 float；
- 毛利 = 成交额 − 货成本（SKU 成本价 × 件数）− 平台扣点（适配器费率）
  − 履约成本（适配器单件成本）。SKU 未映射 / 未维护成本价时该行毛利
  记为「未知」；整周无一行有成本时毛利为 None —— 宁缺毋虚报，
  绝不按比例捏造成本（旧版 net×0.4 兜底已废弃）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core import AppError
from backend.app.core.adapter_registry import get_adapter, load_builtin
from backend.app.exporters.weekly_report import generate_weekly_excel_report
from backend.app.models.entities import SalesFact, Sku

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_ZERO = Decimal(0)

# 周报只读这些列：避免把 raw_row_json 大 JSON 字段整表读进内存
_FACT_COLS = (
    "stat_date", "platform_key", "shop_name", "platform_product_code",
    "product_name", "sku_id", "paid_qty", "gmv", "net_amount",
    "refund_amount", "visitors", "buyers", "ad_cost",
)


def _get_week_bounds(d: date) -> tuple[date, date, date, date]:
    """返回 (本周一, 本周日, 上周一, 上周日)。"""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    prev_monday = monday - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return monday, sunday, prev_monday, prev_sunday


def _platform_display(key: str | None) -> str:
    """平台 key → 展示名（与日报口径一致，读内置适配器 display_name）。"""
    if not key:
        return "未知平台"
    try:
        return load_builtin(str(key)).display_name
    except Exception:  # noqa: BLE001
        return str(key)


def _load_facts(db: Session, start: date, end: date) -> list[dict[str, Any]]:
    """按列查询（排除 raw_row_json），返回 dict 行。"""
    rows = (
        db.query(*(getattr(SalesFact, c) for c in _FACT_COLS))
        .filter(SalesFact.stat_date >= start, SalesFact.stat_date <= end)
        .all()
    )
    return [r._asdict() for r in rows]


def _attach_costs(db: Session, facts: list[dict[str, Any]]) -> None:
    """为每行追加 _gp（行毛利；成本未知 → None）。

    - 货成本：SKU 成本价 × paid_qty；未映射 / 未维护成本价 / 无件数 → 未知
    - 平台扣点：net × 适配器 platform_fee_rate（用户覆盖版生效）
    - 履约成本：paid_qty × 适配器 fulfillment_cost_per_unit
    """
    sku_costs = {
        s.id: s.cost_price
        for s in db.query(Sku.id, Sku.cost_price).all()
        if s.cost_price is not None
    }
    fee_map: dict[str, tuple[Decimal, Decimal]] = {}
    for f in facts:
        p = f["platform_key"]
        if p is not None and p not in fee_map:
            try:
                sem = get_adapter(db, p).spec.metric_semantics
                fee_map[p] = (
                    Decimal(sem.platform_fee_rate),
                    Decimal(sem.fulfillment_cost_per_unit),
                )
            except Exception:  # noqa: BLE001
                fee_map[p] = (_ZERO, _ZERO)

    for f in facts:
        net = Decimal(str(f["net_amount"])) if f["net_amount"] is not None else None
        qty = Decimal(str(f["paid_qty"])) if f["paid_qty"] is not None else None
        cost_price = sku_costs.get(f["sku_id"])
        cogs = cost_price * qty if cost_price is not None and qty is not None else None
        rate, per_unit = fee_map.get(f["platform_key"], (_ZERO, _ZERO))
        fee = net * rate if net is not None else None
        ful = qty * per_unit if qty is not None else None
        # 与日报一致：成本未知则毛利未知，绝不按比例兜底、不负利润截断
        if net is None or cogs is None:
            f["_gp"] = None
        else:
            f["_gp"] = net - cogs - (fee or _ZERO) - (ful or _ZERO)


def _dsum0(facts: list[dict], key: str) -> Decimal:
    """累加指标求和（None 按 0；周报 GMV/件数等累加量口径）。"""
    return sum(
        (Decimal(str(f[key])) for f in facts if f[key] is not None), _ZERO
    )


def _gp_sum(facts: list[dict]) -> Decimal | None:
    """毛利求和：只累加成本已知的行；无一行已知 → None（宁缺毋虚报）。"""
    vals = [f["_gp"] for f in facts if f["_gp"] is not None]
    if not vals:
        return None
    return sum(vals, _ZERO)


def _safe_div(n: Decimal, d: Decimal) -> Decimal | None:
    return (n / d) if d and d > 0 else None


def _wow(cur: Decimal, prev: Decimal) -> float | None:
    if prev and prev > 0:
        return float((cur - prev) / prev)
    return None


def _f2(v: Decimal | None) -> float | None:
    return None if v is None else round(float(v), 2)


def _f4(v: Decimal | None) -> float | None:
    return None if v is None else round(float(v), 4)


def get_weekly_data(db: Session, date_str: str) -> dict[str, Any]:
    """计算周报聚合核心数据。"""
    try:
        target_date = date.fromisoformat(date_str)
    except Exception as e:
        raise AppError("INVALID_DATE", f"日期格式错误: {date_str}", status_code=400) from e

    monday, sunday, prev_monday, prev_sunday = _get_week_bounds(target_date)

    # 1. 查询本周与上周明细（按列，排除大 JSON 字段）
    cur_facts = _load_facts(db, monday, sunday)

    if not cur_facts:
        raise AppError(
            "NO_DATA_FOR_WEEK",
            f"{monday} 至 {sunday} 所在周暂无销售数据，请先上传数据。",
            status_code=404,
        )

    prev_facts = _load_facts(db, prev_monday, prev_sunday)

    # 2. 成本富集（行毛利）
    _attach_costs(db, cur_facts)
    _attach_costs(db, prev_facts)

    # 3. 本周各项汇总
    cur_gmv = _dsum0(cur_facts, "gmv")
    cur_net = _dsum0(cur_facts, "net_amount")
    cur_refund = _dsum0(cur_facts, "refund_amount")
    cur_paid = _dsum0(cur_facts, "paid_qty")
    cur_ad = _dsum0(cur_facts, "ad_cost")
    cur_gross_profit = _gp_sum(cur_facts)

    cur_refund_rate = _safe_div(cur_refund, cur_gmv)
    cur_gross_margin = (
        _safe_div(cur_gross_profit, cur_net) if cur_gross_profit is not None else None
    )
    cur_aov = _safe_div(cur_net, cur_paid)
    cur_roi = _safe_div(cur_net, cur_ad)

    # 上周各项汇总
    prev_gmv = _dsum0(prev_facts, "gmv")
    prev_net = _dsum0(prev_facts, "net_amount")
    prev_refund = _dsum0(prev_facts, "refund_amount")
    prev_paid = _dsum0(prev_facts, "paid_qty")
    prev_ad = _dsum0(prev_facts, "ad_cost")
    prev_gross_profit = _gp_sum(prev_facts)

    prev_refund_rate = _safe_div(prev_refund, prev_gmv)
    prev_gross_margin = (
        _safe_div(prev_gross_profit, prev_net) if prev_gross_profit is not None else None
    )
    prev_aov = _safe_div(prev_net, prev_paid)
    prev_roi = _safe_div(prev_net, prev_ad)

    # 周环比
    wow_gmv = _wow(cur_gmv, prev_gmv)
    wow_net = _wow(cur_net, prev_net)
    wow_refund = _wow(cur_refund, prev_refund)
    wow_paid = _wow(cur_paid, prev_paid)
    wow_gp = (
        _wow(cur_gross_profit, prev_gross_profit)
        if cur_gross_profit is not None and prev_gross_profit is not None
        else None
    )
    wow_aov = _wow(cur_aov, prev_aov) if cur_aov is not None and prev_aov is not None else None
    wow_roi = _wow(cur_roi, prev_roi) if cur_roi is not None and prev_roi is not None else None
    wow_ref_rate_pp = (
        float((cur_refund_rate - prev_refund_rate) * 100)
        if prev_facts and cur_refund_rate is not None and prev_refund_rate is not None
        else None
    )
    wow_gm_pp = (
        float((cur_gross_margin - prev_gross_margin) * 100)
        if prev_facts and cur_gross_margin is not None and prev_gross_margin is not None
        else None
    )

    # 4. 7 天每日趋势
    daily_breakdown = []
    for offset in range(7):
        cur_day = monday + timedelta(days=offset)
        day_facts = [f for f in cur_facts if f["stat_date"] == cur_day]
        d_gmv = _dsum0(day_facts, "gmv")
        d_net = _dsum0(day_facts, "net_amount")
        d_ref = _dsum0(day_facts, "refund_amount")
        d_paid = _dsum0(day_facts, "paid_qty")
        d_gp = _gp_sum(day_facts)
        d_ad = _dsum0(day_facts, "ad_cost")

        daily_breakdown.append({
            "date": cur_day.isoformat(),
            "weekday": WEEKDAYS[cur_day.weekday()],
            "gmv": _f2(d_gmv) or 0.0,
            "net_amount": _f2(d_net) or 0.0,
            "refund_rate": _f4(_safe_div(d_ref, d_gmv)),
            "gross_profit": _f2(d_gp),
            "gross_margin": (
                _f4(_safe_div(d_gp, d_net)) if d_gp is not None else None
            ),
            "paid_qty": int(d_paid),
            "avg_order_value": _f2(_safe_div(d_net, d_paid)),
            "roi": _f2(_safe_div(d_net, d_ad)),
        })

    # 5. 分平台周汇总
    platform_keys = sorted({f["platform_key"] for f in cur_facts if f["platform_key"]})
    platforms_summary = []
    for p_key in platform_keys:
        p_cur = [f for f in cur_facts if f["platform_key"] == p_key]
        p_prev = [f for f in prev_facts if f["platform_key"] == p_key]

        p_gmv = _dsum0(p_cur, "gmv")
        p_net = _dsum0(p_cur, "net_amount")
        p_ref = _dsum0(p_cur, "refund_amount")
        p_gp = _gp_sum(p_cur)
        p_prev_gmv = _dsum0(p_prev, "gmv")

        platforms_summary.append({
            "platform_key": p_key,
            "platform_name": _platform_display(p_key),
            "gmv": _f2(p_gmv) or 0.0,
            "share": _f4(_safe_div(p_gmv, cur_gmv)),
            "net_amount": _f2(p_net) or 0.0,
            "refund_rate": _f4(_safe_div(p_ref, p_gmv)),
            "gross_profit": _f2(p_gp),
            "wow_gmv": _wow(p_gmv, p_prev_gmv),
        })

    platforms_summary.sort(key=lambda x: x["gmv"], reverse=True)

    # 6. 本周爆款榜 Top 10
    sku_code_map = {s.id: s.sku_code for s in db.query(Sku.id, Sku.sku_code).all()}
    sku_groups: dict[str, dict[str, Any]] = {}
    for f in cur_facts:
        sc = sku_code_map.get(f["sku_id"])
        key = sc or f["platform_product_code"] or f["product_name"] or "未知"
        name = f["product_name"] or sc or key
        if key not in sku_groups:
            sku_groups[key] = {
                "name": name,
                "platform": _platform_display(f["platform_key"]) if f["platform_key"] else "全网",
                "gmv": _ZERO,
                "refund_amount": _ZERO,
                "paid_qty": 0,
            }
        g = sku_groups[key]
        g["gmv"] += Decimal(str(f["gmv"])) if f["gmv"] is not None else _ZERO
        g["refund_amount"] += (
            Decimal(str(f["refund_amount"])) if f["refund_amount"] is not None else _ZERO
        )
        g["paid_qty"] += int(f["paid_qty"] or 0)

    top_skus = []
    for v in sku_groups.values():
        top_skus.append({
            **v,
            "gmv": _f2(v["gmv"]) or 0.0,
            "refund_rate": _f4(_safe_div(v["refund_amount"], v["gmv"])),
            "share": _f4(_safe_div(v["gmv"], cur_gmv)),
        })
    top_skus.sort(key=lambda x: x["gmv"], reverse=True)
    top_skus = top_skus[:10]

    week_num = monday.isocalendar()[1]
    week_range_str = f"{monday.isoformat()} 至 {sunday.isoformat()} (第 {week_num} 周)"

    # 7. 生成微信/钉钉周报群发文案
    summary_text = _build_weekly_summary_text(
        week_range_str=week_range_str,
        cur_gmv=cur_gmv,
        wow_gmv=wow_gmv,
        cur_net=cur_net,
        cur_gross_profit=cur_gross_profit,
        cur_gross_margin=cur_gross_margin,
        cur_refund_rate=cur_refund_rate,
        wow_ref_rate_pp=wow_ref_rate_pp,
        platforms_summary=platforms_summary,
        top_skus=top_skus,
        company=settings.company_name,
    )

    return {
        "start_date": monday.isoformat(),
        "end_date": sunday.isoformat(),
        "week_number": week_num,
        "week_range_str": week_range_str,
        "total_gmv": _f2(cur_gmv) or 0.0,
        "prev_gmv": _f2(prev_gmv) if prev_facts else None,
        "wow_gmv": wow_gmv,
        "total_net_amount": _f2(cur_net) or 0.0,
        "prev_net_amount": _f2(prev_net) if prev_facts else None,
        "wow_net_amount": wow_net,
        "total_refund_amount": _f2(cur_refund) or 0.0,
        "prev_refund_amount": _f2(prev_refund) if prev_facts else None,
        "wow_refund_amount": wow_refund,
        "total_refund_rate": _f4(cur_refund_rate),
        "prev_refund_rate": _f4(prev_refund_rate) if prev_facts else None,
        "wow_refund_rate_pp": round(wow_ref_rate_pp, 2) if wow_ref_rate_pp is not None else None,
        "total_gross_profit": _f2(cur_gross_profit),
        "prev_gross_profit": _f2(prev_gross_profit) if prev_facts else None,
        "wow_gross_profit": wow_gp,
        "total_gross_margin": _f4(cur_gross_margin),
        "prev_gross_margin": _f4(prev_gross_margin) if prev_facts else None,
        "wow_gross_margin_pp": round(wow_gm_pp, 2) if wow_gm_pp is not None else None,
        "total_paid_qty": int(cur_paid),
        "prev_paid_qty": int(prev_paid) if prev_facts else None,
        "wow_paid_qty": wow_paid,
        "avg_order_value": _f2(cur_aov),
        "prev_avg_order_value": _f2(prev_aov) if prev_facts else None,
        "wow_avg_order_value": wow_aov,
        "total_roi": _f2(cur_roi),
        "prev_roi": _f2(prev_roi) if prev_facts else None,
        "wow_roi": wow_roi,
        "daily_breakdown": daily_breakdown,
        "platforms_summary": platforms_summary,
        "top_skus": top_skus,
        "summary_text": summary_text,
    }


def _build_weekly_summary_text(
    week_range_str: str,
    cur_gmv: Decimal,
    wow_gmv: float | None,
    cur_net: Decimal,
    cur_gross_profit: Decimal | None,
    cur_gross_margin: Decimal | None,
    cur_refund_rate: Decimal | None,
    wow_ref_rate_pp: float | None,
    platforms_summary: list[dict],
    top_skus: list[dict],
    company: str,
) -> str:
    """生成 800 字符以内的微信/钉钉周报文案。"""
    wow_gmv_str = f"{wow_gmv * 100:+.1f}%" if wow_gmv is not None else "基数不足"
    ref_pp_str = f"{wow_ref_rate_pp:+.1f}pp" if wow_ref_rate_pp is not None else "—"

    if cur_gross_profit is not None and cur_gross_margin is not None:
        gp_str = (
            f"综合毛利：¥{float(cur_gross_profit):,.0f}"
            f"（毛利率：{float(cur_gross_margin) * 100:.1f}%）"
        )
    else:
        gp_str = "综合毛利：—（SKU 成本价未维护，请到「SKU 档案」补全后重算）"

    lines = [
        f"📊【{company} 电商销售周度复盘】",
        f"周期：{week_range_str}",
        "------------------------------",
        "一、周度核心业绩",
        f"• 本周 GMV：¥{float(cur_gmv):,.0f}（周环比：{wow_gmv_str}）",
        f"• 实际成交：¥{float(cur_net):,.0f} | {gp_str}",
        f"• 退款率：{(float(cur_refund_rate) * 100):.1f}%（周变动：{ref_pp_str}）"
        if cur_refund_rate is not None
        else "• 退款率：—",
        "",
        "二、渠道业绩排名",
    ]

    for p in platforms_summary[:4]:
        gp_part = (
            f"，毛利 ¥{p['gross_profit']:,.0f}" if p["gross_profit"] is not None else ""
        )
        lines.append(
            f"• {p['platform_name']}：GMV ¥{p['gmv']:,.0f}"
            f"（占 {(p['share'] or 0) * 100:.0f}%{gp_part}）"
        )

    if top_skus:
        lines.append("")
        lines.append("三、本周核心爆款 Top 3")
        for idx, s in enumerate(top_skus[:3], start=1):
            lines.append(f"{idx}. {s['name'][:18]}（¥{s['gmv']:,.0f}，销量 {s['paid_qty']} 件）")

    lines.append("------------------------------")
    lines.append("💡 详细 7 天趋势明细与分商品数据已同步导出 Excel 报表。")
    return "\n".join(lines)


def export_weekly_report(db: Session, date_str: str) -> Path:
    """计算周报数据并生成 Excel 文件，返回文件 Path。"""
    data = get_weekly_data(db, date_str)
    excel_path = generate_weekly_excel_report(
        data,
        settings.export_dir,
        company=settings.company_name,
    )
    return Path(excel_path)
