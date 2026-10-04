"""聚合引擎测试（T08）：契约结构、环比/周同比、pp 差值、除零、SQL 次数。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from backend.app.core import NoDataForDateError
from backend.app.core.aggregator import build_daily_report
from backend.app.models.entities import SalesFact, Upload

D0 = date(2026, 9, 6)  # 周日
D1 = D0 - timedelta(days=1)
D2 = D0 - timedelta(days=7)


def _insert_fact(db, upload_id, stat_date, platform, code, *, gmv, refund=None, net=None,
                 paid_qty=1, buyers=1, visitors=1, ad_cost=None, sku_id=None,
                 shop_code="S1", shop_name="店铺一", category="美妆", product_name=None):
    gmv_dec = Decimal(str(gmv))
    refund_dec = Decimal(str(refund)) if refund is not None else None
    if net is None:
        net = gmv_dec - (refund_dec or Decimal("0"))
    db.add(
        SalesFact(
            upload_id=upload_id,
            stat_date=stat_date,
            platform_key=platform,
            shop_code=shop_code,
            shop_name=shop_name,
            platform_product_code=code,
            product_name=product_name or f"商品{code}",
            category=category,
            sku_id=sku_id,
            order_qty=paid_qty,
            paid_qty=paid_qty,
            gmv=gmv_dec,
            refund_amount=refund_dec,
            net_amount=Decimal(str(net)),
            visitors=visitors,
            buyers=buyers,
            ad_cost=Decimal(str(ad_cost)) if ad_cost is not None else None,
            raw_row_json={"x": "1"},
        )
    )


@pytest.fixture()
def known_dataset(db_session):
    """构造可精确断言的已知数据集：
    - 平台 A：今日 gmv 1000/退款 100；昨日 gmv 800；上周同日 gmv 500
    - 平台 B：今日 gmv 0 退款 50（退款率除不尽→None 的边界不涉及），
      昨日无数据（环比 None）
    - 平台 C：仅上周有数据（今日缺失，不出现于 by_platform）
    """
    up = Upload(filename="t.csv", sha256="agg001", platform_key="taobao", status="committed")
    db_session.add(up)
    db_session.flush()

    sku = None  # 未映射场景单独测
    # 平台 A
    _insert_fact(db_session, up.id, D0, "taobao", "A1", gmv="1000", refund="100",
                 paid_qty=10, buyers=8, visitors=200, ad_cost="50", sku_id=None)
    _insert_fact(db_session, up.id, D1, "taobao", "A1", gmv="800", refund="80",
                 paid_qty=8, buyers=6, visitors=160)
    _insert_fact(db_session, up.id, D2, "taobao", "A1", gmv="500", refund="50",
                 paid_qty=5, buyers=4, visitors=100)
    # 平台 B：今日有数据，昨日缺失
    _insert_fact(db_session, up.id, D0, "doudian", "B1", gmv="300", refund="0",
                 paid_qty=3, buyers=3, visitors=60, shop_code="S2", shop_name="店铺二",
                 category="食品")
    # 平台 C：只有上周数据
    _insert_fact(db_session, up.id, D2, "pinduoduo", "C1", gmv="999")
    db_session.commit()
    return up.id


def test_contract_structure(known_dataset, db_session):
    report = build_daily_report(db_session, D0)
    for key in ("report_date", "weekday", "compare_dates", "data_completeness",
                "overview", "by_platform", "by_shop", "by_category", "by_sku",
                "trend", "top", "anomalies"):
        assert key in report, f"契约缺少字段 {key}"
    assert report["weekday"] == "周日"
    assert report["compare_dates"] == {"dod": D1.isoformat(), "wow": D2.isoformat()}
    assert set(report["trend"]["dates"]) and len(report["trend"]["dates"]) == 30


def test_overview_exact_values(known_dataset, db_session):
    report = build_daily_report(db_session, D0)
    ov = report["overview"]
    # 今日总 gmv = 1300；今日退款 = 100；net = 1300 - 100 = 1200
    assert ov["gmv"]["value"] == 1300.0
    assert ov["net_amount"]["value"] == 1200.0
    assert ov["refund_amount"]["value"] == 100.0
    # 环比：昨日 gmv 800 → (1300-800)/800 = 0.625
    assert ov["gmv"]["dod"] == pytest.approx(0.625)
    # 周同比：上周同日 gmv 1499 → (1300-1499)/1499 = -0.13276（round4 → -0.1328）
    assert ov["gmv"]["wow"] == pytest.approx(-0.1328, abs=1e-3)
    # 退款率 = 100/1300 = 0.07692...（round4）
    assert ov["refund_rate"]["value"] == pytest.approx(0.0769, abs=1e-4)
    # 转化率 = buyers(11) / visitors(260)
    assert ov["conversion_rate"]["value"] == pytest.approx(11 / 260, abs=1e-4)
    # 客单价 = 1200 / 11
    assert ov["avg_order_value"]["value"] == pytest.approx(109.09, abs=0.01)


def test_ratio_dod_is_pp(known_dataset, db_session):
    """比率类环比为百分点差值而非百分比变化。"""
    report = build_daily_report(db_session, D0)
    ov = report["overview"]
    # 今日退款率 100/1300=0.07692；昨日 80/800=0.1 → -2.308pp
    assert ov["refund_rate"]["dod_pp"] == pytest.approx(-2.3077, abs=1e-3)
    # 转化率今日 11/260=0.04231；昨日 6/160=0.0375 → +0.481pp
    assert ov["conversion_rate"]["dod_pp"] == pytest.approx(0.481, abs=1e-2)


def test_missing_base_returns_none(known_dataset, db_session):
    """平台 B 昨日无数据 → 环比 None；昨日为 0 → None（不能除零）。"""
    report = build_daily_report(db_session, D0)
    by_platform = {r["key"]: r for r in report["by_platform"]}
    assert by_platform["doudian"]["metrics"]["gmv"]["dod"] is None
    assert by_platform["doudian"]["metrics"]["gmv"]["value"] == 300.0


def test_data_completeness(known_dataset, db_session):
    report = build_daily_report(db_session, D0)
    dc = report["data_completeness"]
    assert "taobao" in dc["present_platforms"]
    assert "doudian" in dc["present_platforms"]
    assert "jd" in dc["missing_platforms"]
    assert dc["warning"] and "缺失" in dc["warning"]


def test_platform_share_and_sort(known_dataset, db_session):
    report = build_daily_report(db_session, D0)
    # 平台按 net_amount 降序：taobao 900 > doudian 300
    keys = [r["key"] for r in report["by_platform"]]
    assert keys == ["taobao", "doudian"]
    shares = {r["key"]: r["share"] for r in report["by_platform"]}
    assert shares["taobao"] == pytest.approx(900 / 1200, abs=1e-3)


def test_unmapped_grouped(known_dataset, db_session):
    """全部 SKU 未映射 → by_sku.unmapped 有分组，mapped 为空。"""
    report = build_daily_report(db_session, D0)
    assert report["by_sku"]["mapped"] == []
    unmapped_codes = {r["platform_product_code"] for r in report["by_sku"]["unmapped"]}
    assert {"A1", "B1"} <= unmapped_codes


def test_mapped_sku_rows(db_session):
    up = Upload(filename="t2.csv", sha256="agg002", status="committed")
    db_session.add(up)
    db_session.flush()
    from backend.app.models.entities import Sku

    sku = Sku(sku_code="SKU9001", name="已知商品", category="美妆")
    db_session.add(sku)
    db_session.flush()
    _insert_fact(db_session, up.id, D0, "taobao", "SKU9001", gmv="600", refund="60",
                 sku_id=sku.id)
    db_session.commit()

    report = build_daily_report(db_session, D0)
    mapped = report["by_sku"]["mapped"]
    assert len(mapped) == 1
    assert mapped[0]["sku_code"] == "SKU9001"
    assert mapped[0]["name"] == "已知商品"
    assert mapped[0]["metrics"]["gmv"]["value"] == 600.0


def test_no_data_for_date(db_session):
    with pytest.raises(NoDataForDateError):
        build_daily_report(db_session, date(2026, 1, 1))


def test_zero_yesterday_no_divzero(db_session):
    """昨日 gmv=0 → 环比 None（不能出现 inf 或异常）。"""
    up = Upload(filename="t3.csv", sha256="agg003", status="committed")
    db_session.add(up)
    db_session.flush()
    _insert_fact(db_session, up.id, D0, "taobao", "Z1", gmv="500")
    _insert_fact(db_session, up.id, D1, "taobao", "Z1", gmv="0")
    db_session.commit()
    report = build_daily_report(db_session, D0)
    assert report["overview"]["gmv"]["dod"] is None


def test_refund_gt_gmv_no_crash(db_session):
    """退款额 > GMV 的异常数据：正常计算，退款率 > 100%。"""
    up = Upload(filename="t4.csv", sha256="agg004", status="committed")
    db_session.add(up)
    db_session.flush()
    _insert_fact(db_session, up.id, D0, "taobao", "R1", gmv="100", refund="150")
    db_session.commit()
    report = build_daily_report(db_session, D0)
    assert report["overview"]["refund_rate"]["value"] > 1.0


def test_sql_query_count(db_session):
    """SQL 查询次数 <= 3（规划 6.4 单次查询 + 名称 + 规则）。"""
    up = Upload(filename="t5.csv", sha256="agg005", status="committed")
    db_session.add(up)
    db_session.flush()
    _insert_fact(db_session, up.id, D0, "taobao", "Q1", gmv="100")
    db_session.commit()

    count = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        count["n"] += 1

    event.listen(db_session.bind, "before_cursor_execute", _count)
    try:
        build_daily_report(db_session, D0)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _count)
    assert count["n"] <= 3, f"SQL 次数 {count['n']} 超过 3"


def test_scope_filter(known_dataset, db_session):
    report = build_daily_report(db_session, D0, scope={"platforms": ["taobao"]})
    assert [r["key"] for r in report["by_platform"]] == ["taobao"]
    assert report["overview"]["gmv"]["value"] == 1000.0


def test_trend_series(known_dataset, db_session):
    report = build_daily_report(db_session, D0)
    dates = report["trend"]["dates"]
    gmv = report["trend"]["series"]["gmv"]
    assert len(dates) == len(gmv) == 30
    idx = dates.index(D0.isoformat())
    assert gmv[idx] == 1300.0
    # 无数据日为 None
    empty_idx = dates.index((D0 - timedelta(days=20)).isoformat())
    assert gmv[empty_idx] is None


def test_jd_shipping_note(db_session):
    up = Upload(filename="t6.csv", sha256="agg006", status="committed")
    db_session.add(up)
    db_session.flush()
    _insert_fact(db_session, up.id, D0, "jd", "J1", gmv="800")
    db_session.commit()
    report = build_daily_report(db_session, D0)
    assert "含运费" in report["overview"]["note"]
