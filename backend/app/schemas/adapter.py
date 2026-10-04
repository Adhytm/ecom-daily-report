"""适配器 YAML 的 Pydantic 模型（规划 5.1）。

四个内置平台适配器与用户自定义适配器共用本模型；所有交叉校验
规则在 ``AdapterSpec`` 的 ``model_validator`` 中集中实现（T02 验收）。
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---- 统一 Schema 字段白名单（规划 4.1，不得增删） ----

UNIFIED_FIELDS: frozenset[str] = frozenset(
    {
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
        "raw_row_json",
    }
)

# 允许出现在 column_map / transforms / required_columns 中的字段：
# platform_key 来自适配器自身、sku_id 来自映射表、raw_row_json 为快照，
# 三者不可能从文件列映射得到，故排除。
MAPPABLE_FIELDS: frozenset[str] = UNIFIED_FIELDS - {
    "platform_key",
    "sku_id",
    "raw_row_json",
}

# 数值类统一字段（公式 / dedup 合并时参与算术运算）
NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        "order_qty",
        "paid_qty",
        "gmv",
        "refund_amount",
        "net_amount",
        "visitors",
        "buyers",
        "ad_cost",
    }
)

# row_filters 支持的操作符（规划 5.3，共 13 种）
FILTER_OPS = Literal[
    "not_null",
    "is_null",
    "numeric",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "neq",
]

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


class FileMatching(BaseModel):
    """文件匹配与物理解析参数。"""

    model_config = ConfigDict(extra="forbid")

    extensions: list[str] = Field(default_factory=lambda: [".csv", ".xlsx"])
    encoding_candidates: list[str] = Field(default_factory=lambda: ["utf-8"])
    # 工作表选择：None=首个工作表；str=指定工作表名；list=依次尝试；"auto"=自动挑选最像数据表的页
    sheet: Union[str, list[str], None] = "auto"
    # 整数(1-based) 或 auto
    header_row: Union[int, Literal["auto"]] = "auto"
    auto_header_hints: list[str] = Field(default_factory=list)
    skip_footer: int = Field(default=0, ge=0)
    detect_keywords: list[str] = Field(default_factory=list)
    detect_weight: float = 1.0

    @model_validator(mode="after")
    def _check_auto_hints(self) -> "FileMatching":
        # auto 定位表头依赖 hints，缺失则无法定位
        if self.header_row == "auto" and not self.auto_header_hints:
            raise ValueError("file_matching.header_row 为 auto 时必须提供 auto_header_hints")
        return self


class TransformSpec(BaseModel):
    """单字段类型转换规则。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["datetime", "money", "integer", "string"]
    # datetime
    formats: list[str] | None = None
    truncate_to: Literal["day"] | None = None
    # money / integer / string
    strip_chars: list[str] | None = None
    unit: Literal["yuan", "fen"] | None = None
    # 空值/解析失败前的兜底值：字符串形式，"null" 或 None 表示保持 NULL
    default: str | None = None
    trim: bool | None = None

    @model_validator(mode="after")
    def _check_by_type(self) -> "TransformSpec":
        if self.type == "datetime":
            if not self.formats:
                raise ValueError("datetime 转换必须提供 formats")
        if self.type == "money" and self.unit is None:
            raise ValueError("money 转换必须指定 unit: yuan | fen")
        return self


class RowFilter(BaseModel):
    """行过滤条件（全部条件 AND，通过才保留）。"""

    model_config = ConfigDict(extra="forbid")

    column: str
    op: FILTER_OPS
    # in / not_in / contains / not_contains 使用列表
    values: list[str] | None = None
    # gt / gte / lt / lte / eq / neq 使用标量
    value: Union[str, float, int, None] = None

    @model_validator(mode="after")
    def _check_op_args(self) -> "RowFilter":
        needs_values = self.op in ("in", "not_in", "contains", "not_contains")
        needs_value = self.op in ("gt", "gte", "lt", "lte", "eq", "neq")
        if needs_values and not self.values:
            raise ValueError(f"op={self.op} 必须提供 values 列表")
        if needs_value and self.value is None:
            raise ValueError(f"op={self.op} 必须提供 value 标量")
        return self


class MetricSemantics(BaseModel):
    """口径声明（规划 5.1 / 5.4）。"""

    model_config = ConfigDict(extra="forbid")

    gmv_basis: Literal["paid", "ordered", "shipped"]
    gmv_includes_shipping: bool
    gmv_includes_tax: bool
    refund_handling: Literal["separate", "deducted", "none"]
    net_amount_formula: str
    timezone: str = "Asia/Shanghai"
    grain: Literal["sku_day", "order_line", "day"]
    # 平台扣点比例（0.05 = 5%）。默认 0：不参与利润计算，
    # 各平台各类目扣点不同，需按实际经营类目填写
    platform_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    # 单件履约成本（快递 + 包材，元/件）。默认 0：不计入利润
    fulfillment_cost_per_unit: Decimal = Field(default=Decimal("0"), ge=0)


