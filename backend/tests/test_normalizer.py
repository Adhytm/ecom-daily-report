"""归一化引擎测试（T04）：money/datetime/integer 转换、过滤、去重、公式、性能。"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from backend.app.core import MissingRequiredColumnError
from backend.app.core.normalizer import normalize
from backend.app.schemas.adapter import AdapterSpec


def _spec(**overrides) -> AdapterSpec:
    """构造一个面向测试的适配器定义（表头风格贴近抖店）。"""
    d = dict(
        adapter_version=1,
        platform_key="testp",
        display_name="测试平台",
        file_matching={
            "encoding_candidates": ["utf-8"],
            "header_row": "auto",
            "auto_header_hints": ["商品ID", "支付金额"],
            "detect_keywords": ["测试"],
        },
        column_map={
            "统计时间": "stat_date",
            "店铺名称": "shop_name",
            "商品ID": "platform_product_code",
            "商品名称": "product_name",
            "一级类目": "category",
            "支付金额": "gmv",
            "支付件数": "paid_qty",
            "退款金额": "refund_amount",
            "访客数": "visitors",
            "支付人数": "buyers",
            "推广消耗": "ad_cost",
        },
        column_aliases={"gmv": ["成交金额"]},
        required_columns=["stat_date", "platform_product_code", "gmv"],
        optional_columns=["shop_name", "product_name", "category", "paid_qty",
                          "refund_amount", "visitors", "buyers", "ad_cost", "order_qty"],
        transforms={
            "stat_date": {"type": "datetime",
                          "formats": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"],
                          "truncate_to": "day"},
            "gmv": {"type": "money", "strip_chars": [",", "￥", "¥", " ", "\t", "元"],
                    "unit": "yuan", "default": "0"},
            "refund_amount": {"type": "money", "strip_chars": [",", "￥", "¥", " "],
                              "unit": "yuan", "default": "0"},
            "paid_qty": {"type": "integer", "strip_chars": [",", "件"], "default": "0"},
            "visitors": {"type": "integer", "strip_chars": [","], "default": None},
            "platform_product_code": {"type": "string", "strip_chars": [" ", "\t", "'"], "trim": True},
        },
        metric_semantics={
            "gmv_basis": "paid",
            "gmv_includes_shipping": False,
            "gmv_includes_tax": False,
            "refund_handling": "separate",
            "net_amount_formula": "gmv - refund_amount",
            "timezone": "Asia/Shanghai",
            "grain": "sku_day",
        },
        row_filters=[{"column": "product_name", "op": "not_contains", "values": ["测试", "赠品"]}],
        dedup={"enabled": True, "keys": ["stat_date", "platform_key", "platform_product_code"],
               "strategy": "sum"},
    )
    d.update(overrides)
    return AdapterSpec(**d)


def _df(rows: list[dict], columns: list[str] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows, dtype=object)
    if columns:
        df = df[columns]
    return df


# ---------------------------------------------------------------------------
# money 转换
# ---------------------------------------------------------------------------


def test_money_variants():
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1,234.56"},   # 千分位
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "￥1234"},     # 货币符号
        {"统计时间": "2026-09-06", "商品ID": "C", "支付金额": "-"},          # 占位符 → default
        {"统计时间": "2026-09-06", "商品ID": "D", "支付金额": "--"},
        {"统计时间": "2026-09-06", "商品ID": "E", "支付金额": ""},
        {"统计时间": "2026-09-06", "商品ID": "F", "支付金额": "0.00"},
        {"统计时间": "2026-09-06", "商品ID": "G", "支付金额": "-12.5"},      # 负数
        {"统计时间": "2026-09-06", "商品ID": "H", "支付金额": "1.23e3"},     # 科学计数法
        {"统计时间": "2026-09-06", "商品ID": "I", "支付金额": "99元"},       # 带单位
    ])
    result = normalize(df, spec)
    by_code = {r.platform_product_code: r.gmv for r in result.rows}
    assert by_code["A"] == Decimal("1234.56")
    assert by_code["B"] == Decimal("1234.00")
    assert by_code["C"] == Decimal("0")
    assert by_code["D"] == Decimal("0")
    assert by_code["E"] == Decimal("0")
    assert by_code["F"] == Decimal("0.00")
    assert by_code["G"] == Decimal("-12.50")
    assert by_code["H"] == Decimal("1230.00")
    assert by_code["I"] == Decimal("99.00")


def test_money_unit_fen():
    spec = _spec(transforms={
        "stat_date": {"type": "datetime", "formats": ["%Y-%m-%d"], "truncate_to": "day"},
        "gmv": {"type": "money", "strip_chars": [], "unit": "fen", "default": "0"},
    })
    df = _df([{"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "12345"}])
    result = normalize(df, spec)
    assert result.rows[0].gmv == Decimal("123.45")


def test_money_parse_failure_row_error():
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "abc"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "10"},
    ])
    result = normalize(df, spec)
    assert result.stats["failed"] == 1
    assert result.stats["valid"] == 1
    assert result.errors[0].error_type == "number_parse_failed"
    assert result.errors[0].row_number == 2  # 文件行号（表头第 1 行）


# ---------------------------------------------------------------------------
# datetime 转换
# ---------------------------------------------------------------------------


def test_datetime_formats_and_serial():
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06 13:24:35", "商品ID": "A", "支付金额": "1"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1"},
        {"统计时间": "2026/09/06", "商品ID": "C", "支付金额": "1"},
        {"统计时间": "20260906", "商品ID": "D", "支付金额": "1"},
        {"统计时间": "45358", "商品ID": "E", "支付金额": "1"},  # Excel 序列日期
        {"统计时间": "not-a-date", "商品ID": "F", "支付金额": "1"},  # 非法
    ])
    result = normalize(df, spec)
    dates = {r.platform_product_code: r.stat_date for r in result.rows}
    assert dates["A"].isoformat() == "2026-09-06"
    assert dates["B"].isoformat() == "2026-09-06"
    assert dates["C"].isoformat() == "2026-09-06"
    assert dates["D"].isoformat() == "2026-09-06"
    assert dates["E"].isoformat() == "2024-03-07"  # 45358 → 2024-03-07
    assert "F" not in dates
    assert result.errors[-1].error_type == "date_parse_failed"


def test_datetime_truncate_to_day():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06 23:59:59", "商品ID": "A", "支付金额": "1"}])
    result = normalize(df, spec)
    assert result.rows[0].stat_date.day == 6


# ---------------------------------------------------------------------------
# integer 转换（ROUND_HALF_UP）
# ---------------------------------------------------------------------------


def test_integer_round_half_up():
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1", "访客数": "1,234", "支付件数": "12件"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1", "访客数": "12.6"},   # → 13
        {"统计时间": "2026-09-06", "商品ID": "C", "支付金额": "1", "访客数": "12.4"},   # → 12
        {"统计时间": "2026-09-06", "商品ID": "D", "支付金额": "1", "访客数": "-12.5"},  # → -13
    ])
    result = normalize(df, spec)
    v = {r.platform_product_code: r.visitors for r in result.rows}
    q = {r.platform_product_code: r.paid_qty for r in result.rows}
    assert v["A"] == 1234
    assert q["A"] == 12  # “件”字由 paid_qty 的 strip_chars 剥离
    assert v["B"] == 13
    assert v["C"] == 12
    assert v["D"] == -13


# ---------------------------------------------------------------------------
# 列映射
# ---------------------------------------------------------------------------


def test_required_column_missing_error_detail():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06", "支付金额": "1"}])
    with pytest.raises(MissingRequiredColumnError) as exc_info:
        normalize(df, spec)
    detail = exc_info.value.detail
    assert "platform_product_code" in detail["missing_columns"]
    assert "统计时间" in detail["file_columns"]


def test_column_alias_fallback():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06", "商品ID": "A", "成交金额": "88.5"}])
    result = normalize(df, spec)
    assert result.rows[0].gmv == Decimal("88.50")


def test_shop_code_fallback():
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1", "店铺名称": "官方旗舰店"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1"},
    ])
    result = normalize(df, spec)
    codes = {r.platform_product_code: r.shop_code for r in result.rows}
    assert codes["A"] == "官方旗舰店"   # 无 shop_code 用 shop_name 兜底
    assert codes["B"] == "__UNKNOWN__"  # 皆缺


# ---------------------------------------------------------------------------
# row_filters（13 种操作符）
# ---------------------------------------------------------------------------


def _filter_spec(filters: list[dict]) -> AdapterSpec:
    return _spec(row_filters=filters)


def test_filter_not_contains():
    spec = _spec()  # 默认带 not_contains 测试/赠品
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1", "商品名称": "正常商品"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1", "商品名称": "测试商品勿拍"},
        {"统计时间": "2026-09-06", "商品ID": "C", "支付金额": "1", "商品名称": "店铺赠品"},
    ])
    result = normalize(df, spec)
    assert result.stats["valid"] == 1
    assert result.stats["filtered"] == 2
    assert {e.error_type for e in result.errors} == {"filtered_out"}


def test_filter_not_null_and_is_null():
    spec = _filter_spec([{"column": "category", "op": "not_null"}])
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1", "一级类目": "美妆"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1", "一级类目": ""},
    ])
    result = normalize(df, spec)
    assert result.rows[0].platform_product_code == "A"

    spec2 = _filter_spec([{"column": "一级类目", "op": "is_null"}])
    result2 = normalize(df, spec2)
    assert result2.rows[0].platform_product_code == "B"


def test_filter_numeric():
    spec = _filter_spec([{"column": "支付金额", "op": "numeric"}])
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "12.5"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "N/A"},
    ])
    result = normalize(df, spec)
    assert result.stats["valid"] == 1


def test_filter_in_not_in():
    spec = _filter_spec([{"column": "商品ID", "op": "in", "values": ["A", "C"]}])
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1"},
    ])
    assert normalize(df, spec).rows[0].platform_product_code == "A"

    spec2 = _filter_spec([{"column": "商品ID", "op": "not_in", "values": ["A"]}])
    assert normalize(df, spec2).rows[0].platform_product_code == "B"


def test_filter_contains():
    spec = _filter_spec([{"column": "商品名称", "op": "contains", "values": ["限定"]}])
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "1", "商品名称": "春季限定款"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "1", "商品名称": "普通款"},
    ])
    assert normalize(df, spec).stats["valid"] == 1


def test_filter_comparison_ops():
    rows = [
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "100"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "200"},
    ]
    df = _df(rows)
    for op, expect in [("gt", "B"), ("gte", "B"), ("lt", "A"), ("lte", "A")]:
        spec = _filter_spec([{"column": "支付金额", "op": op, "value": "150"}])
        assert normalize(df, spec).rows[0].platform_product_code == expect, op
    spec_eq = _filter_spec([{"column": "商品ID", "op": "eq", "value": "A"}])
    assert normalize(df, spec_eq).rows[0].platform_product_code == "A"
    spec_neq = _filter_spec([{"column": "商品ID", "op": "neq", "value": "A"}])
    assert normalize(df, spec_neq).rows[0].platform_product_code == "B"


# ---------------------------------------------------------------------------
# dedup 三种策略
# ---------------------------------------------------------------------------


def _dup_df() -> pd.DataFrame:
    return _df([
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "100", "访客数": "10", "商品名称": "甲"},
        {"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "50", "访客数": "5", "商品名称": "甲"},
        {"统计时间": "2026-09-06", "商品ID": "B", "支付金额": "30", "访客数": "3", "商品名称": "乙"},
    ])


def test_dedup_sum():
    result = normalize(_dup_df(), _spec())
    assert result.stats["duplicated"] == 1
    row_a = [r for r in result.rows if r.platform_product_code == "A"][0]
    assert row_a.gmv == Decimal("150.00")
    assert row_a.visitors == 15


def test_dedup_first_last():
    spec_first = _spec(dedup={"enabled": True,
                              "keys": ["stat_date", "platform_key", "platform_product_code"],
                              "strategy": "first"})
    result = normalize(_dup_df(), spec_first)
    assert result.stats["valid"] == 2
    row_a = [r for r in result.rows if r.platform_product_code == "A"][0]
    assert row_a.gmv == Decimal("100.00")

    spec_last = _spec(dedup={"enabled": True,
                             "keys": ["stat_date", "platform_key", "platform_product_code"],
                             "strategy": "last"})
    result_last = normalize(_dup_df(), spec_last)
    row_a = [r for r in result_last.rows if r.platform_product_code == "A"][0]
    assert row_a.gmv == Decimal("50.00")


def test_dedup_disabled_keeps_all():
    spec = _spec(dedup={"enabled": False, "keys": [], "strategy": "sum"})
    result = normalize(_dup_df(), spec)
    assert result.stats["valid"] == 3


# ---------------------------------------------------------------------------
# net_amount 公式
# ---------------------------------------------------------------------------


def test_net_amount_formula_computed():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "100.50",
               "退款金额": "30.25"}])
    result = normalize(df, spec)
    assert result.rows[0].net_amount == Decimal("70.25")


def test_net_amount_null_refund_as_zero():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "100"}])
    result = normalize(df, spec)
    assert result.rows[0].net_amount == Decimal("100.00")


def test_net_amount_injection_rejected_at_config():
    with pytest.raises(Exception):
        _spec(metric_semantics={
            "gmv_basis": "paid", "gmv_includes_shipping": False, "gmv_includes_tax": False,
            "refund_handling": "separate", "net_amount_formula": "__import__('os').getcwd()",
            "timezone": "Asia/Shanghai", "grain": "sku_day",
        })


# ---------------------------------------------------------------------------
# 原始快照
# ---------------------------------------------------------------------------


def test_raw_row_json_snapshot():
    spec = _spec()
    df = _df([{"统计时间": "2026-09-06", "商品ID": "A", "支付金额": "￥1,000",
               "商品名称": "商品甲", "多余列": "x"}])
    result = normalize(df, spec)
    raw = result.rows[0].raw_row_json
    assert raw["商品ID"] == "A"
    assert raw["支付金额"] == "￥1,000"
    assert raw["多余列"] == "x"
    assert set(raw.keys()) == {"统计时间", "商品ID", "支付金额", "商品名称", "多余列"}


# ---------------------------------------------------------------------------
# 性能（5 万行 10 秒内）
# ---------------------------------------------------------------------------


def test_performance_50k_rows():
    spec = _spec()
    n = 50000
    df = _df({
        "统计时间": ["2026-09-06"] * n,
        "商品ID": [f"P{i % 500:05d}" for i in range(n)],
        "商品名称": [f"商品{i % 500}" for i in range(n)],
        "支付金额": [f"{(i % 900) + 10}.5" for i in range(n)],
        "访客数": [str(i % 300) for i in range(n)],
    })
    start = time.perf_counter()
    result = normalize(df, spec)
    elapsed = time.perf_counter() - start
    assert result.stats["valid"] == 500  # 5 万行按 500 个商品去重合并
    assert elapsed < 10, f"5 万行归一化耗时 {elapsed:.2f}s，超过 10s"
