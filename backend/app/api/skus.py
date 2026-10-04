"""内部 SKU 与映射维护 API（规划 7 / 8.4）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.core import SkuInUseError, SkuNotFoundError, ValidationError
from backend.app.core.sku_mapper import (
    apply_suggestion,
    batch_upsert,
    fuzzy_candidates,
    sku_reference_count,
)
from backend.app.main import ok
from backend.app.models.entities import SalesFact, Sku, SkuMapping
from backend.app.schemas.sku import (
    BatchMappingIn,
    MappingUpdateIn,
    SkuCreate,
    SkuMappingOut,
    SkuOut,
    SkuUpdate,
    SuggestCandidate,
    SuggestItem,
)

router = APIRouter(tags=["skus"])


def _sku_or_404(db: Session, sku_id: int) -> Sku:
    sku = db.query(Sku).filter(Sku.id == sku_id).one_or_none()
    if sku is None:
        raise SkuNotFoundError(f"内部 SKU 不存在：{sku_id}")
    return sku


# ---------------------------------------------------------------------------
# 内部 SKU CRUD
# ---------------------------------------------------------------------------


@router.get("/skus")
def list_skus(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """SKU 分页列表，支持名称/编码搜索与分类筛选。"""
    query = db.query(Sku)
    if q:
        like = f"%{q}%"
        query = query.filter((Sku.name.like(like)) | (Sku.sku_code.like(like)))
    if category:
        query = query.filter(Sku.category == category)
    total = query.count()
    rows = query.order_by(Sku.sku_code).offset((page - 1) * size).limit(size).all()
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [SkuOut.model_validate(s).model_dump(mode="json") for s in rows],
        }
    )


@router.post("/skus")
def create_sku(body: SkuCreate, db: Session = Depends(get_db)):
    exists = db.query(Sku).filter(Sku.sku_code == body.sku_code).one_or_none()
    if exists:
        raise ValidationError(f"SKU 编码已存在：{body.sku_code}")
    sku = Sku(**body.model_dump())
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return ok(SkuOut.model_validate(sku).model_dump(mode="json"))


@router.put("/skus/{sku_id}")
def update_sku(sku_id: int, body: SkuUpdate, db: Session = Depends(get_db)):
    sku = _sku_or_404(db, sku_id)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(sku, k, v)
    db.commit()
    db.refresh(sku)
    return ok(SkuOut.model_validate(sku).model_dump(mode="json"))


@router.delete("/skus/{sku_id}")
def delete_sku(sku_id: int, db: Session = Depends(get_db)):
    """删除 SKU；被 SalesFact/SkuMapping 引用时拒绝（409）并返回引用数。"""
    sku = _sku_or_404(db, sku_id)
    refs = sku_reference_count(db, sku_id)
    if refs["sales_facts"] > 0 or refs["mappings"] > 0:
        raise SkuInUseError(
            f"SKU {sku.sku_code} 被 {refs['sales_facts']} 条销售明细、"
            f"{refs['mappings']} 条映射记录引用，无法删除",
            detail=refs,
        )
    db.delete(sku)
    db.commit()
    return ok({"deleted": sku_id})


# ---------------------------------------------------------------------------
# 映射表
# ---------------------------------------------------------------------------


@router.get("/sku-mappings")
def list_mappings(
    platform_key: str | None = Query(default=None),
    match_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """映射表分页列表，可按平台 / 匹配类型 / 名称搜索。

    ``match_type=confirmed`` 为前端「已确认」Tab 的专用值：
    匹配任意已绑定内部 SKU 的映射（manual/exact/fuzzy）。
    """
    query = db.query(SkuMapping)
    if platform_key:
        query = query.filter(SkuMapping.platform_key == platform_key)
    if match_type == "confirmed":
        query = query.filter(SkuMapping.sku_id.isnot(None))
    elif match_type:
        query = query.filter(SkuMapping.match_type == match_type)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (SkuMapping.platform_product_name.like(like))
            | (SkuMapping.platform_product_code.like(like))
        )
    total = query.count()
    rows = (
        query.order_by(SkuMapping.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    sku_ids = {r.sku_id for r in rows if r.sku_id}
    sku_map = {}
    if sku_ids:
        sku_map = {
            s.id: s for s in db.query(Sku).filter(Sku.id.in_(sku_ids)).all()
        }
    items = []
    for r in rows:
        item = SkuMappingOut(
            id=r.id,
            platform_key=r.platform_key,
            platform_product_code=r.platform_product_code,
            platform_product_name=r.platform_product_name,
            sku_id=r.sku_id,
            sku_code=sku_map[r.sku_id].sku_code if r.sku_id in sku_map else None,
            sku_name=sku_map[r.sku_id].name if r.sku_id in sku_map else None,
            match_type=r.match_type,
            confidence=float(r.confidence),
            updated_at=r.updated_at,
        )
        items.append(item.model_dump(mode="json"))
    return ok({"total": total, "page": page, "size": size, "items": items})


@router.put("/sku-mappings/{mapping_id}")
def update_mapping(mapping_id: int, body: MappingUpdateIn, db: Session = Depends(get_db)):
    """采纳建议 / 手工指定映射（回填历史明细的 sku_id）。"""
    row = apply_suggestion(db, mapping_id, body.sku_id)
    db.commit()
    return ok(
        {
            "id": row.id,
            "platform_key": row.platform_key,
            "platform_product_code": row.platform_product_code,
            "sku_id": row.sku_id,
            "match_type": row.match_type,
            "confidence": float(row.confidence),
        }
    )


@router.post("/sku-mappings/batch")
def batch_mappings(body: BatchMappingIn, db: Session = Depends(get_db)):
    """批量人工映射：单事务 upsert，部分失败整体回滚。"""
    try:
        count = batch_upsert(db, [i.model_dump() for i in body.items])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"updated": count})


@router.post("/sku-mappings/suggest")
def suggest_mappings(body: dict, db: Session = Depends(get_db)):
    """未映射项 + 模糊匹配建议候选（Top3，带得分）。"""
    platform_key = body.get("platform_key")
    try:
        limit = int(body.get("limit", 50))
    except (TypeError, ValueError):
        raise ValidationError("limit 必须是整数")

    query = db.query(SkuMapping).filter(SkuMapping.sku_id.is_(None))
    if platform_key:
        query = query.filter(SkuMapping.platform_key == platform_key)
    rows = query.order_by(SkuMapping.id).limit(limit).all()

    items = []
    for r in rows:
        candidates = fuzzy_candidates(db, r.platform_product_name, None, limit=3)
        items.append(
            SuggestItem(
                mapping_id=r.id,
                platform_key=r.platform_key,
                platform_product_code=r.platform_product_code,
                product_name=r.platform_product_name,
                candidates=[
                    SuggestCandidate(
                        sku_id=c.sku.id,
                        sku_code=c.sku.sku_code,
                        name=c.sku.name,
                        score=round(c.score, 1),
                    )
                    for c in candidates
                ],
            ).model_dump()
        )
    return ok({"items": items, "total": len(items)})


@router.get("/sku-mappings/{mapping_id}/reference-count")
def mapping_reference_count(mapping_id: int, db: Session = Depends(get_db)):
    """映射对应 SKU 的引用计数（前端删除确认用）。"""
    row = db.query(SkuMapping).filter(SkuMapping.id == mapping_id).one_or_none()
    if row is None or row.sku_id is None:
        return ok({"sales_facts": 0, "mappings": 0})
    return ok(sku_reference_count(db, row.sku_id))


@router.get("/skus/{sku_id}/reference-count")
def sku_refs(sku_id: int, db: Session = Depends(get_db)):
    """SKU 引用计数。"""
    _sku_or_404(db, sku_id)
    return ok(sku_reference_count(db, sku_id))
