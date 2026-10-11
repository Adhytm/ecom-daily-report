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


def test_build_diagnosis_context_with_anomalies_and_top_items():
    from backend.app.schemas.report import DailyReportData
    from backend.app.services.ai_service import build_diagnosis_context

    raw_data = {
        "report_date": "2026-09-02",
        "weekday": "周三",
        "compare_dates": {"yesterday": "2026-09-01"},
        "data_completeness": {"expected_platforms": ["taobao"], "present_platforms": ["taobao"], "cost_coverage": 1.0},
        "overview": {
            "gmv": {"value": 10000.0, "dod": 0.12},
            "net_amount": {"value": 9000.0, "dod": 0.10},
            "refund_rate": {"value": 0.10, "dod_pp": 0.5},
            "gross_profit": {"value": 5000.0},
            "gross_margin": {"value": 0.55},
            "operating_profit": {"value": 4500.0},
            "paid_qty": {"value": 100.0},
            "avg_order_value": {"value": 90.0},
            "visitors": {"value": 2000.0},
            "conversion_rate": {"value": 0.05},
            "roi": {"value": 9.0},
        },
        "by_platform": [
            {
                "key": "taobao",
                "name": "天猫淘宝",
                "share": 1.0,
                "metrics": {"gmv": {"value": 10000.0, "dod": 0.12}, "refund_rate": {"value": 0.10}},
            }
        ],
        "by_shop": [],
        "by_category": [],
        "by_sku": {"mapped": [], "unmapped": []},
        "trend": {"dates": [], "series": {}},
        "top": {
            "by_gmv": [],
            "by_growth": [
                {"name": "热销面霜", "sku_code": "SKU01", "gmv": 5000.0, "gmv_dod": 0.35, "net_amount": 4500.0, "refund_rate": 0.1}
            ],
            "by_decline": [
                {"name": "滞销眼影", "sku_code": "SKU02", "gmv": 1200.0, "gmv_dod": -0.45, "net_amount": 1000.0, "refund_rate": 0.15}
            ],
            "by_refund": [
                {"name": "高退口红", "sku_code": "SKU03", "gmv": 800.0, "refund_rate": 0.28, "net_amount": 576.0, "gmv_dod": 0.05}
            ],
        },
        "anomalies": [
            {
                "rule_name": "大盘退款率超标",
                "severity": "critical",
                "scope": "overview",
                "subject": "全盘",
                "metric": "refund_rate",
                "value": 0.25,
                "threshold": 0.15,
                "message": "大盘退款率达 25.0%，超过阈值 15.0%",
            }
        ],
        "diagnosis": {
            "primary_driver": "visitors",
            "driver_label": "访客规模变动",
            "explanation": "访客下降是主要驱动因素",
            "contributions": {},
        },
    }
    report_data = DailyReportData.model_validate(raw_data)

    ctx = build_diagnosis_context(report_data)
    assert "热销面霜: GMV 5,000 元, 环比 +35.0%" in ctx
    assert "滞销眼影: GMV 1,200 元, 环比 -45.0%" in ctx
    assert "高退口红: 退款率 28.0%, GMV 800 元" in ctx
    assert "[CRITICAL] 大盘退款率超标" in ctx
    assert "当前值: 0.25, 阈值: 0.15" in ctx

