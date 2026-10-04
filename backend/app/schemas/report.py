"""日报看板 / 聚合结果的响应 Schema（规划 6.4 契约，T08 强校验用）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricBlock(BaseModel):
    """单个指标的取值与对比。

    - 金额/数量类：value + dod（环比）+ wow（周同比），均为百分比变化率
    - 比率类（退款率/转化率）：value + dod_pp + wow_pp，为百分点差值
    - ROI 等倍率指标按百分比变化率处理（口径说明文档中注明）
    """

    value: float | None = None
    dod: float | None = None
    wow: float | None = None
    dod_pp: float | None = None
    wow_pp: float | None = None


class DimensionRow(BaseModel):
    """分平台 / 分店铺 / 分类目的单行。"""

    key: str
    name: str
    metrics: dict[str, MetricBlock]
    share: float | None = None


class MappedSkuRow(BaseModel):
    """分SKU（已映射）单行。"""

    sku_code: str
    name: str
    category: str | None
    platforms: str | None = None
    metrics: dict[str, MetricBlock]


class UnmappedSkuRow(BaseModel):
    """分SKU（待映射）单行。"""

    platform_key: str
    platform_product_code: str
    product_name: str | None
    metrics: dict[str, MetricBlock]


class BySku(BaseModel):
    mapped: list[MappedSkuRow] = Field(default_factory=list)
    unmapped: list[UnmappedSkuRow] = Field(default_factory=list)


class DataCompleteness(BaseModel):
    """数据完整性提示（规划 6.4）。"""

    expected_platforms: list[str] = Field(default_factory=list)
    present_platforms: list[str] = Field(default_factory=list)
    missing_platforms: list[str] = Field(default_factory=list)
    warning: str | None = None
    # 有成本价的成交额占比（0~1）：<1 说明利润指标只覆盖部分商品
    cost_coverage: float | None = None


class TrendSeries(BaseModel):
    """近 30 天趋势序列。"""

    dates: list[str] = Field(default_factory=list)
    series: dict[str, list[float | None]] = Field(default_factory=dict)


class AnomalyItem(BaseModel):
    """一条异常预警。"""

    rule_name: str
    severity: str  # warning | critical
    scope: str
    subject: str
    metric: str
    value: float | None
    threshold: float
    message: str


class Diagnosis(BaseModel):
    """GMV 环比归因诊断（杜邦三因子拆解）。"""

    primary_driver: str
    driver_label: str
    explanation: str
    gmv_dod: float | None = None
    contributions: dict[str, float] = Field(default_factory=dict)


class DailyReportData(BaseModel):
    """GET /api/reports/daily 的完整返回结构（6.4 契约）。"""

    report_date: str
    weekday: str
    compare_dates: dict[str, str | None]
    data_completeness: DataCompleteness
    overview: dict[str, MetricBlock | str]
    overview_raw: dict[str, Any] | None = None
    diagnosis: Diagnosis | None = None
    by_platform: list[DimensionRow] = Field(default_factory=list)
    by_shop: list[DimensionRow] = Field(default_factory=list)
    by_category: list[DimensionRow] = Field(default_factory=list)
    by_sku: BySku = Field(default_factory=BySku)
    trend: TrendSeries
    top: dict[str, list[dict[str, Any]]]
    anomalies: list[AnomalyItem] = Field(default_factory=list)


class ScopeIn(BaseModel):
    """生成日报的范围参数。"""

    date: str
    platforms: list[str] | None = None
    shops: list[str] | None = None


class GenerateReportOut(BaseModel):
    """POST /api/reports/daily/generate 返回。

    带 scope（平台/店铺筛选）的生成不落库（保护同日期全量日报不被
    子集口径覆盖），此时 report_id / excel_url 为 None。
    """

    report_id: int | None
    report_date: str
    excel_url: str | None
    summary_text: str
    char_count: int


class DailyReportListItem(BaseModel):
    """GET /api/reports 历史列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_date: str
    generated_at: str
    # 不暴露服务器物理路径，前端走 /api/reports/daily/{date}/export
    excel_url: str | None
    has_summary: bool
    scope: dict | None


class AnomalyRuleIn(BaseModel):
    """PUT /api/anomaly-rules/{id} 允许修改的字段。"""

    name: str | None = None
    threshold: float | None = None
    min_base: float | None = None
    enabled: bool | None = None


class AnomalyRuleOut(BaseModel):
    id: int
    name: str
    metric: str
    scope: str
    operator: str
    threshold: float
    min_base: float
    enabled: bool
