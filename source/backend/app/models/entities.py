"""全部 ORM 实体（规划 4.2）。

金额一律 Numeric(14,2)（Decimal），禁止 float；统一 Schema 字段名
为跨模块契约，不得增删改。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base


class Upload(Base):
    """一次文件上传（解析 → 提交两阶段）。"""

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    platform_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    adapter_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending | parsed | committed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    row_count_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    errors: Mapped[list["UploadError"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="UploadError.row_number",
    )
    sales_facts: Mapped[list["SalesFact"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan"
    )


class UploadError(Base):
    """行级错误明细：前端可展示“哪些行没解析成功”。"""

    __tablename__ = "upload_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # missing_required_column / date_parse_failed / number_parse_failed
    # / filtered_out / duplicate_row
    error_type: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    upload: Mapped[Upload] = relationship(back_populates="errors")


class SalesFact(Base):
    """归一化后的销售明细（统一 Schema，规划 4.1 全部字段）。"""

    __tablename__ = "sales_facts"
    __table_args__ = (
        Index("ix_sales_facts_date_platform", "stat_date", "platform_key"),
        Index("ix_sales_facts_date_sku", "stat_date", "sku_id"),
        Index("ix_sales_facts_upload", "upload_id"),
        Index(
            "ix_sales_facts_platform_product",
            "platform_key",
            "platform_product_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    # ---- 统一 Schema ----
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    platform_key: Mapped[str] = mapped_column(String(32), nullable=False)
    shop_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shop_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("skus.id", ondelete="SET NULL"), nullable=True
    )
    order_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gmv: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    visitors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buyers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ad_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    raw_row_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    upload: Mapped[Upload] = relationship(back_populates="sales_facts")
    sku: Mapped["Sku | None"] = relationship()


class Sku(Base):
    """内部统一商品。"""

    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # active | discontinued
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    mappings: Mapped[list["SkuMapping"]] = relationship(back_populates="sku")


class SkuMapping(Base):
    """平台商品编码 → 内部 SKU 映射。"""

    __tablename__ = "sku_mappings"
    __table_args__ = (
        UniqueConstraint(
            "platform_key", "platform_product_code", name="uq_mapping_platform_code"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    platform_product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_product_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("skus.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # manual | exact | fuzzy | unmapped
    match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unmapped")
    # 模糊匹配得分（0~1），manual/exact 为 1.0
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    sku: Mapped["Sku | None"] = relationship(back_populates="mappings")


class AdapterConfig(Base):
    """用户覆盖的适配器配置（内置 YAML 作为 baseline）。"""

    __tablename__ = "adapter_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )


class DailyReport(Base):
    """已生成的日报（同日期重复生成时覆盖更新）。"""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )
    scope_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    excel_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AnomalyRule(Base):
    """异常预警规则（阈值可配）。"""

    __tablename__ = "anomaly_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    # overall | platform | shop | category | sku
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # gt | lt | abs_gt
    operator: Mapped[str] = mapped_column(String(8), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    # 基数下限：主体 GMV 低于该值不评估，避免小样本误报
    min_base: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
