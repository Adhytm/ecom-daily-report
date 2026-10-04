"""契约健壮性测试：NULL 维度值不得让看板接口 500。

回归背景（2026-10-04 现网 500）：
`_load_facts` 用 `pd.DataFrame.from_records` 建 DataFrame，含 NULL 的字符串列
会被推断成 float64，NULL 变成**浮点 NaN**。而 `k["category"] or "未分类"` 这类
`or` 兜底对 NaN 无效（NaN 是真值），于是聚合出的 `name` 是 `nan`，
被 `DailyReportData` 的 `name: str` 拒绝 → `GET /api/reports/daily` 500。

这里直接以"类目/店铺为 NULL 的明细"入库，断言接口仍 200 且给出兜底文案。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.entities import SalesFact, Upload

_STAT_DATE = date(2026, 9, 6)


def _seed_null_dimension_facts(db) -> None:
    """两条明细：一条类目/店铺全 NULL，一条正常。"""
    up = Upload(
        filename="无类目明细.csv",
        sha256="0" * 64,
        platform_key="doudian",
        status="committed",
        row_count_total=2,
        row_count_valid=2,
        row_count_error=0,
    )
    db.add(up)
    db.flush()
    db.add_all(
        [
            SalesFact(
                upload_id=up.id,
                stat_date=_STAT_DATE,
                platform_key="doudian",
                shop_code=None,
                shop_name=None,
                platform_product_code="NO_CAT_1",
                product_name="没有类目的商品",
                category=None,
                sku_id=None,
                order_qty=None,
                paid_qty=3,
                gmv=Decimal("1200.00"),
                refund_amount=Decimal("0"),
                net_amount=Decimal("1200.00"),
                visitors=10,
                buyers=2,
                ad_cost=Decimal("0"),
                raw_row_json={},
            ),
            SalesFact(
                upload_id=up.id,
                stat_date=_STAT_DATE,
                platform_key="doudian",
                shop_code=None,
                shop_name="正常店",
                platform_product_code="OK_1",
                product_name="正常商品",
                category="美妆护肤",
                sku_id=None,
                order_qty=None,
                paid_qty=1,
                gmv=Decimal("100.00"),
                refund_amount=Decimal("0"),
                net_amount=Decimal("100.00"),
                visitors=5,
                buyers=1,
                ad_cost=Decimal("0"),
                raw_row_json={},
            ),
        ]
    )
    db.commit()


def test_daily_report_survives_null_category_and_shop(client, db_session):
    """NULL 类目/NULL 店铺 → 接口 200，且展示为兜底文案而不是 nan。"""
    _seed_null_dimension_facts(db_session)

    resp = client.get("/api/reports/daily", params={"date": "2026-09-06"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # 分平台里 doudian 的 GMV = 1200 + 100
    by_platform = {r["key"]: r for r in data["by_platform"]}
    assert Decimal(str(by_platform["doudian"]["metrics"]["gmv"]["value"])) == Decimal("1300.00")

    # 分K类目：NULL 归到「未分类」，正常类目保留
    cats = {r["name"]: r for r in data["by_category"]}
    assert "未分类" in cats, data["by_category"]
    assert "美妆护肤" in cats
    assert Decimal(str(cats["未分类"]["metrics"]["gmv"]["value"])) == Decimal("1200.00")
    # 契约要求 name 是字符串：任何一行都不许出现 nan / None
    for dim in ("by_platform", "by_shop", "by_category"):
        for row in data[dim]:
            assert isinstance(row["name"], str) and row["name"].strip(), (dim, row)
            assert row["name"].lower() != "nan", (dim, row)

    # 分店铺：NULL 店铺给兜底名，不出现 nan
    shops = [r["name"] for r in data["by_shop"]]
    assert any("未命名店铺" in s for s in shops), shops
    assert not any("nan" in s.lower() for s in shops), shops


def test_daily_report_dimension_keys_stay_strings(client, db_session):
    """维度 key 也必须是字符串（前端按 key 去重/排序）。"""
    _seed_null_dimension_facts(db_session)
    data = client.get("/api/reports/daily", params={"date": "2026-09-06"}).json()["data"]
    for dim in ("by_platform", "by_shop", "by_category"):
        for row in data[dim]:
            assert isinstance(row["key"], str) and row["key"], (dim, row)
