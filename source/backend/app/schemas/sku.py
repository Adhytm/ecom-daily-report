"""SKU 与映射相关的 API Schema（规划 7）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SKU_STATUS = Literal["active", "discontinued"]


class SkuCreate(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    category: str | None = None
    brand: str | None = None
    cost_price: Decimal | None = None
    status: SKU_STATUS = "active"


class SkuUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    category: str | None = None
    brand: str | None = None
    cost_price: Decimal | None = None
    status: SKU_STATUS | None = None

    @model_validator(mode="after")
    def _explicit_null_forbidden(self) -> "SkuUpdate":
        # name/status 落库为 NOT NULL：客户端显式传 null 必须在
        # 校验层拒绝（422），否则 setattr(None) 会触发 IntegrityError
        # 变成 500。category/brand/cost_price 可空，允许显式清空。
        for field in ("name", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null（如需清空请省略该字段）")
        return self


class SkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name: str
    category: str | None
    brand: str | None
    cost_price: Decimal | None
    status: str
    created_at: datetime
    updated_at: datetime


class SkuMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform_key: str
    platform_product_code: str
    platform_product_name: str | None
    sku_id: int | None
    sku_code: str | None = None
    sku_name: str | None = None
    match_type: str
    confidence: float
    updated_at: datetime


class BatchMappingItem(BaseModel):
    """POST /api/sku-mappings/batch 的单项。"""

    platform_key: str
    platform_product_code: str
    sku_id: int | None = None


class BatchMappingIn(BaseModel):
    items: list[BatchMappingItem] = Field(min_length=1)


class SuggestCandidate(BaseModel):
    """模糊匹配候选。"""

    sku_id: int
    sku_code: str
    name: str
    score: float  # 0~100


class SuggestItem(BaseModel):
    """一条未映射项 + 建议候选。"""

    mapping_id: int
    platform_key: str
    platform_product_code: str
    product_name: str | None
    candidates: list[SuggestCandidate] = Field(default_factory=list)


class MappingUpdateIn(BaseModel):
    sku_id: int | None = None
    match_type: str = "manual"
