"""群发文字摘要生成（规划 6.7）。

模板固定、全角符号逐字符匹配；总长度控制在 800 字符内（微信/钉钉
群消息友好），超出时按三级裁剪：TOP3 → 分平台只留前 4 → 需关注
只保留 critical。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.config import settings

MAX_SUMMARY_LEN = 800
TOP_N = 3
PLATFORM_KEEP_WHEN_TRIMMED = 4

_SEVERITY_MARK = {"critical": "🔴", "warning": "⚠️"}


def _fmt_money(value: float | None) -> str:
    """金额：¥123,456.78；>= 1 万且开启 SUMMARY_USE_WAN 时用万元简写。"""
    if value is None:
        return "—"
    if settings.summary_use_wan and abs(value) >= 10000:
        return f"¥{value / 10000:.2f}万"
    return f"¥{value:,.2f}"


def _fmt_delta(value: float | None, is_pp: bool = False) -> str:
    """变化率：+12.3% / -4.5%；百分点：+1.2pp；None → —。"""
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    if is_pp:
        return f"{sign}{value:.1f}pp"
    return f"{sign}{value * 100:.1f}%"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}"


def _profit_line(ov: dict) -> str | None:
    """利润行：毛利（率）｜经营利润。无成本数据时整行省略，不展示空指标。"""
    gross = ov.get("gross_profit", {})
    if gross.get("value") is None:
        return None
    return (
        f"毛利 {_fmt_money(gross['value'])}"
        f"（率 {_fmt_rate(ov.get('gross_margin', {}).get('value'))}）"
        f"｜经营利润 {_fmt_money(ov.get('operating_profit', {}).get('value'))}"
    )


def _platform_anomaly_marks(report: dict) -> dict[str, str]:
    """平台名 → 异常标记（取该平台最高级别）。"""
    marks: dict[str, str] = {}
    for a in report.get("anomalies", []):
        if a["scope"] != "platform":
            continue
        mark = _SEVERITY_MARK.get(a["severity"], "")
        if mark and mark not in marks.get(a["subject"], ""):
            # critical 优先
            if marks.get(a["subject"]) != "🔴":
                marks[a["subject"]] = mark
    return marks


def generate_text_summary(report: dict, company: str | None = None,
                          generated_at: datetime | None = None) -> str:
    """按固定模板生成纯文字摘要（<= 800 字符）。"""
    company = company if company is not None else settings.company_name
    generated_at = generated_at or datetime.now()
    ov = report["overview"]

    lines: list[str] = []
    lines.append(f"【{company}电商销售日报】{report['report_date']} {report['weekday']}")
    lines.append("")
    lines.append("◆ 整体")
    lines.append(
        f"GMV {_fmt_money(ov['gmv']['value'])}｜环比 {_fmt_delta(ov['gmv']['dod'])}"
        f"｜周同比 {_fmt_delta(ov['gmv']['wow'])}"
    )
    lines.append(
        f"实际成交 {_fmt_money(ov['net_amount']['value'])}"
        f"｜退款率 {_fmt_rate(ov['refund_rate']['value'])}"
        f"｜支付件数 {_fmt_num(ov['paid_qty']['value'])}"
        f"｜买家数 {_fmt_num(ov['buyers']['value'])}"
    )
    lines.append(
        f"客单价 {_fmt_money(ov['avg_order_value']['value'])}"
        f"｜转化率 {_fmt_rate(ov['conversion_rate']['value'])}"
        f"｜推广ROI {_fmt_num(ov['roi']['value'])}"
    )
    profit = _profit_line(ov)
    if profit:
        lines.append(profit)
    diag = report.get("diagnosis")
    if diag and diag.get("explanation"):
        lines.append(f"💡 归因：{diag['explanation']}")
    note = ov.get("note")
    if note:
        lines.append(note)
    lines.append("")

    # ◆ 分平台
    platform_lines: list[str] = []
    marks = _platform_anomaly_marks(report)
    for row in report["by_platform"]:
        m = row["metrics"]
        mark = marks.get(row["name"], "")
        platform_lines.append(
            f"· {row['name']} {_fmt_money(m['net_amount']['value'])}"
            f"（环比 {_fmt_delta(m['gmv']['dod'])}）{mark}"
        )
    lines.append("◆ 分平台")
    lines.extend(platform_lines)
    lines.append("")

    # ◆ TOP3 单品（无榜单数据时整段省略，不留空标题）
    top_lines: list[str] = []
    for i, item in enumerate(report["top"].get("by_gmv", [])[:TOP_N], start=1):
        name = item["name"][:20]
        top_lines.append(
            f"{i}. {name} {_fmt_money(item['net_amount'])}"
            f"（环比 {_fmt_delta(item.get('gmv_dod'))}）"
        )
    if top_lines:
        lines.append("◆ TOP3 单品")
        lines.extend(top_lines)
        lines.append("")

    # ◆ 需关注
    lines.append("◆ 需关注")
    anomalies = report.get("anomalies", [])
    if anomalies:
        for a in anomalies:
            mark = _SEVERITY_MARK.get(a["severity"], "·")
            lines.append(f"· [{mark}] {a['message']}")
    else:
        lines.append("· 本日无异常预警")
    lines.append("")

    # 缺失平台警告（无则省略）
    dc = report.get("data_completeness", {})
    if dc.get("warning"):
        lines.append(dc["warning"])

    lines.append(f"—— 由 电商日报工具 自动生成 {generated_at.strftime('%Y-%m-%d %H:%M')}")

    text = "\n".join(lines)
    if len(text) <= MAX_SUMMARY_LEN:
        return text
    return _trim(report, company, generated_at, marks)


def _trim(report: dict, company: str, generated_at: datetime,
          marks: dict[str, str]) -> str:
    """三级裁剪：TOP3 → 分平台只留前 4 → 需关注只保留 critical。

    进入本函数即意味着一级裁剪（丢 TOP3）已经发生，各阶段都是从
    report 重建，因此不接未裁剪的原文。若仍超长（极端大量 critical），
    继续收缩条数，最后硬截断兜底。
    """
    def _rebuild(platform_count: int, critical_only: bool,
                 keep_limit: int | None = None) -> str:
        ov = report["overview"]
        out: list[str] = []
        out.append(f"【{company}电商销售日报】{report['report_date']} {report['weekday']}")
        out.append("")
        out.append("◆ 整体")
        out.append(
            f"GMV {_fmt_money(ov['gmv']['value'])}｜环比 {_fmt_delta(ov['gmv']['dod'])}"
            f"｜周同比 {_fmt_delta(ov['gmv']['wow'])}"
        )
        out.append(
            f"实际成交 {_fmt_money(ov['net_amount']['value'])}"
            f"｜退款率 {_fmt_rate(ov['refund_rate']['value'])}"
            f"｜支付件数 {_fmt_num(ov['paid_qty']['value'])}"
            f"｜买家数 {_fmt_num(ov['buyers']['value'])}"
        )
        out.append(
            f"客单价 {_fmt_money(ov['avg_order_value']['value'])}"
            f"｜转化率 {_fmt_rate(ov['conversion_rate']['value'])}"
            f"｜推广ROI {_fmt_num(ov['roi']['value'])}"
        )
        profit = _profit_line(ov)
        if profit:
            out.append(profit)
        diag = report.get("diagnosis")
        if diag and diag.get("explanation"):
            out.append(f"💡 归因：{diag['explanation']}")
        if ov.get("note"):
            out.append(ov["note"])
        out.append("")
        out.append("◆ 分平台")
        for row in report["by_platform"][:platform_count]:
            m = row["metrics"]
            mark = marks.get(row["name"], "")
            out.append(
                f"· {row['name']} {_fmt_money(m['net_amount']['value'])}"
                f"（环比 {_fmt_delta(m['gmv']['dod'])}）{mark}"
            )
        out.append("")
        out.append("◆ 需关注")
        anomalies = report.get("anomalies", [])
        if critical_only and anomalies:
            criticals = [a for a in anomalies if a["severity"] == "critical"]
            # 无 critical 时不得反过来输出「本日无异常预警」：退化为保留
            # warning，条数仍由 keep_limit 收缩。severity 分级修正后，
            # 「整日只有 warning」是常态，旧逻辑会让群消息谎报平安。
            anomalies = criticals or anomalies
        if keep_limit is not None:
            anomalies = anomalies[:keep_limit]
        if anomalies:
            for a in anomalies:
                mark = _SEVERITY_MARK.get(a["severity"], "·")
                out.append(f"· [{mark}] {a['message']}")
        else:
            out.append("· 本日无异常预警")
        out.append("")
        dc = report.get("data_completeness", {})
        if dc.get("warning"):
            out.append(dc["warning"])
        out.append(f"—— 由 电商日报工具 自动生成 {generated_at.strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(out)

    # 一级：裁 TOP3（进入本函数即已生效）；二级：分平台只留 4；
    # 三级：只保留 critical；之后逐级收缩条数；最后硬截断兜底
    candidate = ""
    for platform_count, critical_only, keep_limit in [
        (PLATFORM_KEEP_WHEN_TRIMMED, False, None),
        (PLATFORM_KEEP_WHEN_TRIMMED, True, None),
        (PLATFORM_KEEP_WHEN_TRIMMED, True, 5),
        (2, True, 2),
        (0, True, 1),
    ]:
        candidate = _rebuild(platform_count, critical_only, keep_limit)
        if len(candidate) <= MAX_SUMMARY_LEN:
            return candidate
    return candidate[: MAX_SUMMARY_LEN - 1] + "…"
