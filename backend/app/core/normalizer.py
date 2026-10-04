"""归一化引擎（规划 6.2）。

``DataFrame(原始表头)`` + ``AdapterSpec`` → ``NormalizeResult``。

设计要点：
- 转换全部基于 pandas 向量化 / 列级推导，不做逐行 ``iterrows``；
- 金额全程 ``Decimal``，禁止 float 参与金额运算；
- 行过滤 / 去重 / 失败行均有行级错误记录，绝不静默丢弃
  （filtered_out 与 duplicate_row 不算失败，只在报告中统计）。

处理顺序（规划 6.2）：列映射 → 类型转换 → shop_code 兜底 → 行过滤
→ 去重 → 口径换算（net_amount）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import pandas as pd

from backend.app.core import MissingRequiredColumnError
from backend.app.core.semantics import compute_net_amount
from backend.app.schemas.adapter import AdapterSpec, RowFilter, TransformSpec

# 金额量化到 2 位小数
_TWO_PLACES = Decimal("0.01")
# 空占位符：视为 default（规划 6.2 money 规则）
_EMPTY_PLACEHOLDERS = {"", "-", "--"}
# Excel 序列日期起点（1900 日期系统）
_EXCEL_EPOCH = "1899-12-30"
_EXCEL_SERIAL_RE = re.compile(r"^\d+(\.\d+)?$")

_UNIFIED_STRING_FIELDS = (
    "shop_code",
    "shop_name",
    "platform_product_code",
    "product_name",
    "category",
)
_UNIFIED_INT_FIELDS = ("order_qty", "paid_qty", "visitors", "buyers")
_UNIFIED_MONEY_FIELDS = ("gmv", "refund_amount", "ad_cost")


@dataclass
class RowError:
    """行级记录（filtered_out / duplicate_row 属于非失败记录）。"""

    row_number: int  # 文件内 1-based 行号（含表头偏移）
    error_type: str
    error_message: str
    raw_content: str | None = None


@dataclass
class NormalizedRow:
    """归一化后的一行（统一 Schema）。"""

    stat_date: date
    platform_key: str
    shop_code: str | None
    shop_name: str | None
    platform_product_code: str
    product_name: str | None
    category: str | None
    order_qty: int | None
    paid_qty: int | None
    gmv: Decimal
    refund_amount: Decimal | None
    net_amount: Decimal
    visitors: int | None
    buyers: int | None
    ad_cost: Decimal | None
    raw_row_json: dict[str, Any]
    # commit 阶段由 sku_mapper.attach_sku_ids 回填（精确匹配 / 查表命中）；
    # 未映射保持 None
    sku_id: int | None = None

    def to_db_dict(self, upload_id: int, sku_id: int | None = None) -> dict[str, Any]:
        """转成 SalesFact 可用的字段字典。

        ``sku_id`` 显式传参时优先；否则使用 attach_sku_ids 回填到行上的值。
        """
        return {
            "upload_id": upload_id,
            "sku_id": sku_id if sku_id is not None else self.sku_id,
            "stat_date": self.stat_date,
            "platform_key": self.platform_key,
            "shop_code": self.shop_code,
            "shop_name": self.shop_name,
            "platform_product_code": self.platform_product_code,
            "product_name": self.product_name,
            "category": self.category,
            "order_qty": self.order_qty,
            "paid_qty": self.paid_qty,
            "gmv": self.gmv,
            "refund_amount": self.refund_amount,
            "net_amount": self.net_amount,
            "visitors": self.visitors,
            "buyers": self.buyers,
            "ad_cost": self.ad_cost,
            "raw_row_json": self.raw_row_json,
        }


@dataclass
class NormalizeResult:
    rows: list[NormalizedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    semantics_note: str = ""


# ---------------------------------------------------------------------------
# 列映射
# ---------------------------------------------------------------------------


def _resolve_columns(spec: AdapterSpec, df: pd.DataFrame) -> dict[str, str]:
    """列映射：column_map 精确匹配 → column_aliases 兜底。

    返回 统一字段 → 源列名。required 缺失时抛
    ``MissingRequiredColumnError``（含缺失列与文件实际列名清单）。
    """
    resolved: dict[str, str] = {}
    for src, dst in spec.column_map.items():
        if src in df.columns:
            resolved[dst] = src

    for dst, aliases in spec.column_aliases.items():
        if dst in resolved:
            continue
        for alias in aliases:
            if alias in df.columns:
                resolved[dst] = alias
                break

    missing_required = [c for c in spec.required_columns if c not in resolved]
    if missing_required:
        raise MissingRequiredColumnError(
            f"缺少必需列：{'、'.join(missing_required)}",
            detail={
                "missing_columns": missing_required,
                "file_columns": list(df.columns),
            },
        )
    return resolved


# ---------------------------------------------------------------------------
# 类型转换（列级向量化）
# ---------------------------------------------------------------------------


def _strip_chars(series: pd.Series, chars: list[str] | None) -> pd.Series:
    """按 strip_chars 列表逐个删除指定字符。"""
    s = series.astype("string")
    for ch in chars or []:
        s = s.str.replace(ch, "", regex=False)
    return s


def _empty_mask(s: pd.Series) -> pd.Series:
    """空串 / '-' / '--' / NA 的布尔掩码（视为 default）。"""
    return s.isna() | s.isin(list(_EMPTY_PLACEHOLDERS))


def _iter_failed(failed: pd.Series, row_nos: pd.Series):
    """产出失败行的 (文件行号, 位置索引)。"""
    for i in np.flatnonzero(failed.to_numpy()):
        yield int(row_nos.iloc[i]), i


def _transform_money(series: pd.Series, t: TransformSpec, col: str,
                     errors: list[RowError], row_nos: pd.Series) -> pd.Series:
    """money：去 strip_chars → 空占位符取 default → Decimal；unit=fen 自动 /100。"""
    s = _strip_chars(series, t.strip_chars)
    empty = _empty_mask(s)

    numeric = pd.to_numeric(s.where(~empty), errors="coerce")
    failed = numeric.isna() & ~empty
    for row_no, i in _iter_failed(failed, row_nos):
        errors.append(
            RowError(row_no, "number_parse_failed",
                     f"列“{col}”的值“{series.iloc[i]}”无法解析为金额")
        )

    default_val = None if t.default is None else Decimal(str(t.default))
    div = Decimal("100") if t.unit == "fen" else Decimal("1")

    def _conv(x):
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default_val
        return (Decimal(str(x)) / div).quantize(_TWO_PLACES)

    return numeric.map(_conv)


def _transform_integer(series: pd.Series, t: TransformSpec, col: str,
                       errors: list[RowError], row_nos: pd.Series) -> pd.Series:
    """integer：去 strip_chars → default → ROUND_HALF_UP 四舍五入取整。"""
    s = _strip_chars(series, t.strip_chars)
    empty = _empty_mask(s)

    numeric = pd.to_numeric(s.where(~empty), errors="coerce")
    failed = numeric.isna() & ~empty
    for row_no, i in _iter_failed(failed, row_nos):
        errors.append(
            RowError(row_no, "number_parse_failed",
                     f"列“{col}”的值“{series.iloc[i]}”无法解析为整数")
        )

    default_val = None if t.default is None else int(Decimal(str(t.default)))

    def _conv(x):
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default_val
        # 四舍五入而非截断（规划 6.2：ROUND_HALF_UP）
        return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return numeric.map(_conv)


def _transform_datetime(series: pd.Series, t: TransformSpec, col: str,
                        errors: list[RowError], row_nos: pd.Series) -> pd.Series:
    """datetime：按 formats 顺序尝试 + Excel 序列日期兜底 + 按天截断。"""
    raw = series.astype("string")
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    for fmt in t.formats or []:
        todo = parsed.isna()
        if not todo.any():
            break
        conv = pd.to_datetime(raw.where(todo), format=fmt, errors="coerce")
        parsed = parsed.fillna(conv)

    # Excel 序列日期（如 45358）
    todo = parsed.isna()
    if todo.any():
        serial_like = raw.where(todo).str.match(_EXCEL_SERIAL_RE, na=False)
        if serial_like.any():
            serials = pd.to_numeric(raw.where(todo & serial_like), errors="coerce")
            conv = pd.to_datetime(serials, unit="D", origin=_EXCEL_EPOCH, errors="coerce")
            parsed = parsed.fillna(conv)

    failed = parsed.isna() & raw.notna() & (raw != "")
    for row_no, i in _iter_failed(failed, row_nos):
        errors.append(
            RowError(row_no, "date_parse_failed",
                     f"列“{col}”的值“{series.iloc[i]}”无法按任何格式解析为日期")
        )

    if t.truncate_to == "day":
        parsed = parsed.dt.normalize()
    return parsed


def _transform_string(series: pd.Series, t: TransformSpec | None) -> pd.Series:
    """string：去 strip_chars + trim；空串归一为 None。"""
    out = _strip_chars(series, t.strip_chars if t else None)
    out = out.str.strip()
    return out.replace("", None)


# ---------------------------------------------------------------------------
# 行过滤（规划 5.3 的 13 种操作符）
# ---------------------------------------------------------------------------


def _eval_filter_op(target: pd.Series, f: RowFilter) -> pd.Series:
    """单个过滤操作符的向量化求值，返回"保留"布尔掩码（NA 一律视为不通过）。"""
    op = f.op
    s = target

    if op == "not_null":
        cond = s.notna() & (s.astype("string").fillna("").str.strip() != "")
    elif op == "is_null":
        cond = s.isna() | (s.astype("string").fillna("").str.strip() == "")
    elif op == "numeric":
        cond = pd.to_numeric(s, errors="coerce").notna()
    elif op in ("in", "not_in"):
        vals = [str(v) for v in (f.values or [])]
        hit = s.astype("string").isin(vals)
        cond = hit if op == "in" else ~hit.fillna(False)
    elif op in ("contains", "not_contains"):
        pattern = "|".join(re.escape(str(v)) for v in (f.values or []))
        hit = s.astype("string").str.contains(pattern, na=False, regex=True)
        cond = hit if op == "contains" else ~hit
    else:
        # gt / gte / lt / lte / eq / neq：优先数值比较，目标列不可数值化时退化为字符串比较
        num = pd.to_numeric(s, errors="coerce")
        threshold = pd.to_numeric(pd.Series([f.value]), errors="coerce").iloc[0]
        if pd.notna(threshold):
            cmp_series, cmp_value = num, float(threshold)
        else:
            cmp_series, cmp_value = s.astype("string"), str(f.value)

        if op == "gt":
            cond = cmp_series > cmp_value
        elif op == "gte":
            cond = cmp_series >= cmp_value
        elif op == "lt":
            cond = cmp_series < cmp_value
        elif op == "lte":
            cond = cmp_series <= cmp_value
        elif op == "eq":
            cond = cmp_series == cmp_value
        else:  # neq：NA 与任何值比较为 NA，此处视为"不等于"（保留）
            cond = (cmp_series != cmp_value) | cmp_series.isna()

    return cond.fillna(False).astype(bool)


def _apply_filters(spec: AdapterSpec, out: pd.DataFrame, raw_df: pd.DataFrame,
                   resolved: dict[str, str], errors: list[RowError]) -> pd.DataFrame:
    """应用 row_filters（AND 语义，通过才保留）。

    过滤列可以是统一字段（out 中），也可以是源表头名（raw_df 中）；
    被过滤行记 ``filtered_out``，不算失败。
    """
    keep = pd.Series(True, index=out.index)

    for f in spec.row_filters:
        if f.column in out.columns:
            target = out[f.column]
        elif f.column in resolved and resolved[f.column] in raw_df.columns:
            target = raw_df[resolved[f.column]]
        elif f.column in raw_df.columns:
            target = raw_df[f.column]
        else:
            # 列完全不存在：视为全空，仅 is_null 能通过
            target = pd.Series(pd.NA, index=out.index, dtype="string")

        # target 可能取自 raw_df（含已因转换失败被剔除的行），对齐到 out 的行
        target = target.reindex(out.index)
        cond = _eval_filter_op(target, f)
        newly_dropped = keep & ~cond
        for i in out.index[newly_dropped]:
            errors.append(
                RowError(
                    row_number=int(out.at[i, "_row_no"]),
                    error_type="filtered_out",
                    error_message=f"被行过滤规则命中并剔除（{f.column} {f.op}）",
                )
            )
        keep = keep & cond

    return out[keep]


# ---------------------------------------------------------------------------
# 去重（规划 6.2 第 5 步）
# ---------------------------------------------------------------------------


def _blank_to_na(s: pd.Series) -> pd.Series:
    """把 NA 与空字符串统一置为 pd.NA，保留原值类型。

    配合 ``groupby.first()``（自动跳过 NA）即可向量化地取到组内首个
    非空值，无需逐组回调 Python 函数。
    """
    nonempty = s.notna() & (s.astype("string").fillna("") != "")
    return s.where(nonempty, pd.NA)


def _is_numeric_object_column(s: pd.Series) -> bool:
    """object 列是否可数值化（用于 sum 策略选择求和列）。"""
    vals = s.dropna()
    if not len(vals):
        return False
    try:
        pd.to_numeric(vals)
        return True
    except (ValueError, TypeError):
        return False


def _apply_dedup(spec: AdapterSpec, out: pd.DataFrame,
                 errors: list[RowError]) -> pd.DataFrame:
    """按 dedup.keys 分组合并：sum / last / first 三种策略。

    被合并掉的行记 ``duplicate_row``（不算失败）。保留行的 _row_no
    取组内最小行号，保证原始快照可溯源。
    """
    d = spec.dedup
    if not d.enabled or not d.keys:
        return out

    keys = [k for k in d.keys if k in out.columns]
    if not keys:
        return out

    group_sizes = out.groupby(keys, dropna=False, sort=False)["_row_no"].transform("size")
    dup_mask = group_sizes > 1

    if d.strategy in ("first", "last"):
        keep_arg = "first" if d.strategy == "first" else "last"
        kept_idx = set(
            out[dup_mask].drop_duplicates(subset=keys, keep=keep_arg).index
        )
        for i in out.index[dup_mask]:
            if i not in kept_idx:
                errors.append(
                    RowError(
                        row_number=int(out.at[i, "_row_no"]),
                        error_type="duplicate_row",
                        error_message=f"重复行，按策略 {d.strategy} 保留另一条",
                    )
                )
        return out.drop_duplicates(subset=keys, keep=keep_arg)

    # sum 策略：可数值化列求和，其余列取首个非空
    value_cols = [c for c in out.columns if c not in (*keys, "_row_no")]
    numeric_cols = [c for c in value_cols if _is_numeric_object_column(out[c])]
    other_cols = [c for c in value_cols if c not in numeric_cols]

    # 将非数值列的空值统一为 NA，以便用内置 "first" 聚合（跳过 NA）。
    # 旧实现对每个非数值列传入 Python 函数 _first_nonempty，会退化为
    # 「组数 × 列数」次逐组调用并反复构造 Series；当组数达万级时
    # （如每行 sku-day 唯一）开销占总耗时 80% 以上。
    agg_frame = out
    if other_cols:
        agg_frame = out.copy()
        for c in other_cols:
            agg_frame[c] = _blank_to_na(agg_frame[c])

    grouped = agg_frame.groupby(keys, dropna=False, sort=False)
    agg_map: dict[str, Any] = {c: "sum" for c in numeric_cols}
    agg_map.update({c: "first" for c in other_cols})
    agg_map["_row_no"] = "min"

    merged = grouped.agg(agg_map).reset_index()

    # 每个重复组中被合并掉的行（除保留行外）记 duplicate_row
    kept_row_nos = set(merged["_row_no"].tolist())
    for i in out.index[dup_mask]:
        if int(out.at[i, "_row_no"]) not in kept_row_nos:
            errors.append(
                RowError(
                    row_number=int(out.at[i, "_row_no"]),
                    error_type="duplicate_row",
                    error_message=f"重复行，已按 sum 策略合并（keys={keys}）",
                )
            )
    return merged


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def normalize(df: pd.DataFrame, spec: AdapterSpec, header_row_index: int = 1) -> NormalizeResult:
    """主入口：原始 DataFrame → NormalizeResult。

    ``header_row_index`` 为表头行号（1-based），用于把数据行号换算成
    文件内行号。
    """
    resolved = _resolve_columns(spec, df)
    errors: list[RowError] = []

    out = pd.DataFrame(index=df.index)
    # 文件内行号：表头行 + 数据行偏移（1-based）
    out["_row_no"] = header_row_index + np.arange(1, len(df) + 1)

    for unified, src in resolved.items():
        out[unified] = df[src]
    out["platform_key"] = spec.platform_key

    # ---- 2) 类型转换 ----
    for unified in spec.column_map.values():
        if unified not in out.columns:
            continue
        t = spec.transforms.get(unified)
        if t is None:
            out[unified] = _transform_string(out[unified], None)
        elif t.type == "money":
            out[unified] = _transform_money(out[unified], t, unified, errors, out["_row_no"])
        elif t.type == "integer":
            out[unified] = _transform_integer(out[unified], t, unified, errors, out["_row_no"])
        elif t.type == "datetime":
            out[unified] = _transform_datetime(out[unified], t, unified, errors, out["_row_no"])
        elif t.type == "string":
            out[unified] = _transform_string(out[unified], t)

    # 转换失败的行先剔除，不参与后续过滤与去重
    failed_row_nos = {e.row_number for e in errors
                      if e.error_type in ("number_parse_failed", "date_parse_failed")}
    if failed_row_nos:
        out = out[~out["_row_no"].isin(failed_row_nos)]

    # ---- 3) shop_code 兜底 ----
    sc = out["shop_code"] if "shop_code" in out.columns else pd.Series(pd.NA, index=out.index, dtype="string")
    sn = out["shop_name"] if "shop_name" in out.columns else pd.Series(pd.NA, index=out.index, dtype="string")
    sc = sc.astype("string").str.strip()
    sn = sn.astype("string").str.strip()
    filled = sc.where(sc.notna() & (sc != ""), sn)
    filled = filled.where(filled.notna() & (filled != ""), "__UNKNOWN__")
    out["shop_code"] = filled

    # ---- 4) 行过滤 ----
    out = _apply_filters(spec, out, df, resolved, errors)

    # ---- 5) 去重 ----
    out = _apply_dedup(spec, out, errors)

    # ---- 6) 口径换算 + 组装 NormalizedRow ----
    rows: list[NormalizedRow] = []
    raw_records = df.to_dict("records")  # 批量生成原始快照底料

    field_names = (*_UNIFIED_STRING_FIELDS, *_UNIFIED_INT_FIELDS, *_UNIFIED_MONEY_FIELDS)
    # 一次性转 dict 后遍历：iterrows 每行都会新建 Series，取字段要经索引
    # 查找（每行约 28 次 get_loc），万级行时占主循环耗时九成以上。
    for r in out.to_dict("records"):
        row_dict = {k: r.get(k) for k in (*field_names, "stat_date")}

        try:
            if _to_dec_none(row_dict.get("gmv")) is None:
                # gmv 为 NULL → 整行判为无效（规划 5.2）
                errors.append(RowError(int(r["_row_no"]), "number_parse_failed",
                                       "必需列 gmv 缺失或无法解析，整行判为无效"))
                continue
            if pd.isna(r.get("stat_date")):
                errors.append(RowError(int(r["_row_no"]), "date_parse_failed",
                                       "必需列 stat_date 缺失或无法解析，整行判为无效"))
                continue

            net_amount = compute_net_amount(spec, row_dict)

            # _row_no = header_row_index + (数据行序号 + 1)，故减 1 还原为
            # raw_records 的 0-based 下标，保证快照与当前行严格对应（可溯源）
            raw_idx = int(r["_row_no"]) - header_row_index - 1
            raw = raw_records[raw_idx] if 0 <= raw_idx < len(raw_records) else {}

            stat_date = r["stat_date"]
            if isinstance(stat_date, (datetime, pd.Timestamp)):
                stat_date = stat_date.date()
            elif isinstance(stat_date, str):
                stat_date = datetime.strptime(stat_date, "%Y-%m-%d").date()

            rows.append(
                NormalizedRow(
                    stat_date=stat_date,
                    platform_key=spec.platform_key,
                    shop_code=_none_if_na(r.get("shop_code")),
                    shop_name=_none_if_na(r.get("shop_name")),
                    platform_product_code=_none_if_na(r.get("platform_product_code")) or "",
                    product_name=_none_if_na(r.get("product_name")),
                    category=_none_if_na(r.get("category")),
                    order_qty=_to_int_none(r.get("order_qty")),
                    paid_qty=_to_int_none(r.get("paid_qty")),
                    gmv=_to_dec_none(r["gmv"]),
                    refund_amount=_to_dec_none(r.get("refund_amount")),
                    net_amount=Decimal(net_amount).quantize(_TWO_PLACES),
                    visitors=_to_int_none(r.get("visitors")),
                    buyers=_to_int_none(r.get("buyers")),
                    ad_cost=_to_dec_none(r.get("ad_cost")),
                    raw_row_json={str(k): _json_safe(v) for k, v in raw.items()},
                )
            )
        except Exception as e:  # 公式运行时错误（除零）/ 未配置 transform 的字段含脏值，
            # 一律按行级失败处理，绝不因单行脏数据让整个文件 500
            errors.append(RowError(int(r["_row_no"]), "number_parse_failed",
                                   f"行数据组装或 net_amount 计算失败：{e}"))
            continue

    total = len(df)
    filtered = sum(1 for e in errors if e.error_type == "filtered_out")
    duplicated = sum(1 for e in errors if e.error_type == "duplicate_row")
    failed = sum(1 for e in errors
                 if e.error_type in ("number_parse_failed", "date_parse_failed"))

    # 回填行级错误的原始行内容（前端「这一行原始内容是什么」排查用）。
    # 各 RowError 构造点散落在转换/过滤/去重阶段，统一在出口按行号
    # 反查 raw_records 快照，避免每个构造点重复换算下标。
    if errors:
        for e in errors:
            if e.raw_content is not None:
                continue
            raw_idx = e.row_number - header_row_index - 1
            if 0 <= raw_idx < len(raw_records):
                snapshot = {
                    str(k): _json_safe(v) for k, v in raw_records[raw_idx].items()
                }
                text = json.dumps(snapshot, ensure_ascii=False)
                # 防止异常宽列把单条错误撑爆（DB TEXT 无限制，但前端展示需要收敛）
                e.raw_content = text if len(text) <= 2000 else text[:2000] + "…"

    from backend.app.schemas.adapter import build_semantics_note

    return NormalizeResult(
        rows=rows,
        errors=errors,
        stats={
            "total": total,
            "valid": len(rows),
            "filtered": filtered,
            "duplicated": duplicated,
            "failed": failed,
        },
        semantics_note=build_semantics_note(spec),
    )


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _none_if_na(v) -> str | None:
    """NA / 空串 → None；其余转 strip 后的字符串。"""
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return str(v)
    s = str(v).strip()
    return s if s else None


# 未配置 transform 的数值字段兜底清洗：千分位 / 货币符号 / 单位字
_LOOSE_STRIP_RE = re.compile(r"[,，￥¥元\s]")


def _to_int_none(v) -> int | None:
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    s = _LOOSE_STRIP_RE.sub("", str(v).strip())
    if s in _EMPTY_PLACEHOLDERS:
        return None
    return int(Decimal(s).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_dec_none(v) -> Decimal | None:
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, Decimal):
        return v
    s = _LOOSE_STRIP_RE.sub("", str(v).strip())
    if s in _EMPTY_PLACEHOLDERS:
        return None
    return Decimal(s).quantize(_TWO_PLACES)


def _json_safe(v):
    """原始快照：NaN → None，其余转字符串（保持"原始表头 → 原始字符串值"）。"""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return str(v)
