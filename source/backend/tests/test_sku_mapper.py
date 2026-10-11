"""SKU 跨平台映射测试（规划 §3 / §6.3）：四级匹配优先级、映射落库、批量人工映射、采纳回填。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core import SkuNotFoundError, ValidationError
from backend.app.core.normalizer import NormalizedRow
from backend.app.core.sku_mapper import (
    FuzzyCandidate,
    apply_suggestion,
    attach_sku_ids,
    batch_upsert,
    fuzzy_candidates,
    normalize_name,
    sku_reference_count,
)
from backend.app.models.entities import SalesFact, Sku, SkuMapping, Upload

REPORT_DATE = date(2026, 9, 6)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _row(code: str, name: str | None = "玻尿酸精华液",
         category: str | None = "美妆",
         stat_date: date = REPORT_DATE) -> NormalizedRow:
    return NormalizedRow(
        stat_date=stat_date, platform_key="taobao", shop_code=None,
        shop_name="测试旗舰店", platform_product_code=code, product_name=name,
        category=category, order_qty=1, paid_qty=1, gmv=Decimal("100.00"),
        refund_amount=Decimal("0"), net_amount=Decimal("100.00"),
        visitors=10, buyers=1, ad_cost=Decimal("0"), raw_row_json={},
    )


def _sku(db, code: str, name: str, category: str | None = "美妆",
         status: str = "active") -> Sku:
    sku = Sku(sku_code=code, name=name, category=category, status=status)
    db.add(sku)
    db.flush()
    return sku


def _mapping(db, platform_key: str, code: str, sku_id: int | None,
             match_type: str, confidence) -> SkuMapping:
    m = SkuMapping(platform_key=platform_key, platform_product_code=code,
                   sku_id=sku_id, match_type=match_type, confidence=confidence)
    db.add(m)
    db.flush()
    return m


def _upload(db, sha: str) -> Upload:
    up = Upload(filename="t.csv", sha256=sha, platform_key="taobao",
                status="committed")
    db.add(up)
    db.flush()
    return up


def _fact(db, upload: Upload, code: str, sku_id: int | None = None) -> SalesFact:
    fact = SalesFact(
        upload_id=upload.id, sku_id=sku_id, stat_date=REPORT_DATE,
        platform_key="taobao", shop_code=None, shop_name="测试旗舰店",
        platform_product_code=code, product_name=None, category=None,
        order_qty=1, paid_qty=1, gmv=Decimal("100.00"), refund_amount=None,
        net_amount=Decimal("100.00"), visitors=None, buyers=None,
        ad_cost=None, raw_row_json={},
    )
    db.add(fact)
    db.flush()
    return fact


# ---------------------------------------------------------------------------
# 名称预处理（第 3 级匹配的前置）
# ---------------------------------------------------------------------------


def test_normalize_name_lowercases_and_strips_noise():
    assert normalize_name("超值 精华液30ml") == "超值 精华液30"
    assert normalize_name("胶原蛋白(礼盒)【正品】面霜") == "胶原蛋白面霜"
    assert normalize_name("面膜（滋润型）[新款]") == "面膜"
    assert normalize_name("A  B\t C") == "a b c"


def test_normalize_name_empty_and_fully_stripped():
    assert normalize_name(None) == ""
    assert normalize_name("") == ""
    assert normalize_name("【包邮】新款") == ""  # 括号与规格词剔除后为空


# ---------------------------------------------------------------------------
# 模糊匹配（仅生成建议，绝不自动落库）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score,band", [
    (95.0, "high"), (90.0, "high"),
    (89.9, "medium"), (75.0, "medium"),
    (74.9, "none"), (0.0, "none"),
])
def test_fuzzy_candidate_band_boundaries(score, band):
    assert FuzzyCandidate(sku=None, score=score).band == band


def test_fuzzy_candidates_empty_target_returns_empty(db_session):
    db = db_session
    _sku(db, "S1", "玻尿酸精华液")
    assert fuzzy_candidates(db, None, "美妆") == []
    assert fuzzy_candidates(db, "", "美妆") == []
    assert fuzzy_candidates(db, "【包邮】新款", "美妆") == []  # 归一化后为空


def test_fuzzy_candidates_no_active_skus(db_session):
    assert fuzzy_candidates(db_session, "玻尿酸精华液", "美妆") == []


def test_fuzzy_candidates_ignores_discontinued(db_session):
    db = db_session
    _sku(db, "D1", "玻尿酸精华液", status="discontinued")
    assert fuzzy_candidates(db, "玻尿酸精华液", None) == []


def test_fuzzy_candidates_all_names_normalize_empty(db_session):
    db = db_session
    _sku(db, "S1", "【包邮】")
    _sku(db, "S2", "新款")
    assert fuzzy_candidates(db, "玻尿酸精华液", None) == []


def test_fuzzy_candidates_prefers_same_category_pool(db_session):
    db = db_session
    for i in range(5):  # 同类目候选池满 5 个 → 不扩大
        _sku(db, f"C{i}", f"美妆品{i}号")
    _sku(db, "X1", "玻尿酸精华液", category="个护")  # 同名候选在别的类目
    assert fuzzy_candidates(db, "玻尿酸精华液", "美妆") == []


def test_fuzzy_candidates_expands_pool_when_category_sparse(db_session):
    db = db_session
    _sku(db, "A1", "玻尿酸精华液", category="个护")
    _sku(db, "A2", "洗发水", category="个护")
    candidates = fuzzy_candidates(db, "玻尿酸补水精华液", "美妆")  # 美妆池空 → 全量
    assert [c.sku.sku_code for c in candidates] == ["A1"]


def test_fuzzy_candidates_score_bands_and_limit(db_session):
    db = db_session
    _sku(db, "H1", "玻尿酸精华液")        # 与目标一致 → 高置信
    _sku(db, "M1", "玻尿酸补水精华液")    # 部分重合 → 中置信
    candidates = fuzzy_candidates(db, "玻尿酸精华液", None)
    assert len(candidates) <= 3
    assert candidates[0].band == "high"
    assert any(c.band == "medium" for c in candidates)
    assert all(c.score >= 75.0 for c in candidates)


# ---------------------------------------------------------------------------
# attach_sku_ids：四级匹配优先级
# ---------------------------------------------------------------------------


def test_attach_lookup_hit_reuses_existing_mapping(db_session):
    db = db_session
    sku = _sku(db, "TB1001", "玻尿酸精华液")
    _mapping(db, "taobao", "P1", sku.id, "manual", Decimal("1"))
    # 同一商品码多天多行（grain=sku_day）→ 只求值一次，结果批量回填
    rows = [_row("P1"), _row("P1", stat_date=date(2026, 9, 5))]
    stats = attach_sku_ids(db, rows, "taobao")
    assert stats == {"mapped": 1, "unmapped": 0, "suggestions": 0}
    assert {r.sku_id for r in rows} == {sku.id}
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.match_type == "manual"  # 查表命中不改动原映射


def test_attach_exact_code_match_auto_maps(db_session):
    db = db_session
    sku = _sku(db, "P-EXACT", "内部商品")  # sku_code 恰等于平台编码
    rows = [_row("P-EXACT", name="平台侧叫别的名字")]
    stats = attach_sku_ids(db, rows, "taobao")
    assert stats["mapped"] == 1 and stats["unmapped"] == 0
    assert rows[0].sku_id == sku.id
    m = db.query(SkuMapping).filter_by(
        platform_key="taobao", platform_product_code="P-EXACT").one()
    assert m.sku_id == sku.id
    assert m.match_type == "exact"
    assert m.confidence == Decimal("1.0")


def test_attach_fuzzy_suggestion_never_auto_maps(db_session):
    db = db_session
    _sku(db, "TB1001", "玻尿酸精华液")
    rows = [_row("P1", name="玻尿酸补水精华液")]
    stats = attach_sku_ids(db, rows, "taobao")
    assert stats == {"mapped": 0, "unmapped": 1, "suggestions": 1}
    assert rows[0].sku_id is None  # 建议只提示、不落库、不填 sku_id
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.sku_id is None
    assert m.match_type == "unmapped"
    assert m.confidence == 0


def test_attach_unmapped_without_suggestion(db_session):
    db = db_session
    _sku(db, "TB1001", "洗发水")
    rows = [_row("P1", name="完全无关的商品名")]
    stats = attach_sku_ids(db, rows, "taobao")
    assert stats["suggestions"] == 0 and stats["unmapped"] == 1
    assert rows[0].sku_id is None  # 未映射行不丢弃，保持 NULL 进【待映射】


def test_attach_mixed_codes_stats(db_session):
    db = db_session
    hit_sku = _sku(db, "TB1001", "已有映射商品")
    _sku(db, "P-EXACT", "精确码商品")
    _sku(db, "TB1002", "玻尿酸精华液")  # 仅供模糊建议
    _mapping(db, "taobao", "P-HIT", hit_sku.id, "manual", Decimal("1"))
    rows = [
        _row("P-HIT"),                                  # 1 查表命中
        _row("P-EXACT"),                                # 2 精确码
        _row("P-FUZZ", name="玻尿酸补水精华液"),        # 3 建议
        _row("P-NULL", name="完全无关商品名"),          # 4 未映射
    ]
    assert attach_sku_ids(db, rows, "taobao") == {
        "mapped": 2, "unmapped": 2, "suggestions": 1,
    }
    assert rows[0].sku_id == hit_sku.id


def test_attach_rerun_upgrades_unmapped_to_exact(db_session):
    db = db_session
    rows = [_row("P1", name="旧名字")]
    assert attach_sku_ids(db, rows, "taobao")["unmapped"] == 1

    sku = _sku(db, "P1", "后来建的商品")  # 第二次运行前补建了同码 SKU
    rows2 = [_row("P1", name=None)]      # 名字缺失 → 保留原缓存名
    stats = attach_sku_ids(db, rows2, "taobao")
    assert stats["mapped"] == 1
    assert rows2[0].sku_id == sku.id
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.sku_id == sku.id and m.match_type == "exact"
    assert m.platform_product_name == "旧名字"  # 新名字为空时不覆盖缓存名


# ---------------------------------------------------------------------------
# batch_upsert：批量人工映射（单事务 upsert）
# ---------------------------------------------------------------------------


def test_batch_upsert_creates_manual_mappings(db_session):
    db = db_session
    s1 = _sku(db, "S1", "商品一")
    s2 = _sku(db, "S2", "商品二")
    n = batch_upsert(db, [
        {"platform_key": "taobao", "platform_product_code": "P1", "sku_id": s1.id},
        {"platform_key": "jd", "platform_product_code": "P2", "sku_id": s2.id},
    ])
    assert n == 2
    m1 = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m1.sku_id == s1.id
    assert m1.match_type == "manual"
    assert m1.confidence == Decimal("1")


def test_batch_upsert_missing_sku_raises_and_writes_nothing(db_session):
    db = db_session
    s1 = _sku(db, "S1", "商品一")
    with pytest.raises(SkuNotFoundError):
        batch_upsert(db, [
            {"platform_key": "taobao", "platform_product_code": "P1", "sku_id": s1.id},
            {"platform_key": "jd", "platform_product_code": "P9", "sku_id": 99999},
        ])
    # 校验先于写入：任一 sku_id 不存在 → 整批不落库，调用方无需部分回滚
    assert db.query(SkuMapping).count() == 0


def test_batch_upsert_updates_existing_row(db_session):
    db = db_session
    s_old = _sku(db, "S1", "旧商品")
    _mapping(db, "taobao", "P1", s_old.id, "exact", Decimal("1"))
    s_new = _sku(db, "S2", "新商品")
    batch_upsert(db, [{"platform_key": "taobao", "platform_product_code": "P1",
                       "sku_id": s_new.id}])
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.sku_id == s_new.id
    assert m.match_type == "manual"
    assert m.confidence == Decimal("1")


def test_batch_upsert_null_sku_keeps_confirmed_mapping(db_session):
    db = db_session
    s1 = _sku(db, "S1", "商品一")
    _mapping(db, "taobao", "P1", s1.id, "manual", Decimal("1"))
    # sku_id=None：已确认的映射不被重置
    batch_upsert(db, [{"platform_key": "taobao", "platform_product_code": "P1",
                       "sku_id": None}])
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.sku_id == s1.id and m.match_type == "manual"


def test_batch_upsert_null_sku_marks_unmapped_row_manual(db_session):
    db = db_session
    rows = [_row("P1", name="未知商品")]
    attach_sku_ids(db, rows, "taobao")
    batch_upsert(db, [{"platform_key": "taobao", "platform_product_code": "P1",
                       "sku_id": None}])
    m = db.query(SkuMapping).filter_by(platform_product_code="P1").one()
    assert m.sku_id is None
    assert m.match_type == "manual"
    assert m.confidence == Decimal("1")


# ---------------------------------------------------------------------------
# apply_suggestion：采纳建议 / 手工指定（PUT /api/sku-mappings/{id}）
# ---------------------------------------------------------------------------


def test_apply_suggestion_maps_and_backfills_facts(db_session):
    db = db_session
    sku = _sku(db, "S1", "商品一")
    up = _upload(db, "map001")
    _fact(db, up, "P1")  # 历史明细 sku_id 为 NULL
    m = _mapping(db, "taobao", "P1", None, "unmapped", 0)
    row = apply_suggestion(db, m.id, sku.id)
    assert row.sku_id == sku.id
    assert row.match_type == "manual"
    assert row.confidence == Decimal("1")
    fact = db.query(SalesFact).filter_by(platform_product_code="P1").one()
    assert fact.sku_id == sku.id  # 采纳后回填历史明细


def test_apply_suggestion_clear_keeps_history(db_session):
    db = db_session
    sku = _sku(db, "S1", "商品一")
    up = _upload(db, "map002")
    _fact(db, up, "P1")
    m = _mapping(db, "taobao", "P1", sku.id, "manual", Decimal("1"))
    row = apply_suggestion(db, m.id, None)
    assert row.sku_id is None
    assert row.match_type == "unmapped"
    assert row.confidence == 0
    fact = db.query(SalesFact).filter_by(platform_product_code="P1").one()
    assert fact.sku_id is None  # 清除映射不回填


def test_apply_suggestion_missing_mapping_raises(db_session):
    with pytest.raises(ValidationError):
        apply_suggestion(db_session, 99999, None)


def test_apply_suggestion_missing_sku_raises(db_session):
    db = db_session
    m = _mapping(db, "taobao", "P1", None, "unmapped", 0)
    with pytest.raises(SkuNotFoundError):
        apply_suggestion(db, m.id, 99999)


# ---------------------------------------------------------------------------
# sku_reference_count：删除确认引用统计
# ---------------------------------------------------------------------------


def test_sku_reference_count(db_session):
    db = db_session
    s1 = _sku(db, "S1", "商品一")
    s2 = _sku(db, "S2", "商品二")
    up = _upload(db, "ref001")
    _fact(db, up, "P1", sku_id=s1.id)
    _fact(db, up, "P2", sku_id=s1.id)
    _fact(db, up, "P3", sku_id=s2.id)
    _mapping(db, "taobao", "P1", s1.id, "manual", Decimal("1"))
    _mapping(db, "jd", "J1", s2.id, "manual", Decimal("1"))
    assert sku_reference_count(db, s1.id) == {"sales_facts": 2, "mappings": 1}
    assert sku_reference_count(db, s2.id) == {"sales_facts": 1, "mappings": 1}
    assert sku_reference_count(db, 99999) == {"sales_facts": 0, "mappings": 0}
