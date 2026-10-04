"""周报引擎与 AI 智能诊断接口测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.entities import SalesFact, Upload


def _seed_facts(db_session, stat_date: date, gmv: float = 1000.0):
    up = db_session.query(Upload).first()
    if not up:
        up = Upload(
            filename="mock.csv",
            sha256="test_sha256_mock_file",
            platform_key="taobao",
            status="committed",
            row_count_total=1,
            row_count_valid=1,
            row_count_error=0,
        )
        db_session.add(up)
        db_session.commit()

    fact = SalesFact(
        upload_id=up.id,
        stat_date=stat_date,
        platform_key="taobao",
        shop_name="测试天猫店",
        platform_product_code="TB001",
        product_name="测试热销眼霜",
        category="美妆",
        visitors=500,
        buyers=50,
        paid_qty=50,
        gmv=Decimal(str(gmv)),
        net_amount=Decimal(str(gmv * 0.9)),
        refund_amount=Decimal(str(gmv * 0.1)),
        ad_cost=Decimal(str(gmv * 0.2)),
        raw_row_json={},
    )
    db_session.add(fact)
    db_session.commit()
    return fact


def test_weekly_report_flow(client, db_session):
    # 注入本周 2026-09-01 (周二) 和 2026-09-02 (周三)
    _seed_facts(db_session, date(2026, 9, 1), 2000.0)
    _seed_facts(db_session, date(2026, 9, 2), 3000.0)

    # 1. 查询周报聚合数据
    resp = client.get("/api/reports/weekly?date=2026-09-02")
    assert resp.status_code == 200
    res = resp.json()
    assert res["ok"] is True
    data = res["data"]
    assert data["total_gmv"] == 5000.0
    assert len(data["daily_breakdown"]) == 7
    assert len(data["platforms_summary"]) >= 1
    assert len(data["top_skus"]) >= 1
    assert "周度复盘" in data["summary_text"]

    # 2. 查询周报摘要
    resp_sum = client.get("/api/reports/weekly/summary?date=2026-09-02")
    assert resp_sum.status_code == 200
    assert resp_sum.json()["data"]["char_count"] > 0

    # 3. 一键导出周报 Excel
    resp_exp = client.get("/api/reports/weekly/export?date=2026-09-02")
    assert resp_exp.status_code == 200
    assert "spreadsheetml" in resp_exp.headers.get("content-type", "")
    assert len(resp_exp.content) > 1000


def test_weekly_report_no_data(client):
    resp = client.get("/api/reports/weekly?date=2010-01-01")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_DATA_FOR_WEEK"


def test_daily_export_auto_generate(client, db_session):
    # 注入销售数据但故意不调用 generate
    d = date(2026, 9, 15)
    _seed_facts(db_session, d, 8888.0)

    # 直接请求导出 -> 应该触发自动即时生成，返回 200 而非 404
    resp = client.get("/api/reports/daily/2026-09-15/export")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers.get("content-type", "")


def test_ai_diagnose_cloud_requires_key(client):
    resp = client.post("/api/ai/diagnose", json={"date": "2026-09-02", "provider": "cloud"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AI_KEY_REQUIRED"
