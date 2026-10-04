"""异常预警规则引擎（规划 6.5）。

遍历启用的 AnomalyRule，对指定 scope 的每个主体求值：
- ``gt``：值 > 阈值触发；``lt``：值 < 阈值触发；
  ``abs_gt``：|变化率| > 阈值触发（metric 应为 xxx_dod 形式）
- ``min_base``：仅当主体 gmv >= min_base 时才评估，避免小样本噪音
- severity：超出阈值 1 倍以内 → warning，超过 1 倍 → critical

主体指标上下文由聚合引擎提供（同一份 DataFrame 计算，不额外发 SQL）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# 规则 metric → 主体上下文中的取值键（派生指标见规划 4.3）
SUPPORTED_METRICS = {
    "gmv_dod",              # GMV 环比（-1 ~ +∞）
    "refund_rate",          # 退款率
    "conversion_rate",      # 转化率
    "conversion_rate_dod_pp",  # 转化率环比（百分点差值）
    "roi",                  # 推广 ROI
    "ad_cost_ratio",        # 推广费比
}

_METRIC_LABELS = {
    "gmv_dod": "GMV 环比",
    "refund_rate": "退款率",
    "conversion_rate": "转化率",
    "conversion_rate_dod_pp": "转化率环比",
    "roi": "推广 ROI",
    "ad_cost_ratio": "推广费比",
}


def _overshoot(value: float, operator: str, threshold: float) -> float:
    """超出阈值的程度（severity 分级用）：<=1 为 warning，>1 为 critical。

    统一按「越过阈值的绝对量 / |阈值|」计算，对正负阈值均成立：
    - ``gt``：越过量 = value - threshold
    - ``lt``：越过量 = threshold - value（阈值常为负，如 gmv_dod < -0.40）
    - ``abs_gt``：越过量 = |value| - |threshold|

    旧实现对 ``lt`` 用 abs(threshold) - value，在负阈值下刚过线即得 >2，
    导致 warning 级完全失效。
    """
    base = abs(threshold)
    if not base:
        return 0.0
    if operator == "abs_gt":
        return (abs(value) - base) / base
    if operator == "lt":
        return (threshold - value) / base
    # gt
    return (value - threshold) / base


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def evaluate_rule(rule, subject: str, ctx: dict[str, Any]) -> dict | None:
    """对单个主体评估单条规则；不满足条件返回 None。

    ``ctx`` 需包含规则 metric 对应的值，以及作为小样本门槛的 gmv。
    """
    metric = rule.metric
    if metric not in SUPPORTED_METRICS:
        return None

    value = ctx.get(metric)
    base_gmv = ctx.get("gmv") or Decimal("0")
    if value is None:
        return None
    if base_gmv < Decimal(str(rule.min_base)):
        return None

    threshold = float(rule.threshold)
    v = float(value)
    triggered = False
    if rule.operator == "gt":
        triggered = v > threshold
    elif rule.operator == "lt":
        triggered = v < threshold
    elif rule.operator == "abs_gt":
        triggered = abs(v) > threshold

    if not triggered:
        return None

    over = _overshoot(v, rule.operator, threshold)
    severity = "critical" if over > 1.0 else "warning"

    label = _METRIC_LABELS.get(metric, metric)
    if metric in ("refund_rate", "conversion_rate", "ad_cost_ratio"):
        value_text = _fmt_pct(v)
        threshold_text = _fmt_pct(threshold)
    elif metric in ("gmv_dod", "conversion_rate_dod_pp"):
        value_text = f"{v * 100:+.1f}%" if metric == "gmv_dod" else f"{v:+.1f}pp"
        threshold_text = (
            f"{threshold * 100:+.0f}%" if metric == "gmv_dod" else f"{threshold:+.1f}pp"
        )
    else:
        value_text = _fmt_num(v)
        threshold_text = _fmt_num(threshold)

    direction = {"gt": "高于阈值", "lt": "低于阈值", "abs_gt": "波动超过阈值"}[rule.operator]
    message = f"{subject} {label} {value_text}，{direction} {threshold_text}"

    return {
        "rule_name": rule.name,
        "severity": severity,
        "scope": rule.scope,
        "subject": subject,
        "metric": metric,
        "value": v,
        "threshold": threshold,
        "message": message,
    }


def evaluate_rules(rules: list, context: dict[str, dict[str, dict[str, Any]]]) -> list[dict]:
    """遍历全部启用规则 × 全部 scope 主体，返回触发的异常列表。

    ``context`` 结构：``{scope: {subject: {metric: value}}}``，
    其中 overall / platform / sku / shop / category 为合法 scope。
    """
    scored: list[tuple[float, dict]] = []
    for rule in rules:
        if not rule.enabled:
            continue
        scope_ctx = context.get(rule.scope, {})
        for subject, ctx in scope_ctx.items():
            result = evaluate_rule(rule, subject, ctx)
            if result is not None:
                scored.append(
                    (_overshoot(result["value"], rule.operator, result["threshold"]), result)
                )

    # critical 优先，同级按超出程度降序（越严重越靠前）
    scored.sort(key=lambda t: (t[1]["severity"] != "critical", -t[0]))
    return [result for _, result in scored]
