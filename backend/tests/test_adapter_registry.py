"""适配器注册中心与 YAML 校验测试（T02 交叉校验 + T05 识别）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.core import AdapterNotFoundError
from backend.app.core.adapter_registry import (
    detect_platform,
    load_builtin,
    list_builtin_keys,
    validate_yaml_text,
)
from backend.app.schemas.adapter import AdapterSpec, build_semantics_note

BUILTIN_KEYS = ["taobao", "doudian", "pinduoduo", "jd"]


def _builtin_text(key: str) -> str:
    return load_builtin(key).yaml_text


def _base_spec_dict(**overrides) -> dict:
    """构造一个合法的最小适配器定义，供反例测试覆盖字段。"""
    d = {
        "adapter_version": 1,
        "platform_key": "test_platform",
        "display_name": "测试平台",
        "file_matching": {
            "encoding_candidates": ["utf-8"],
            "header_row": "auto",
            "auto_header_hints": ["商品ID", "支付金额"],
            "detect_keywords": ["测试"],
        },
        "column_map": {
            "统计时间": "stat_date",
            "商品ID": "platform_product_code",
            "支付金额": "gmv",
        },
        "required_columns": ["stat_date", "platform_product_code", "gmv"],
        "metric_semantics": {
            "gmv_basis": "paid",
            "gmv_includes_shipping": False,
            "gmv_includes_tax": False,
            "refund_handling": "separate",
            "net_amount_formula": "gmv - refund_amount",
            "timezone": "Asia/Shanghai",
            "grain": "sku_day",
        },
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# T02：交叉校验规则（每条规则 1 个反例）
# ---------------------------------------------------------------------------


def _assert_rejected_with(spec_dict: dict, keyword: str) -> None:
    """断言非法配置被拒绝且错误信息指明具体字段。"""
    with pytest.raises(ValidationError) as exc_info:
        AdapterSpec(**spec_dict)
    msgs = str(exc_info.value)
    assert keyword in msgs, f"错误信息应包含 {keyword!r}，实际：{msgs}"


def test_cross_check_column_map_value_whitelist():
    d = _base_spec_dict(column_map={"统计时间": "stat_date", "商品ID": "not_a_field",
                                    "支付金额": "gmv"})
    _assert_rejected_with(d, "not_a_field")


def test_cross_check_required_columns_subset():
    d = _base_spec_dict(required_columns=["stat_date", "platform_product_code", "gmv",
                                          "nonexistent_col"])
    _assert_rejected_with(d, "nonexistent_col")


def test_cross_check_transforms_keys():
    d = _base_spec_dict(
        transforms={"bad_field": {"type": "money", "unit": "yuan", "default": "0"}}
    )
    _assert_rejected_with(d, "bad_field")


def test_cross_check_formula_fields():
    d = _base_spec_dict()
    d["metric_semantics"]["net_amount_formula"] = "gmv - secret_field * 2"
    _assert_rejected_with(d, "secret_field")


def test_cross_check_formula_rejects_call_syntax():
    d = _base_spec_dict()
    d["metric_semantics"]["net_amount_formula"] = "__import__('os').system('ls')"
    # __import__ 会在字段白名单检查处被拒（Call 节点也会被求值器拒绝），两处拦截任一生效即可
    _assert_rejected_with(d, "__import__")


def test_cross_check_dedup_keys():
    d = _base_spec_dict(dedup={"enabled": True, "keys": ["stat_date", "hacker"], "strategy": "sum"})
    _assert_rejected_with(d, "hacker")


def test_cross_check_row_filter_column():
    d = _base_spec_dict(row_filters=[{"column": "bad_column", "op": "not_null"}])
    _assert_rejected_with(d, "bad_column")


def test_cross_check_row_filter_column_allows_source_header():
    """row_filters 的 column 允许是源表头名（column_map 键）。"""
    d = _base_spec_dict(row_filters=[{"column": "商品ID", "op": "not_null"}])
    AdapterSpec(**d)  # 不应抛异常


def test_cross_check_duplicate_mapping_rejected():
    d = _base_spec_dict(
        column_map={"统计时间": "stat_date", "日期": "stat_date", "商品ID": "platform_product_code",
                    "支付金额": "gmv"}
    )
    _assert_rejected_with(d, "重复映射")


# ---------------------------------------------------------------------------
# T05：4 个内置 YAML 全部通过校验 + 语义声明
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", BUILTIN_KEYS)
def test_builtin_yaml_valid(key):
    spec = AdapterSpec(**__import__("yaml").safe_load(_builtin_text(key)))
    assert spec.platform_key == key
    assert spec.required_columns == ["stat_date", "platform_product_code", "gmv"]


def test_jd_semantics():
    """京东：下单口径 + 含运费 + 不可直接比较提示。"""
    spec = AdapterSpec(**__import__("yaml").safe_load(_builtin_text("jd")))
    assert spec.metric_semantics.gmv_basis == "ordered"
    assert spec.metric_semantics.gmv_includes_shipping is True
    note = build_semantics_note(spec)
    assert "含运费" in note
    assert "不可直接相加比较" in note


@pytest.mark.parametrize("key", ["taobao", "doudian", "pinduoduo"])
def test_other_platforms_paid_basis(key):
    spec = AdapterSpec(**__import__("yaml").safe_load(_builtin_text(key)))
    assert spec.metric_semantics.gmv_basis == "paid"
    assert spec.metric_semantics.gmv_includes_shipping is False
    assert spec.metric_semantics.net_amount_formula == "gmv - refund_amount"


# ---------------------------------------------------------------------------
# T05：平台自动识别
# ---------------------------------------------------------------------------


def test_detect_taobao():
    cols = ["统计日期", "商品ID", "商品名称", "支付金额", "商品访客数", "支付买家数"]
    candidates = detect_platform(cols, filename="生意参谋_商品效果_20260906.csv")
    assert candidates[0]["platform_key"] == "taobao"
    assert candidates[0]["score"] > candidates[1]["score"]


def test_detect_doudian():
    cols = ["统计时间", "商品ID", "商品名称", "支付金额", "商品访客数", "支付人数"]
    candidates = detect_platform(cols, filename="抖店罗盘_商品分析_2026-09-06.csv")
    assert candidates[0]["platform_key"] == "doudian"


def test_detect_pinduoduo():
    cols = ["统计日期", "商品ID", "商品名称", "成团金额(GMV)", "成团件数", "成团买家数"]
    candidates = detect_platform(cols, filename="拼多多_商品数据_20260906.xlsx")
    assert candidates[0]["platform_key"] == "pinduoduo"


def test_detect_jd():
    cols = ["日期", "商品编号", "商品名称", "下单金额", "付款件数", "访客数"]
    candidates = detect_platform(cols, filename="京东商智_商品分析_20260906.csv")
    assert candidates[0]["platform_key"] == "jd"


def test_detect_no_match():
    candidates = detect_platform(["col_a", "col_b"], filename="unknown.csv")
    assert all(c["score"] == 0 for c in candidates)


# ---------------------------------------------------------------------------
# validate_yaml_text（编辑器校验，不落库）
# ---------------------------------------------------------------------------


def test_validate_yaml_text_ok():
    result = validate_yaml_text(_builtin_text("doudian"))
    assert result.valid is True
    assert result.errors == []


def test_validate_yaml_text_syntax_error():
    result = validate_yaml_text("column_map: [unclosed")
    assert result.valid is False
    assert "YAML 语法错误" in result.errors[0]


def test_validate_yaml_text_semantic_error():
    result = validate_yaml_text(_builtin_text("doudian").replace("支付金额\": gmv", "支付金额\": wrong"))
    assert result.valid is False
    assert any("wrong" in e for e in result.errors)


def test_load_builtin_missing():
    with pytest.raises(AdapterNotFoundError):
        load_builtin("nonexistent")


def test_list_builtin_keys():
    assert set(BUILTIN_KEYS).issubset(set(list_builtin_keys()))


# ---------------------------------------------------------------------------
# 补充分支覆盖：覆盖配置 / 重置 / 自定义平台 / 校验提示
# ---------------------------------------------------------------------------


def test_save_override_and_get_adapter_override(db_session):
    from backend.app.core.adapter_registry import get_adapter, save_override

    yaml_text = _builtin_text("doudian")
    bundle = save_override(db_session, "doudian", yaml_text)
    assert bundle.source == "override"
    assert bundle.version == 2

    got = get_adapter(db_session, "doudian")
    assert got.source == "override"


def test_save_override_platform_mismatch(db_session):
    from backend.app.core import AdapterConfigError
    from backend.app.core.adapter_registry import save_override

    with pytest.raises(AdapterConfigError):
        save_override(db_session, "taobao", _builtin_text("doudian"))


def test_get_all_adapters_includes_custom_platform(db_session):
    from backend.app.core.adapter_registry import get_all_adapters, save_override

    custom_yaml = _builtin_text("doudian").replace(
        "platform_key: doudian", "platform_key: my_custom"
    )
    save_override(db_session, "my_custom", custom_yaml)
    keys = {b.platform_key for b in get_all_adapters(db_session)}
    assert "my_custom" in keys


def test_reset_custom_platform_raises(db_session):
    from backend.app.core import AdapterNotFoundError
    from backend.app.core.adapter_registry import reset_to_builtin, save_override

    custom_yaml = _builtin_text("doudian").replace(
        "platform_key: doudian", "platform_key: my_custom2"
    )
    save_override(db_session, "my_custom2", custom_yaml)
    with pytest.raises(AdapterNotFoundError):
        reset_to_builtin(db_session, "my_custom2")


def test_reload_builtin_cache():
    from backend.app.core.adapter_registry import (
        _builtin_cache,
        load_builtin,
        reload_builtin_cache,
    )

    load_builtin("taobao")
    assert "taobao" in _builtin_cache
    reload_builtin_cache()
    assert "taobao" not in _builtin_cache


def test_validate_yaml_warnings(monkeypatch):
    d = _base_spec_dict()
    d["file_matching"]["detect_keywords"] = []
    d.pop("transforms", None)
    d["metric_semantics"]["gmv_includes_shipping"] = True
    d["metric_semantics"]["refund_handling"] = "none"
    import yaml as yaml_mod

    result = validate_yaml_text(yaml_mod.safe_dump(d, allow_unicode=True))
    assert result.valid is True
    assert len(result.warnings) >= 4


def test_validate_yaml_not_a_dict():
    result = validate_yaml_text("- just\n- a\n- list\n")
    assert result.valid is False
    assert "键值映射" in result.errors[0]
