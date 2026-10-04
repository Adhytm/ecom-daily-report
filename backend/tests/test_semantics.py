"""口径换算与公式求值器测试（T04）：AST 白名单、防注入、NULL 处理。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core import AdapterConfigError
from backend.app.core.semantics import compute_net_amount, eval_formula


def test_basic_arithmetic():
    assert eval_formula("gmv - refund_amount", {"gmv": Decimal("100"), "refund_amount": Decimal("30")}) == Decimal("70")


def test_constants_and_parentheses():
    assert eval_formula("(gmv - refund_amount) * 0.9", {"gmv": Decimal("100"), "refund_amount": Decimal("10")}) == Decimal("81.0")


def test_null_field_treated_as_zero():
    assert eval_formula("gmv - refund_amount", {"gmv": Decimal("100"), "refund_amount": None}) == Decimal("100")


def test_formula_division():
    assert eval_formula("gmv / 2", {"gmv": Decimal("101")}) == Decimal("50.5")


def test_injection_rejected_import():
    with pytest.raises(AdapterConfigError):
        eval_formula("__import__('os').system('ls')", {"gmv": Decimal("1")})


def test_injection_rejected_attribute():
    with pytest.raises(AdapterConfigError):
        eval_formula("gmv.__class__", {"gmv": Decimal("1")})


def test_injection_rejected_call():
    with pytest.raises(AdapterConfigError):
        eval_formula("open('secret.txt')", {"gmv": Decimal("1")})


def test_injection_rejected_lambda_name():
    with pytest.raises(AdapterConfigError):
        eval_formula("os", {"gmv": Decimal("1")})


def test_syntax_error():
    with pytest.raises(AdapterConfigError):
        eval_formula("gmv -", {"gmv": Decimal("1")})


def test_compute_net_amount_row():
    spec_like_formula = "gmv - refund_amount"

    class Sem:
        net_amount_formula = spec_like_formula

    class Spec:
        metric_semantics = Sem()

    row = {"gmv": Decimal("200.50"), "refund_amount": Decimal("20.25"), "ad_cost": None}
    assert compute_net_amount(Spec(), row) == Decimal("180.25")


# ---------------------------------------------------------------------------
# 边缘分覆盖：非白名单节点 / 一元运算 / 字符串常量 / 口径提示
# ---------------------------------------------------------------------------


def test_unary_minus_allowed():
    assert eval_formula("-gmv + 10", {"gmv": Decimal("5")}) == Decimal("5")


def test_unsupported_binop_rejected():
    with pytest.raises(AdapterConfigError):
        eval_formula("gmv | 2", {"gmv": Decimal("1")})


def test_unsupported_unaryop_rejected():
    with pytest.raises(AdapterConfigError):
        eval_formula("~gmv", {"gmv": Decimal("1")})


def test_string_constant_rejected():
    with pytest.raises(AdapterConfigError):
        eval_formula("gmv + 'abc'", {"gmv": Decimal("1")})


def test_bool_constant_rejected():
    with pytest.raises(AdapterConfigError):
        eval_formula("gmv + True", {"gmv": Decimal("1")})


def test_compute_net_amount_refund_deducted_formula():
    class Sem:
        net_amount_formula = "gmv"

    class Spec:
        metric_semantics = Sem()
        display_name = "测试"

    # refund_handling=deducted 场景：公式仅引用 gmv，NULL 退款不影响
    assert compute_net_amount(Spec(), {"gmv": Decimal("88")}) == Decimal("88")


def test_semantics_warning_shipping():
    from backend.app.core.adapter_registry import load_builtin
    from backend.app.core.semantics import semantics_warning

    jd = load_builtin("jd").spec
    assert semantics_warning(jd) and "含运费" in semantics_warning(jd)
    tb = load_builtin("taobao").spec
    assert semantics_warning(tb) is None
