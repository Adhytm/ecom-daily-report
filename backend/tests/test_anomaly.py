"""异常预警规则引擎测试（T09）：默认规则幂等、min_base、abs_gt、severity。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core.anomaly import evaluate_rule, evaluate_rules
from backend.app.models.entities import AnomalyRule


def _rule(**overrides) -> AnomalyRule:
    base = dict(
        name="测试规则", metric="refund_rate", scope="sku", operator="gt",
        threshold=Decimal("0.15"), min_base=Decimal("0"), enabled=True,
    )
    base.update(overrides)
    return AnomalyRule(**base)


def test_gt_triggered_and_severity_warning():
    rule = _rule()
    result = evaluate_rule(rule, "SKU1 商品A", {"refund_rate": Decimal("0.20"), "gmv": Decimal("1000")})
    assert result is not None
    assert result["severity"] == "warning"  # (0.20-0.15)/0.15 = 0.33 < 1
    assert result["message"].endswith("高于阈值 15.0%")


def test_gt_severity_critical():
    rule = _rule()
    # (0.35-0.15)/0.15 = 1.33 > 1 → critical
    result = evaluate_rule(rule, "S", {"refund_rate": Decimal("0.35"), "gmv": Decimal("1000")})
    assert result["severity"] == "critical"


def test_not_triggered():
    rule = _rule()
    assert evaluate_rule(rule, "S", {"refund_rate": Decimal("0.10"), "gmv": Decimal("1000")}) is None


def test_min_base_blocks_small_sample():
    rule = _rule(min_base=Decimal("500"))
    assert evaluate_rule(rule, "S", {"refund_rate": Decimal("0.50"), "gmv": Decimal("499")}) is None
    assert evaluate_rule(rule, "S", {"refund_rate": Decimal("0.50"), "gmv": Decimal("500")}) is not None


def test_lt_operator():
    rule = _rule(metric="roi", operator="lt", threshold=Decimal("1.0"))
    assert evaluate_rule(rule, "S", {"roi": Decimal("0.5"), "gmv": Decimal("900")}) is not None
    assert evaluate_rule(rule, "S", {"roi": Decimal("2.0"), "gmv": Decimal("900")}) is None


def test_abs_gt_operator():
    rule = _rule(metric="gmv_dod", operator="abs_gt", threshold=Decimal("0.30"))
    assert evaluate_rule(rule, "S", {"gmv_dod": -0.5, "gmv": Decimal("2000")}) is not None
    assert evaluate_rule(rule, "S", {"gmv_dod": 0.35, "gmv": Decimal("2000")}) is not None
    assert evaluate_rule(rule, "S", {"gmv_dod": 0.2, "gmv": Decimal("2000")}) is None


def test_null_value_no_trigger():
    rule = _rule()
    assert evaluate_rule(rule, "S", {"refund_rate": None, "gmv": Decimal("1000")}) is None


def test_conversion_rate_dod_pp():
    rule = _rule(name="转化率异常下滑", metric="conversion_rate_dod_pp",
                 operator="lt", threshold=Decimal("-2.0"))
    result = evaluate_rule(rule, "平台A", {"conversion_rate_dod_pp": -3.2, "gmv": Decimal("2000")})
    assert result is not None
    assert "pp" in result["message"]
    assert result["value"] == -3.2


def test_evaluate_rules_multi_scope():
    rules = [
        _rule(name="r1", scope="platform"),
        _rule(name="r2", scope="sku", threshold=Decimal("0.10")),
    ]
    context = {
        "platform": {"淘宝": {"refund_rate": Decimal("0.30"), "gmv": Decimal("5000")}},
        "sku": {
            "SKU1 甲": {"refund_rate": Decimal("0.20"), "gmv": Decimal("800")},
            "SKU2 乙": {"refund_rate": Decimal("0.05"), "gmv": Decimal("800")},
        },
    }
    anomalies = evaluate_rules(rules, context)
    subjects = {(a["rule_name"], a["subject"]) for a in anomalies}
    assert ("r1", "淘宝") in subjects
    assert ("r2", "SKU1 甲") in subjects
    assert ("r2", "SKU2 乙") not in subjects


def test_disabled_rule_skipped():
    rules = [_rule(enabled=False)]
    context = {"sku": {"S": {"refund_rate": Decimal("0.99"), "gmv": Decimal("1000")}}}
    assert evaluate_rules(rules, context) == []


def test_unknown_metric_ignored():
    rule = _rule(metric="not_a_metric")
    assert evaluate_rule(rule, "S", {"not_a_metric": 1, "gmv": Decimal("1")}) is None


# ---------------------------------------------------------------------------
# 默认规则种子：幂等插入
# ---------------------------------------------------------------------------


def test_default_rules_seeded(db_session):
    from backend.app.db import seed_default_anomaly_rules

    seed_default_anomaly_rules(db_session)
    db_session.commit()
    names = {r.name for r in db_session.query(AnomalyRule.name).all()}
    assert len(names) == 6
    # 再次插入仍为 6 条（幂等）
    seed_default_anomaly_rules(db_session)
    db_session.commit()
    assert db_session.query(AnomalyRule).count() == 6

    jd_rule = (
        db_session.query(AnomalyRule).filter(AnomalyRule.name == "GMV 环比大幅波动").one()
    )
    assert jd_rule.metric == "gmv_dod"
    assert jd_rule.scope == "platform"
    assert jd_rule.operator == "abs_gt"
    assert jd_rule.threshold == Decimal("0.30")
    assert jd_rule.min_base == Decimal("1000")


# ---------------------------------------------------------------------------
# 与 T06 模拟数据联跑：命中 >= 4 条不同规则（集成，mock 文件缺失则跳过）
# ---------------------------------------------------------------------------


def test_mock_data_hits_at_least_4_rules(db_engine, monkeypatch, tmp_path):
    from pathlib import Path

    mock_dir = Path(__file__).resolve().parents[2] / "samples" / "mock"
    if not (mock_dir / "manifest.json").is_file():
        pytest.skip("samples/mock 未生成，先运行 gen_mock_data")

    from sqlalchemy.orm import sessionmaker

    from backend.app.models.entities import Sku
    from backend.app.services.upload_service import commit_upload, ingest_file
    from backend.app.core.aggregator import build_daily_report

    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        monkeypatch.setattr(
            "backend.app.services.upload_service._UPLOADS_DIR", tmp_path / "uploads"
        )
        manifest = __import__("json").loads(
            (mock_dir / "manifest.json").read_text(encoding="utf-8")
        )
        # 先建内部 SKU 目录（共享 SKU 供模糊匹配）
        for s in manifest["internal_skus"]:
            db.add(Sku(sku_code=s["sku_code"], name=s["name"], category=s["category"]))
        db.commit()

        for platform_key, fname in manifest["files"].items():
            raw = (mock_dir / fname).read_bytes()
            ingest_file(db, fname, raw, platform_key=platform_key)
        # 取最新 4 条 parsed 上传并提交
        from backend.app.models.entities import Upload

        uploads = db.query(Upload).filter(Upload.status == "parsed").all()
        for u in uploads:
            commit_upload(db, u.id)

        # 模拟运营采纳高置信（>=90 分）模糊匹配建议
        from backend.app.core.sku_mapper import fuzzy_candidates
        from backend.app.models.entities import SkuMapping

        for m in db.query(SkuMapping).filter(SkuMapping.sku_id.is_(None)).all():
            candidates = fuzzy_candidates(db, m.platform_product_name, None, limit=1)
            if candidates and candidates[0].score >= 90:
                m.sku_id = candidates[0].sku.id
                m.match_type = "manual"
                m.confidence = 1.0
        # autoflush=False：先落盘采纳结果，回填查询才能看到
        db.flush()
        # 采纳后回填历史明细
        for m in db.query(SkuMapping).filter(SkuMapping.sku_id.isnot(None)).all():
            from backend.app.models.entities import SalesFact

            db.query(SalesFact).filter(
                SalesFact.platform_key == m.platform_key,
                SalesFact.platform_product_code == m.platform_product_code,
            ).update({"sku_id": m.sku_id}, synchronize_session=False)
        db.commit()

        report_date = __import__("datetime").date.fromisoformat(manifest["report_date"])
        report = build_daily_report(db, report_date)

    rule_names = {a["rule_name"] for a in report["anomalies"]}
    assert len(rule_names) >= 4, f"仅命中 {rule_names}，需至少 4 条不同规则"
