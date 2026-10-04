"""SKU 跨平台映射（规划 6.3）。

四级匹配优先级：
1. 查表命中：SkuMapping 已有 sku_id → 直接沿用
2. 精确码匹配：platform_product_code == Sku.sku_code → 自动落库
3. 名称模糊匹配：仅生成**建议**，绝不自动落库
4. 未映射：写入 sku_id=NULL 的映射行，行保持 sku_id=NULL

未映射行绝不丢弃、绝不阻塞报表：聚合时归入虚拟分组【待映射】。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from backend.app.core import SkuNotFoundError
from backend.app.models.entities import SalesFact, Sku, SkuMapping

# 模糊匹配建议阈值（规划 6.3）
FUZZY_HIGH = 90.0   # >= 90 高置信建议
FUZZY_MEDIUM = 75.0  # 75~90 中置信建议；< 75 不建议

# 名称预处理：去括号内容、去规格词、压缩空格
_BRACKETS_RE = re.compile(r"【[^】]*】|\[[^\]]*\]|\([^)]*\)|（[^）]*）")
_SPEC_WORDS_RE = re.compile(r"克|kg|ml|毫升|片|支|盒|袋|装|正品|旗舰|包邮|新款", re.IGNORECASE)
_SPACES_RE = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """模糊匹配前的名称预处理（规划 6.3 第 3 级）。"""
    if not name:
        return ""
    text = str(name).lower()
    text = _BRACKETS_RE.sub("", text)
    text = _SPEC_WORDS_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text).strip()
    return text


@dataclass
class FuzzyCandidate:
    sku: Sku
    score: float

    @property
    def band(self) -> str:
        if self.score >= FUZZY_HIGH:
            return "high"
        if self.score >= FUZZY_MEDIUM:
            return "medium"
        return "none"


def fuzzy_candidates(db: Session, product_name: str | None, category: str | None,
                     limit: int = 3) -> list[FuzzyCandidate]:
    """对给定平台商品名生成 Top-N SKU 候选（带得分）。

    候选池优先同 category 的 Sku.name，不足 5 个时扩大到全量。
    """
    target = normalize_name(product_name)
    if not target:
        return []

    query = db.query(Sku).filter(Sku.status == "active")
    pool = query.filter(Sku.category == category).all() if category else []
    if len(pool) < 5:
        pool = query.all()
    if not pool:
        return []

    choices = {sku.id: normalize_name(sku.name) for sku in pool}
    # 目标名去重后仍为空 → 无可比对
    if not any(choices.values()):
        return []

    matches = process.extract(
        target,
        choices,
        scorer=fuzz.token_sort_ratio,
        limit=limit,
        score_cutoff=FUZZY_MEDIUM,
    )
    sku_by_id = {sku.id: sku for sku in pool}
    return [
        FuzzyCandidate(sku=sku_by_id[choice[2]], score=float(choice[1]))
        for choice in matches
    ]


def _upsert_mapping(db: Session, platform_key: str, platform_product_code: str,
                    platform_product_name: str | None, sku_id: int | None,
                    match_type: str, confidence: float) -> SkuMapping:
    """按 (platform_key, platform_product_code) 唯一键 upsert 映射行。"""
    row = (
        db.query(SkuMapping)
        .filter(
            SkuMapping.platform_key == platform_key,
            SkuMapping.platform_product_code == platform_product_code,
        )
        .one_or_none()
    )
    if row is None:
        row = SkuMapping(
            platform_key=platform_key,
            platform_product_code=platform_product_code,
            platform_product_name=platform_product_name,
            sku_id=sku_id,
            match_type=match_type,
            confidence=confidence,
        )
        db.add(row)
        # 立即 flush：同一商品码的后续行 / 后续查询必须能看到本行，
        # 否则会违反唯一约束
        db.flush()
    else:
        row.platform_product_name = platform_product_name or row.platform_product_name
        # 已确认（manual/exact）的映射不被后续同名商品重置为 unmapped
        if sku_id is not None or row.sku_id is None:
            row.sku_id = sku_id
            row.match_type = match_type
            row.confidence = confidence
    return row


def attach_sku_ids(db: Session, rows: list, platform_key: str) -> dict[str, int]:
    """为归一化行填充 sku_id（commit 阶段调用）。

    ``rows`` 为 NormalizedRow 列表；同一商品码只求值一次，结果批量回填。
    返回统计：``{"mapped": n, "unmapped": n, "suggestions": n}``，
    suggestions 为"有至少 1 个中/高置信候选"的未映射商品数。
    """
    sku_code_index: dict[str, Sku] = {
        s.sku_code: s for s in db.query(Sku).filter(Sku.status == "active").all()
    }

    existing_mappings: dict[str, SkuMapping] = {
        m.platform_product_code: m
        for m in db.query(SkuMapping).filter(SkuMapping.platform_key == platform_key).all()
    }

    # 按平台商品码分组（grain=sku_day 时同一码会出现多天）
    by_code: dict[str, list] = {}
    for row in rows:
        by_code.setdefault(row.platform_product_code, []).append(row)

    mapped = 0
    unmapped = 0
    suggestions = 0

    for code, code_rows in by_code.items():
        sample = code_rows[0]
        mapping = existing_mappings.get(code)

        # 1) 查表命中：沿用表中映射
        if mapping is not None and mapping.sku_id is not None:
            for r in code_rows:
                r.sku_id = mapping.sku_id
            mapped += 1
            continue

        # 2) 精确码匹配：平台编码恰为内部 sku_code → 自动建映射
        sku = sku_code_index.get(code)
        if sku is not None:
            _upsert_mapping(db, platform_key, code, sample.product_name,
                            sku.id, "exact", 1.0)
            for r in code_rows:
                r.sku_id = sku.id
            mapped += 1
            continue

        # 3) 名称模糊匹配：仅统计建议数，不落库、不填 sku_id
        candidates = fuzzy_candidates(db, sample.product_name, sample.category)
        if candidates:
            suggestions += 1

        # 4) 未映射：登记 sku_id=NULL 的映射行，行保持 sku_id=NULL
        _upsert_mapping(db, platform_key, code, sample.product_name, None,
                        "unmapped", 0.0)
        for r in code_rows:
            r.sku_id = None
        unmapped += 1

    db.flush()
    return {"mapped": mapped, "unmapped": unmapped, "suggestions": suggestions}


def batch_upsert(db: Session, items: list[dict]) -> int:
    """批量人工映射（单事务 upsert，match_type=manual）。

    任一 sku_id 不存在则抛 SkuNotFoundError，由调用方回滚整个事务。
    返回成功条数。
    """
    sku_ids = {item["sku_id"] for item in items if item.get("sku_id") is not None}
    if sku_ids:
        found = {
            s.id for s in db.query(Sku.id).filter(Sku.id.in_(sku_ids)).all()
        }
        missing = sku_ids - found
        if missing:
            raise SkuNotFoundError(
                f"以下内部 SKU 不存在：{sorted(missing)}，整批操作已回滚",
                detail={"missing_sku_ids": sorted(missing)},
            )

    for item in items:
        # 先取旧值：_upsert_mapping 会直接改写 sku_id，解绑方向的
        # 历史明细回清必须基于旧 sku_id 过滤，避免误清其它映射
        old_sku_id = (
            db.query(SkuMapping.sku_id)
            .filter(
                SkuMapping.platform_key == item["platform_key"],
                SkuMapping.platform_product_code == item["platform_product_code"],
            )
            .scalar()
        )
        row = _upsert_mapping(
            db,
            item["platform_key"],
            item["platform_product_code"],
            None,  # 不更新缓存名
            item.get("sku_id"),
            "manual",
            1.0,
        )
        # 与单条采纳（apply_suggestion）行为对齐：回填/回清该平台商品
        # 的历史明细，否则看板【待映射】分组会一直保留这些商品；
        # 解绑（sku_id=None）方向同样要回清，否则旧 SKU 被历史明细
        # 永久引用、看板【待映射】消不掉
        if row.sku_id is not None:
            db.query(SalesFact).filter(
                SalesFact.platform_key == row.platform_key,
                SalesFact.platform_product_code == row.platform_product_code,
            ).update({"sku_id": row.sku_id}, synchronize_session=False)
        elif old_sku_id is not None:
            db.query(SalesFact).filter(
                SalesFact.platform_key == row.platform_key,
                SalesFact.platform_product_code == row.platform_product_code,
                SalesFact.sku_id == old_sku_id,
            ).update({"sku_id": None}, synchronize_session=False)
    db.flush()
    return len(items)


def apply_suggestion(db: Session, mapping_id: int, sku_id: int | None) -> SkuMapping:
    """采纳建议 / 手工指定映射（PUT /api/sku-mappings/{id}）。"""
    from backend.app.core import ValidationError

    row = db.query(SkuMapping).filter(SkuMapping.id == mapping_id).one_or_none()
    if row is None:
        raise ValidationError(f"映射记录不存在：{mapping_id}")
    old_sku_id = row.sku_id
    if sku_id is not None:
        sku = db.query(Sku).filter(Sku.id == sku_id).one_or_none()
        if sku is None:
            raise SkuNotFoundError(f"内部 SKU 不存在：{sku_id}")
        row.sku_id = sku.id
        row.match_type = "manual"
        row.confidence = 1.0
    else:
        row.sku_id = None
        row.match_type = "unmapped"
        row.confidence = 0.0

    # 采纳后回填该平台商品的历史明细；解绑（sku_id=None）方向回清，
    # 否则旧 SKU 被历史明细永久引用、看板【待映射】消不掉
    if row.sku_id is not None:
        db.query(SalesFact).filter(
            SalesFact.platform_key == row.platform_key,
            SalesFact.platform_product_code == row.platform_product_code,
        ).update({"sku_id": row.sku_id}, synchronize_session=False)
    elif old_sku_id is not None:
        db.query(SalesFact).filter(
            SalesFact.platform_key == row.platform_key,
            SalesFact.platform_product_code == row.platform_product_code,
            SalesFact.sku_id == old_sku_id,
        ).update({"sku_id": None}, synchronize_session=False)
    db.flush()
    return row


def sku_reference_count(db: Session, sku_id: int) -> dict[str, int]:
    """SKU 被引用情况（删除确认用）。"""
    facts = db.query(SalesFact).filter(SalesFact.sku_id == sku_id).count()
    mappings = db.query(SkuMapping).filter(SkuMapping.sku_id == sku_id).count()
    return {"sales_facts": facts, "mappings": mappings}
