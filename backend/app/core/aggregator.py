"""多维聚合引擎（规划 6.4）。

- **单次 SQL** 取出 ``[report_date-29, report_date]`` 全部 SalesFact，
  之后所有维度聚合在 pandas 内完成（禁止 N+1，SQL 总数 <= 3：
  1 次 facts + 1 次 Sku 名称 + 1 次 AnomalyRule）；
- 环比 / 周同比通过按日期分组对齐计算，缺失日期对比值为 None；
- 金额内部全程 Decimal，仅序列化时转 float 并 round(2)；
- 比率类指标的环比用百分点差值（pp）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import math
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from backend.app.core import NoDataForDateError
from backend.app.core.adapter_registry import load_builtin
from backend.app.core.anomaly import evaluate_rules
from backend.app.models.entities import AnomalyRule, SalesFact, Sku

# 参与求和的统一字段
_SUM_FIELDS = (
    "gmv",
    "refund_amount",
    "net_amount",
    "paid_qty",
    "buyers",
    "visitors",
    "ad_cost",
    "order_qty",
    # 成本三件套：由 _enrich_costs 在装载后按行追加（非数据库列）
    "cogs",
    "platform_fee",
    "fulfillment_cost",
)

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_ZERO = Decimal("0")


def _dec_sum(series: pd.Series) -> Decimal | None:
    """Decimal 列求和；全空返回 None（区分"无数据"与 0）。"""
    vals = [v for v in series if v is not None and not pd.isna(v)]
    if not vals:
        return None
    return sum((Decimal(str(v)) for v in vals), _ZERO)


def _div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """除零 / 空值 → None（规划 4.3 的除零处理约定）。"""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct_change(today: Decimal | None, base: Decimal | None) -> float | None:
    """变化率 = (today - base) / base；base 为 0 或 None → None。"""
    if today is None or base is None or base == 0:
        return None
    return float((today - base) / base)


def _pp_diff(today: Decimal | None, base: Decimal | None) -> float | None:
    """比率类环比：百分点差值（pp）。"""
    if today is None or base is None:
        return None
    return float((today - base) * 100)


def _f(v: Decimal | None, places: int = 2) -> float | None:
    """Decimal → float（序列化出口，round）。"""
    if v is None:
        return None
    return round(float(v), places)


def _metrics_for(sub: pd.DataFrame) -> dict[str, Decimal | None]:
    """对一个明细子集计算全部原始指标（Decimal）。"""
    sums = {f: _dec_sum(sub[f]) if f in sub.columns else None for f in _SUM_FIELDS}
    gmv = sums["gmv"]
    net = sums["net_amount"]
    refund = sums["refund_amount"]
    buyers = sums["buyers"]
    visitors = sums["visitors"]
    ad_cost = sums["ad_cost"]

    out: dict[str, Decimal | None] = dict(sums)
    out["refund_rate"] = _div(refund, gmv)
    out["conversion_rate"] = _div(buyers, visitors)
    out["avg_order_value"] = _div(net, buyers)
    out["roi"] = _div(net, ad_cost)
    out["ad_cost_ratio"] = _div(ad_cost, net)
    out["unit_price"] = _div(net, sums["paid_qty"])

    # ---- 利润链（规划外补充，2026-09）----
    # 毛利 = 成交额 - 货成本 - 平台扣点 - 履约成本；经营利润 = 毛利 - 推广费。
    # 子集中一行成本价都没有时 cogs 为 None → 毛利链整体为 None，
    # 宁可空缺也不把「未知成本」当 0 虚报利润
    cogs = sums["cogs"]
    platform_fee = sums["platform_fee"]
    fulfillment = sums["fulfillment_cost"]
    if net is None or cogs is None:
        gross = None
    else:
        gross = net - cogs - (platform_fee or _ZERO) - (fulfillment or _ZERO)
    out["gross_profit"] = gross
    out["gross_margin"] = _div(gross, net)
    out["operating_profit"] = None if gross is None else gross - (ad_cost or _ZERO)
    return out


def _round4(v: Decimal | None) -> float | None:
    return None if v is None else round(float(v), 4)


def _metric_blocks(
    today_m: dict[str, Decimal | None],
    dod_m: dict[str, Decimal | None] | None,
    wow_m: dict[str, Decimal | None] | None,
) -> dict[str, dict]:
    """把三期指标组织成 overview 的 MetricBlock 结构（6.4 契约）。

    所有键始终存在（无对比数据时为 null），方便前端统一渲染。
    """
    blocks: dict[str, dict] = {}

    def _blk(name: str, value, is_ratio: bool) -> dict:
        block: dict[str, Any] = {
            "value": _round4(value) if is_ratio else _f(value),
            "dod": None,
            "wow": None,
            "dod_pp": None,
            "wow_pp": None,
        }
        if dod_m is not None:
            if is_ratio:
                block["dod_pp"] = _r4(_pp_diff(today_m.get(name), dod_m.get(name)))
            else:
                block["dod"] = _r4(_pct_change(today_m.get(name), dod_m.get(name)))
        if wow_m is not None:
            if is_ratio:
                block["wow_pp"] = _r4(_pp_diff(today_m.get(name), wow_m.get(name)))
            else:
                block["wow"] = _r4(_pct_change(today_m.get(name), wow_m.get(name)))
        return block

    # unit_price（件单价）为金额类派生指标，按 is_ratio=False 走 dod/wow 百分比变化；
    # Excel「分类目」Sheet 依赖此块，缺失会导致件单价列恒为空
    for name in ("gmv", "net_amount", "refund_amount", "paid_qty", "buyers",
                 "visitors", "ad_cost", "avg_order_value", "unit_price",
                 "gross_profit", "operating_profit"):
        blocks[name] = _blk(name, today_m.get(name), is_ratio=False)

    # ROI 等倍率指标按百分比变化处理（口径说明中注明）
    for name in ("roi", "ad_cost_ratio"):
        blocks[name] = _blk(name, today_m.get(name), is_ratio=False)

    for name in ("refund_rate", "conversion_rate", "gross_margin", "refund_rate_7d"):
        blocks[name] = _blk(name, today_m.get(name), is_ratio=True)

    return blocks


def _r4(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


# _load_facts 需要的列（顺序即 DataFrame 列顺序）
_FACT_COLUMNS = (
    "stat_date",
    "platform_key",
    "shop_code",
    "shop_name",
    "platform_product_code",
    "product_name",
    "category",
    "sku_id",
    "order_qty",
    "paid_qty",
    "gmv",
    "refund_amount",
    "net_amount",
    "visitors",
    "buyers",
    "ad_cost",
)


def _load_facts(db: Session, start: date, end: date) -> pd.DataFrame:
    """单次 SQL 取出时间窗内全部销售明细到 DataFrame（金额保持 Decimal）。

    按列查询而非加载整个 ORM 实体：SalesFact 还有 raw_row_json 大字段，
    且省去每行一次 ORM 实例化的开销。空结果也带齐列名，下游无需判空列。
    """
    rows = (
        db.query(*(getattr(SalesFact, c) for c in _FACT_COLUMNS))
        .filter(SalesFact.stat_date >= start, SalesFact.stat_date <= end)
        .all()
    )
    return pd.DataFrame.from_records(rows, columns=_FACT_COLUMNS)


_TWO_PLACES = Decimal("0.01")


def _enrich_costs(db: Session, df: pd.DataFrame, sku_map: dict[int, Sku]) -> pd.DataFrame:
    """在 facts DataFrame 上追加成本三列：cogs / platform_fee / fulfillment_cost。

    - cogs：sku.cost_price × paid_qty；未映射 SKU / 未填成本价 / 无件数 → None
      （None 是「成本未知」的信号，绝不按 0 处理，否则利润会被虚报）
    - platform_fee：net_amount × 适配器 platform_fee_rate（用户覆盖版生效）
    - fulfillment_cost：paid_qty × 适配器 fulfillment_cost_per_unit
    """
    from backend.app.core.adapter_registry import get_adapter

    if df.empty:
        df["cogs"] = pd.Series(dtype=object)
        df["platform_fee"] = pd.Series(dtype=object)
        df["fulfillment_cost"] = pd.Series(dtype=object)
        return df

    # 平台 → (扣点比例, 单件履约成本)，用户覆盖版适配器优先
    fee_map: dict[str, tuple[Decimal, Decimal]] = {}
    for p in df["platform_key"].dropna().unique():
        try:
            sem = get_adapter(db, p).spec.metric_semantics
            fee_map[p] = (
                Decimal(sem.platform_fee_rate),
                Decimal(sem.fulfillment_cost_per_unit),
            )
        except Exception:  # noqa: BLE001
            fee_map[p] = (_ZERO, _ZERO)

    cogs_col: list[Decimal | None] = []
    fee_col: list[Decimal | None] = []
    ful_col: list[Decimal | None] = []
    for sid, qty, net, platform in zip(
        df["sku_id"].tolist(),
        df["paid_qty"].tolist(),
        df["net_amount"].tolist(),
        df["platform_key"].tolist(),
    ):
        cost_price = None
        if sid is not None and not pd.isna(sid):
            sku = sku_map.get(int(sid))
            cost_price = sku.cost_price if sku is not None else None
        qty_dec = None if qty is None or pd.isna(qty) else Decimal(str(qty))

        cogs_col.append(
            (cost_price * qty_dec).quantize(_TWO_PLACES)
            if cost_price is not None and qty_dec is not None
            else None
        )
        rate, per_unit = fee_map.get(platform, (_ZERO, _ZERO))
        fee_col.append(
            (Decimal(net) * rate).quantize(_TWO_PLACES) if net is not None else None
        )
        ful_col.append(
            (qty_dec * per_unit).quantize(_TWO_PLACES) if qty_dec is not None else None
        )

    df = df.copy()
    df["cogs"] = cogs_col
    df["platform_fee"] = fee_col
    df["fulfillment_cost"] = ful_col
    return df


def _sub(df: pd.DataFrame, d: date) -> pd.DataFrame:
    return df[df["stat_date"] == d]


def _compute_rolling_7d_refund(df: pd.DataFrame, target_date: date) -> Decimal | None:
    """计算 target_date 截止的 7 日滚动退款率：7日总退款 / 7日总GMV。

    平滑掉大促后突发集中退款带来的单日退款率失真（>100% 假警报）。
    """
    start_d = target_date - timedelta(days=6)
    sub = df[(df["stat_date"] >= start_d) & (df["stat_date"] <= target_date)]
    if sub.empty:
        return None
    refund_7d = _dec_sum(sub["refund_amount"])
    gmv_7d = _dec_sum(sub["gmv"])
    return _div(refund_7d, gmv_7d)


def _decompose_gmv_dod(
    today_m: dict[str, Any],
    dod_m: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """杜邦三因子增量归因（LMDI 对数均值指数分解）。

    GMV = 访客数(V) × 购买转化率(CR) × 客单价(AOV)
    把 GMV 环比变化率准确拆解为：
    - 访客流量贡献（visitors）
    - 转化率贡献（conversion_rate）
    - 客单价贡献（avg_order_value）
    三者之和严格等于 GMV 环比总变化率（无残差）。
    """
    if dod_m is None:
        return None

    g1 = float(today_m.get("gmv") or 0)
    g0 = float(dod_m.get("gmv") or 0)
    v1 = float(today_m.get("visitors") or 0)
    v0 = float(dod_m.get("visitors") or 0)
    b1 = float(today_m.get("buyers") or 0)
    b0 = float(dod_m.get("buyers") or 0)

    if g0 <= 0 or g1 <= 0 or v0 <= 0 or v1 <= 0 or b0 <= 0 or b1 <= 0:
        return None

    cr0 = b0 / v0
    cr1 = b1 / v1
    aov0 = g0 / b0
    aov1 = g1 / b1

    # GMV 环比总变化率
    gmv_dod = (g1 - g0) / g0

    # 对数均值权重 L(g1, g0)
    if abs(g1 - g0) < 1e-9:
        w = g0
    else:
        w = (g1 - g0) / math.log(g1 / g0)

    # 因子贡献变化量
    delta_v = w * math.log(v1 / v0)
    delta_cr = w * math.log(cr1 / cr0)
    delta_aov = w * math.log(aov1 / aov0)

    # 转化为相对于基期 GMV0 的贡献百分比 (可加和等于 gmv_dod)
    c_v = delta_v / g0
    c_cr = delta_cr / g0
    c_aov = delta_aov / g0

    contributions = {
        "visitors": round(c_v, 4),
        "conversion_rate": round(c_cr, 4),
        "avg_order_value": round(c_aov, 4),
    }

    if gmv_dod < -0.005:
        worst_key = min(contributions, key=contributions.get)
        labels = {
            "visitors": "商品访客数减少（引流不足）",
            "conversion_rate": "购买转化率下滑（承接力减弱）",
            "avg_order_value": "客单价降低（低价件占比高或折扣加深）",
        }
        short_labels = {
            "visitors": "访客流量下滑",
            "conversion_rate": "转化率受挫",
            "avg_order_value": "客单价走低",
        }
        explanation = (
            f"GMV 环比下滑 {abs(gmv_dod) * 100:.1f}%，主要由{labels[worst_key]}拖累"
            f"（贡献 {contributions[worst_key] * 100:+.1f}pp）。"
        )
        driver_label = short_labels[worst_key]
        primary = worst_key
    elif gmv_dod > 0.005:
        best_key = max(contributions, key=contributions.get)
        labels = {
            "visitors": "商品访客增加（引流强劲）",
            "conversion_rate": "购买转化率提升（转化承接高效）",
            "avg_order_value": "客单价拉升（高价爆款或连带率提升）",
        }
        short_labels = {
            "visitors": "访客流量增长",
            "conversion_rate": "转化率提升",
            "avg_order_value": "客单价拉升",
        }
        explanation = (
            f"GMV 环比增长 {gmv_dod * 100:.1f}%，主要由{labels[best_key]}驱动"
            f"（贡献 {contributions[best_key] * 100:+.1f}pp）。"
        )
        driver_label = short_labels[best_key]
        primary = best_key
    else:
        primary = "stable"
        driver_label = "表现平稳"
        explanation = "GMV 环比基本持平，各项指标波动在正常合理区间。"

    return {
        "primary_driver": primary,
        "driver_label": driver_label,
        "explanation": explanation,
        "gmv_dod": round(gmv_dod, 4),
        "contributions": contributions,
    }


def _platform_display(key: str) -> str:
    """平台 key → 展示名；未知 key 原样返回（NULL 也不会漏出 nan）。"""
    if key is None or (isinstance(key, float) and pd.isna(key)):
        return "未知平台"
    try:
        return load_builtin(str(key)).display_name
    except Exception:  # noqa: BLE001
        return str(key)


def _shipping_note(platforms: list[str]) -> str | None:
    """含运费口径标注（规划 5.4：京东 GMV 不可与其他平台直接相加）。"""
    notes = []
    for key in platforms:
        try:
            spec = load_builtin(key).spec
        except Exception:  # noqa: BLE001
            continue
        if spec.metric_semantics.gmv_includes_shipping:
            notes.append(f"*{_platform_display(key)}GMV含运费")
    return "、".join(notes) if notes else None


def _eq_mask(frame: pd.DataFrame, col: str, val) -> pd.Series:
    """None 安全的等值掩码（groupby(dropna=False) 的 NA 组键也能对齐）。"""
    s = frame[col]
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return s.isna()
    return s.eq(val)


def _dim_key_text(key_dict: dict, col: str, default: str) -> str:
    """维度分组键的展示文本。

    该维度取值为 NULL 时返回 default。**不能写成 `v or default`**：
    `_load_facts` 用 `pd.DataFrame.from_records` 建表，含 NULL 的字符串列会被
    推断成 float64，NULL 变成浮点 NaN，而 NaN 是**真值**，`nan or d` 仍得 nan。
    这曾让「未分类」兜底失效，聚合结果里冒出 `name=nan`，被 DailyReportData
    契约拒绝 → GET /api/reports/daily 直接 500。
    """
    v = key_dict.get(col)
    if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip()):
        return default
    return str(v)


def _dim_rows(
    df: pd.DataFrame,
    key_cols: list[str],
    d0: date,
    d1: date,
    d2: date,
    name_of,
    total_net: Decimal | None,
) -> list[dict]:
    """通用维度聚合：今日行为主行，对比取同维度历史值。"""
    today_df = _sub(df, d0)
    rows: list[dict] = []
    if today_df.empty:
        return rows

    total_net_dec = total_net or _ZERO

    # 昨日 / 上周同日明细各过滤一次即可：旧实现把这两次全表过滤放在
    # groupby 循环内，每个维度组都重复执行，维度组数越多浪费越大
    dod_all = _sub(df, d1)
    wow_all = _sub(df, d2)

    for keys, sub in today_df.groupby(key_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        key_dict = dict(zip(key_cols, keys))

        dod_sub = dod_all
        for col, val in key_dict.items():
            if col in dod_sub.columns:
                dod_sub = dod_sub[_eq_mask(dod_sub, col, val)]
        wow_sub = wow_all
        for col, val in key_dict.items():
            if col in wow_sub.columns:
                wow_sub = wow_sub[_eq_mask(wow_sub, col, val)]

        m0 = _metrics_for(sub)
        m1 = _metrics_for(dod_sub) if not dod_sub.empty else None
        m2 = _metrics_for(wow_sub) if not wow_sub.empty else None

        share = None
        if m0["net_amount"] is not None and total_net_dec != 0:
            share = _r4(float(m0["net_amount"] / total_net_dec))

        rows.append(
            {
                "key": "_".join(str(k) for k in keys),
                "name": name_of(key_dict, sub),
                "metrics": _metric_blocks(m0, m1, m2),
                "share": share,
            }
        )

    rows.sort(key=lambda r: r["metrics"]["net_amount"]["value"] or 0, reverse=True)
    return rows


def _sku_context(df: pd.DataFrame, d0: date, d1: date) -> dict[str, dict[str, Any]]:
    """SKU 维度主体上下文（异常规则求值用，含环比）。"""
    ctx: dict[str, dict[str, Any]] = {}
    today = _sub(df, d0)
    yesterday = _sub(df, d1)
    if today.empty:
        return ctx
    for sku_id, sub in today.groupby("sku_id", dropna=False):
        if sku_id is None or pd.isna(sku_id):
            continue
        m0 = _metrics_for(sub)
        y_sub = yesterday[yesterday["sku_id"] == sku_id] if not yesterday.empty else yesterday
        m1 = _metrics_for(y_sub) if not y_sub.empty else None
        # pandas 会把含 NA 的整型列提升为 float，键统一规范为整数字符串
        ctx[str(int(sku_id))] = _rule_ctx(m0, m1)
    return ctx


def _platform_context(df: pd.DataFrame, d0: date, d1: date) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {}
    today = _sub(df, d0)
    yesterday = _sub(df, d1)
    for platform, sub in today.groupby("platform_key", dropna=False):
        m0 = _metrics_for(sub)
        y_sub = yesterday[yesterday["platform_key"] == platform] if not yesterday.empty else yesterday
        m1 = _metrics_for(y_sub) if not y_sub.empty else None
        ctx[str(platform)] = _rule_ctx(m0, m1)
    return ctx


def _rule_ctx(m0: dict[str, Decimal | None], m1: dict[str, Decimal | None] | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "gmv": m0.get("gmv") or _ZERO,
        "refund_rate": m0.get("refund_rate"),
        "conversion_rate": m0.get("conversion_rate"),
        "roi": m0.get("roi"),
        "ad_cost_ratio": m0.get("ad_cost_ratio"),
    }
    if m1 is not None:
        ctx["gmv_dod"] = _pct_change(m0.get("gmv"), m1.get("gmv"))
        ctx["conversion_rate_dod_pp"] = _pp_diff(
            m0.get("conversion_rate"), m1.get("conversion_rate")
        )
    return ctx


def _top_lists(
    sku_rows: list[dict],
    sku_yesterday: dict[int, dict[str, Decimal | None]],
) -> dict[str, list[dict]]:
    """TOP 榜单（规划 6.4）：GMV 前 10 / 涨幅 / 跌幅（基数>1000）/ 退款率（gmv>500）。"""
    def _brief(row: dict) -> dict:
        return {
            "sku_code": row["sku_code"],
            "name": row["name"],
            "category": row["category"],
            "gmv": row["metrics"]["gmv"]["value"],
            "net_amount": row["metrics"]["net_amount"]["value"],
            "refund_rate": row["metrics"]["refund_rate"]["value"],
            "gmv_dod": row["metrics"]["gmv"].get("dod"),
        }

    by_gmv = sorted(sku_rows, key=lambda r: r["metrics"]["gmv"]["value"] or 0, reverse=True)[:10]

    growth, decline = [], []
    for row in sku_rows:
        dod = row["metrics"]["gmv"].get("dod")
        if dod is None:
            continue
        if row["sku_id"] not in sku_yesterday:
            continue
        base = sku_yesterday[row["sku_id"]].get("gmv")
        if base is None or base <= Decimal("1000"):
            continue
        (growth if dod > 0 else decline).append(_brief(row))
    growth.sort(key=lambda r: r["gmv_dod"] or 0, reverse=True)
    decline.sort(key=lambda r: r["gmv_dod"] or 0)

    refunds = [
        _brief(r)
        for r in sku_rows
        if (r["metrics"]["gmv"]["value"] or 0) > 500
        and r["metrics"]["refund_rate"]["value"] is not None
    ]
    refunds.sort(key=lambda r: r["refund_rate"] or 0, reverse=True)

    return {
        "by_gmv": [_brief(r) for r in by_gmv],
        "by_growth": growth[:10],
        "by_decline": decline[:10],
        "by_refund": refunds[:10],
    }


def build_daily_report(
    db: Session,
    report_date: date,
    scope: dict | None = None,
    compare_enabled: bool = True,
) -> dict:
    """聚合主入口：返回 6.4 契约的完整结构（含 anomalies）。"""
    scope = scope or {}
    platforms_filter = scope.get("platforms") or None
    shops_filter = scope.get("shops") or None

    start = report_date - timedelta(days=29)
    df = _load_facts(db, start, report_date)  # SQL #1

    if platforms_filter:
        df = df[df["platform_key"].isin(platforms_filter)]
    if shops_filter and "shop_code" in df.columns:
        df = df[df["shop_code"].isin(shops_filter) | df["shop_name"].isin(shops_filter)]

    d0 = report_date
    d1 = report_date - timedelta(days=1)
    d2 = report_date - timedelta(days=7)

    if df.empty or _sub(df, d0).empty:
        raise NoDataForDateError(
            f"{report_date.isoformat()} 无任何销售数据",
            detail={"report_date": report_date.isoformat(), "scope": scope},
        )

    # ---- SKU 名称（SQL #2） ----
    sku_ids = {int(s) for s in df["sku_id"].dropna().unique().tolist()}
    sku_map: dict[int, Sku] = {}
    if sku_ids:
        sku_map = {s.id: s for s in db.query(Sku).filter(Sku.id.in_(sku_ids)).all()}

    # 成本三列（cogs / platform_fee / fulfillment_cost）追加到窗口全量数据，
    # 之后今日 / 昨日 / 上周同日 / 趋势的所有子集都自带成本，环比同口径
    df = _enrich_costs(db, df, sku_map)

    today_df = _sub(df, d0)
    overall_today = _metrics_for(today_df)
    total_net = overall_today["net_amount"]

    # ---- data_completeness ----
    from backend.app.core.adapter_registry import list_builtin_keys

    expected = list_builtin_keys()
    present = sorted(set(today_df["platform_key"].tolist()))
    # 含运费标注按 30 天窗口内有数据的平台判断（趋势/合计包含该平台即需标注）
    window_platforms = sorted(set(df["platform_key"].tolist()))
    missing = [p for p in expected if p not in present]
    warning = None
    if missing:
        names = "、".join(_platform_display(p) for p in missing)
        warning = f"缺失 {len(missing)} 个平台数据，合计值不完整：{names}"

    # 成本覆盖率：今日有成本价的成交额占比。<100% 时利润指标只覆盖
    # 部分商品（未映射 SKU / 未填成本价），必须显式提示而非静默虚报
    covered_net = _dec_sum(today_df.loc[today_df["cogs"].notna(), "net_amount"])
    total_net_cov = overall_today["net_amount"]
    cost_coverage = (
        _r4(float(covered_net / total_net_cov))
        if total_net_cov and covered_net is not None
        else (0.0 if total_net_cov else None)
    )
    if cost_coverage is not None and cost_coverage < 0.999:
        cov_warn = (
            f"仅 {cost_coverage * 100:.1f}% 的成交额有成本价，"
            "毛利/经营利润只统计了这部分商品（去 SKU 管理补成本价）"
        )
        warning = f"{warning}；{cov_warn}" if warning else cov_warn

    # ---- 7 日滚动退款率与 overview ----
    overall_today["refund_rate_7d"] = _compute_rolling_7d_refund(df, d0)
    if compare_enabled:
        y_df = _sub(df, d1)
        w_df = _sub(df, d2)
        overall_dod = _metrics_for(y_df) if not y_df.empty else None
        overall_wow = _metrics_for(w_df) if not w_df.empty else None
        if overall_dod is not None:
            overall_dod["refund_rate_7d"] = _compute_rolling_7d_refund(df, d1)
        if overall_wow is not None:
            overall_wow["refund_rate_7d"] = _compute_rolling_7d_refund(df, d2)
    else:
        overall_dod = overall_wow = None
    overview = _metric_blocks(overall_today, overall_dod, overall_wow)
    note = _shipping_note(window_platforms)
    if note:
        overview["note"] = note

    # GMV 环比杜邦三因子增量归因（诊断层）
    diagnosis = _decompose_gmv_dod(overall_today, overall_dod) if compare_enabled else None

    # Excel「日报总览」需要昨日 / 上周同日的绝对值（6.4 契约之外的内生补充，
    # 仅用于导出与 DailyReport.metrics_json 快照）
    overview_raw: dict[str, dict] = {}
    for name in ("gmv", "net_amount", "refund_amount", "paid_qty", "buyers",
                 "visitors", "ad_cost", "avg_order_value", "roi", "ad_cost_ratio",
                 "refund_rate", "conversion_rate", "refund_rate_7d",
                 "gross_profit", "gross_margin", "operating_profit"):
        is_ratio = name in ("refund_rate", "conversion_rate", "gross_margin", "refund_rate_7d")
        conv = _round4 if is_ratio else _f
        overview_raw[name] = {
            "today": conv(overall_today.get(name)),
            "dod_value": conv(overall_dod.get(name)) if overall_dod else None,
            "wow_value": conv(overall_wow.get(name)) if overall_wow else None,
        }

    # ---- 维度聚合 ----
    by_platform = _dim_rows(
        df, ["platform_key"], d0, d1, d2,
        lambda k, sub: _platform_display(k["platform_key"]), total_net,
    )

    def shop_name(k, sub):
        # shop_code 有 normalizer 的"空则回填 shop_name"兜底，两者常相等；
        # 都为 NULL 时才是真的没店铺信息，兜底文案别漏出 __UNKNOWN__/nan
        for col in ("shop_name", "shop_code"):
            text = _dim_key_text(k, col, "")
            if text and text != "__UNKNOWN__":
                return text
        return "未命名店铺"

    by_shop = _dim_rows(
        df, ["platform_key", "shop_code", "shop_name"], d0, d1, d2,
        lambda k, sub: f"{_platform_display(k['platform_key'])} · {shop_name(k, sub)}",
        total_net,
    )
    by_category = _dim_rows(
        df, ["category"], d0, d1, d2,
        lambda k, sub: _dim_key_text(k, "category", "未分类"),
        total_net,
    )

    # ---- 分SKU（mapped / unmapped） ----
    mapped_rows: list[dict] = []
    unmapped_rows: list[dict] = []
    y_df = _sub(df, d1)
    w_df = _sub(df, d2)
    sku_yesterday: dict[int, dict[str, Decimal | None]] = {}

    for (sku_id,), sub in today_df.groupby(["sku_id"], dropna=False):
        if sku_id is None or pd.isna(sku_id):
            continue
        sku = sku_map.get(int(sku_id))
        y_sub = y_df[y_df["sku_id"] == sku_id] if not y_df.empty else y_df
        w_sub = w_df[w_df["sku_id"] == sku_id] if not w_df.empty else w_df
        m0 = _metrics_for(sub)
        m1 = _metrics_for(y_sub) if not y_sub.empty else None
        m2 = _metrics_for(w_sub) if not w_sub.empty else None
        if not y_sub.empty:
            sku_yesterday[int(sku_id)] = _metrics_for(y_sub)
        # 该 SKU 今日在哪些平台有销售（Excel 分SKU 展示用）
        platform_list = sorted(
            {_platform_display(p) for p in sub["platform_key"].dropna().unique().tolist()}
        )
        cat_series = sub["category"].dropna()
        mapped_rows.append(
            {
                "sku_id": int(sku_id),
                "sku_code": sku.sku_code if sku else str(sku_id),
                "name": sku.name if sku else f"SKU#{sku_id}",
                "category": (sku.category if sku else None)
                or (cat_series.iloc[0] if not cat_series.empty else None),
                "platforms": "、".join(platform_list),
                "metrics": _metric_blocks(m0, m1 if compare_enabled else None,
                                          m2 if compare_enabled else None),
            }
        )
    mapped_rows.sort(key=lambda r: r["metrics"]["net_amount"]["value"] or 0, reverse=True)

    unmapped_df = today_df[today_df["sku_id"].isna()]
    for keys, sub in unmapped_df.groupby(
        ["platform_key", "platform_product_code"], dropna=False
    ):
        platform_key, code = keys
        m0 = _metrics_for(sub)
        y_sub = y_df[
            (y_df["platform_key"] == platform_key)
            & (y_df["platform_product_code"] == code)
        ] if not y_df.empty else y_df
        m1 = _metrics_for(y_sub) if not y_sub.empty else None
        name = sub["product_name"].dropna()
        unmapped_rows.append(
            {
                "platform_key": platform_key,
                "platform_product_code": code,
                "product_name": name.iloc[0] if not name.empty else code,
                "metrics": _metric_blocks(m0, m1 if compare_enabled else None, None),
            }
        )
    unmapped_rows.sort(key=lambda r: r["metrics"]["net_amount"]["value"] or 0, reverse=True)

    # ---- 趋势（近 30 天） ----
    dates = [start + timedelta(days=i) for i in range(30)]
    trend_dates: list[str] = []
    gmv_series: list[float | None] = []
    net_series: list[float | None] = []
    refund_rate_series: list[float | None] = []
    # 一次 groupby 取代 30 次全表过滤
    by_date = dict(tuple(df.groupby("stat_date", sort=False))) if not df.empty else {}
    for d in dates:
        sub = by_date.get(d)
        trend_dates.append(d.isoformat())
        if sub is None or sub.empty:
            gmv_series.append(None)
            net_series.append(None)
            refund_rate_series.append(None)
            continue
        m = _metrics_for(sub)
        gmv_series.append(_f(m["gmv"]))
        net_series.append(_f(m["net_amount"]))
        refund_rate_series.append(_round4(m["refund_rate"]))

    # ---- 异常预警（SQL #3：规则表） ----
    rules = db.query(AnomalyRule).filter(AnomalyRule.enabled.is_(True)).all()
    # 平台 / SKU 上下文始终按昨日构建：退款率、ROI 等"级别型"规则只看
    # 当日值，关闭环比对比（compare_enabled=False）时也必须照常评估；
    # 仅在关闭对比时剥离 *_dod 键，让环比型规则因缺值而自然跳过
    rule_context = {
        "overall": {"整体": _rule_ctx(overall_today, overall_dod if compare_enabled else None)},
        "platform": _platform_context(df, d0, d1),
        "sku": _sku_context(df, d0, d1),
    }
    if not compare_enabled:
        for scope_ctx in (rule_context["platform"], rule_context["sku"]):
            for ctx in scope_ctx.values():
                ctx.pop("gmv_dod", None)
                ctx.pop("conversion_rate_dod_pp", None)
    # 主体名换成可读名称
    rule_context["platform"] = {
        _platform_display(k): v for k, v in rule_context["platform"].items()
    }
    sku_ctx_named = {}
    for sid, ctx in rule_context["sku"].items():
        sku = sku_map.get(int(sid))
        sku_ctx_named[f"{sku.sku_code} {sku.name}" if sku else f"SKU#{sid}"] = ctx
    rule_context["sku"] = sku_ctx_named

    anomalies = evaluate_rules(rules, rule_context)

    return {
        "report_date": report_date.isoformat(),
        "weekday": WEEKDAY_NAMES[report_date.weekday()],
        "compare_dates": {"dod": d1.isoformat(), "wow": d2.isoformat()},
        "data_completeness": {
            "expected_platforms": expected,
            "present_platforms": present,
            "missing_platforms": missing,
            "warning": warning,
            "cost_coverage": cost_coverage,
        },
        "overview": overview,
        "overview_raw": overview_raw,
        "diagnosis": diagnosis,
        "by_platform": by_platform,
        "by_shop": by_shop,
        "by_category": by_category,
        "by_sku": {"mapped": mapped_rows, "unmapped": unmapped_rows},
        "trend": {
            "dates": trend_dates,
            "series": {
                "gmv": gmv_series,
                "net_amount": net_series,
                "refund_rate": refund_rate_series,
            },
        },
        "top": _top_lists(mapped_rows, sku_yesterday),
        "anomalies": anomalies,
    }
