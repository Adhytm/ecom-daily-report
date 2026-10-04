"""口径换算（规划 5.2 / 6.2 第 3 步）。

核心是基于 ``ast.parse`` 的白名单公式求值器：仅接受 BinOp / UnaryOp /
Constant(数字) / Name(白名单字段)，**禁止 eval**，任何其他节点
（函数调用、属性访问、下标等）一律抛 AdapterConfigError。
"""

from __future__ import annotations

import ast
import operator
from decimal import Decimal

from backend.app.core import AdapterConfigError
from backend.app.schemas.adapter import NUMERIC_FIELDS, AdapterSpec

# 二元运算白名单
_BIN_OPS: dict[type, callable] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

# 一元运算白名单（正负号）
_UNARY_OPS: dict[type, callable] = {
    ast.UAdd: lambda x: +x,
    ast.USub: lambda x: -x,
}


def eval_formula(formula: str, fields: dict[str, Decimal | None]) -> Decimal:
    """按白名单求值 net_amount_formula。

    - 引用的字段为 NULL 时按 0 参与计算（gmv 为 NULL 的整行有效性由
      调用方在归一化层判定）
    - 除零抛 ZeroDivisionError，由归一化层按行级错误处理
    """
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise AdapterConfigError(f"net_amount_formula 语法非法: {formula!r}") from e

    return _eval_node(tree.body, formula, fields)


def _eval_node(node: ast.AST, formula: str, fields: dict[str, Decimal | None]) -> Decimal:
    """递归求值单个 AST 节点，非白名单节点直接拒绝。"""
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise AdapterConfigError(
                f"net_amount_formula 不支持运算符 {op_type.__name__}: {formula!r}"
            )
        left = _eval_node(node.left, formula, fields)
        right = _eval_node(node.right, formula, fields)
        return _BIN_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise AdapterConfigError(
                f"net_amount_formula 不支持一元运算符 {op_type.__name__}: {formula!r}"
            )
        return _UNARY_OPS[op_type](_eval_node(node.operand, formula, fields))

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        raise AdapterConfigError(
            f"net_amount_formula 只允许数字常量，遇到 {node.value!r}: {formula!r}"
        )

    if isinstance(node, ast.Name):
        if node.id not in NUMERIC_FIELDS:
            raise AdapterConfigError(
                f"net_amount_formula 引用了白名单外的字段 {node.id!r}: {formula!r}"
            )
        value = fields.get(node.id)
        # NULL 字段按 0 参与计算（规划 5.2）
        return Decimal("0") if value is None else Decimal(value)

    # 函数调用 / 属性访问 / 下标等一切其他节点 → 拒绝（防注入）
    raise AdapterConfigError(
        f"net_amount_formula 包含不允许的表达式节点 {type(node).__name__}: {formula!r}"
    )


def compute_net_amount(spec: AdapterSpec, row: dict) -> Decimal:
    """按适配器口径声明计算单行的实际成交额。

    ``row`` 为统一字段名 → 值的字典；金额字段可为 None。
    """
    fields = {name: row.get(name) for name in NUMERIC_FIELDS}
    return eval_formula(spec.metric_semantics.net_amount_formula, fields)


def semantics_warning(spec: AdapterSpec) -> str | None:
    """需要重点提示的口径差异（如京东含运费），无则 None。"""
    sem = spec.metric_semantics
    if sem.gmv_includes_shipping:
        return f"{spec.display_name} GMV 含运费，与其他平台不可直接比较"
    return None