class Dedup(BaseModel):
    """去重策略。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    keys: list[str] = Field(default_factory=list)
    strategy: Literal["sum", "last", "first"] = "sum"

    @model_validator(mode="after")
    def _check_keys(self) -> "Dedup":
        if self.enabled and not self.keys:
            raise ValueError("dedup.enabled=true 时必须提供 keys")
        return self


def _extract_formula_names(formula: str) -> set[str]:
    """AST 解析公式，返回引用的字段名集合；语法非法直接抛 ValueError。"""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"net_amount_formula 语法非法: {formula!r}（{e}）") from e
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


class AdapterSpec(BaseModel):
    """一个平台适配器的完整定义。"""

    model_config = ConfigDict(extra="forbid")

    adapter_version: int = Field(ge=1)
    platform_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1)

    file_matching: FileMatching
    column_map: dict[str, str] = Field(min_length=1)
    column_aliases: dict[str, list[str]] = Field(default_factory=dict)
    required_columns: list[str] = Field(min_length=1)
    optional_columns: list[str] = Field(default_factory=list)
    transforms: dict[str, TransformSpec] = Field(default_factory=dict)
    metric_semantics: MetricSemantics
    row_filters: list[RowFilter] = Field(default_factory=list)
    # 未显式配置去重时默认关闭（enabled=false 不要求 keys）
    dedup: Dedup = Field(default_factory=lambda: Dedup(enabled=False, keys=[], strategy="sum"))

    # ---- 逐值校验 ----

    @field_validator("platform_key")
    @classmethod
    def _platform_key_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE.match(v):
            raise ValueError(f"platform_key 必须是 snake_case: {v!r}")
        return v

    @model_validator(mode="after")
    def _cross_validate(self) -> "AdapterSpec":
        errors: list[str] = []

        # 1) column_map 的值必须属于统一 Schema 可映射字段白名单
        for src, dst in self.column_map.items():
            if dst not in MAPPABLE_FIELDS:
                errors.append(
                    f"column_map[{src!r}] -> {dst!r} 不在统一 Schema 字段白名单内"
                )

        # column_map 值也不得重复（两个源列映射到同一统一字段会互相覆盖）
        seen_dst: dict[str, str] = {}
        for src, dst in self.column_map.items():
            if dst in seen_dst:
                errors.append(f"column_map 中 {dst!r} 被重复映射: {seen_dst[dst]!r} 与 {src!r}")
            seen_dst[dst] = src

        alias_keys = set(self.column_aliases.keys())
        for alias_key, alias_list in self.column_aliases.items():
            if alias_key not in MAPPABLE_FIELDS:
                errors.append(f"column_aliases 键 {alias_key!r} 不在统一 Schema 字段白名单内")
            if alias_key not in seen_dst:
                errors.append(
                    f"column_aliases 键 {alias_key!r} 未出现在 column_map 值中，别名无效"
                )
            if not alias_list:
                errors.append(f"column_aliases[{alias_key!r}] 不能为空列表")

        # 2) required_columns ⊆ column_map.values() ∪ column_aliases.keys()
        satisfiable = set(seen_dst) | alias_keys
        for col in self.required_columns:
            if col not in satisfiable:
                errors.append(
                    f"required_columns 中的 {col!r} 既不在 column_map 值内，"
                    f"也没有 column_aliases 别名"
                )
        # optional_columns 仅作登记，不做可满足性校验（可选列本来就允许缺席）

        # 3) transforms 的键必须 ⊆ column_map.values()
        for key in self.transforms:
            if key not in seen_dst:
                errors.append(f"transforms 键 {key!r} 不是 column_map 的值")

        # 4) net_amount_formula 引用字段必须在白名单内（且语法合法）
        try:
            names = _extract_formula_names(self.metric_semantics.net_amount_formula)
        except ValueError as e:
            errors.append(str(e))
        else:
            for n in names:
                if n not in NUMERIC_FIELDS:
                    errors.append(
                        f"net_amount_formula 引用了非法字段 {n!r}，"
                        f"仅允许数值字段: {sorted(NUMERIC_FIELDS)}"
                    )

        # 5) dedup.keys 必须是统一 Schema 字段
        for k in self.dedup.keys:
            if k not in UNIFIED_FIELDS:
                errors.append(f"dedup.keys 中的 {k!r} 不是统一 Schema 字段")

        # 6) row_filters[].column 必须是统一 Schema 字段或源表头名
        source_headers = set(self.column_map.keys())
        for aliases in self.column_aliases.values():
            source_headers.update(aliases)
        for f in self.row_filters:
            if f.column not in UNIFIED_FIELDS and f.column not in source_headers:
                errors.append(
                    f"row_filters 的 column {f.column!r} 既不是统一 Schema 字段，"
                    f"也不是源表头名"
                )

        if errors:
            raise ValueError("适配器配置校验失败：" + "；".join(errors))
        return self

    # ---- 便捷方法 ----

    def formula_fields(self) -> set[str]:
        """net_amount_formula 引用的字段集合。"""
        return _extract_formula_names(self.metric_semantics.net_amount_formula)


class AdapterValidateResult(BaseModel):
    """POST /api/adapters/{key}/validate 的返回结构。"""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def build_semantics_note(spec: AdapterSpec) -> str:
    """根据口径声明生成人类可读的中文口径说明（T05）。"""
    sem = spec.metric_semantics
    basis_text = {"paid": "支付口径", "ordered": "下单口径", "shipped": "发货口径"}[sem.gmv_basis]
    lines = [
        f"GMV 口径：{basis_text}（{sem.gmv_basis}）",
        f"GMV 是否含运费：{'含' if sem.gmv_includes_shipping else '不含'}",
        f"GMV 是否含税：{'含' if sem.gmv_includes_tax else '不含'}",
        f"退款处理方式：{sem.refund_handling}",
        f"实际成交额公式：{sem.net_amount_formula}",
        f"数据粒度：{sem.grain}",
        f"时区：{sem.timezone}",
        f"平台扣点比例：{sem.platform_fee_rate}（0 表示利润不含扣点）",
        f"单件履约成本：{sem.fulfillment_cost_per_unit} 元/件",
    ]
    if sem.gmv_includes_shipping:
        lines.append(
            f"注意：{spec.display_name} 的 GMV 含运费，与其他平台不可直接相加比较"
        )
    if sem.refund_handling == "none":
        lines.append("注意：退款数据缺失，实际成交额未扣除退款")
    return "\n".join(lines)
