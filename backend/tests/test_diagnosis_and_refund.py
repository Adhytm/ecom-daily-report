"""诊断归因与 7 日滚动退款率测试。

验证两项核心业务改进：
1. 7 日滚动综合退款率（refund_rate_7d）：大促后突发单日退款不导致整体退款率失真
2. 杜邦三因子增量归因（DuPont / LMDI）：将 GMV 环比波动精准拆解为访客、转化率、客单价三项贡献
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.core.aggregator import (
    _decompose_gmv_dod,
    build_daily_report,
)
from backend.app.models.entities import Upload
from backend.tests.test_aggregator import _insert_fact

D0 = date(2026, 9, 6)


def test_dupond_lmdi_decomposition_math():
    """测试三因子拆解纯数学逻辑：流量腰斩拖累 GMV 下滑。"""
    # 昨天：1000 访客, 50 买家(转化5%), 成交 5000(客单价100)
    # 今天：500 访客(跌50%), 25 买家(转化5%), 成交 2500(客单价100) -> GMV 跌 50%
    y_m = {
        "gmv": Decimal("5000"),
        "visitors": Decimal("1000"),
        "buyers": Decimal("50"),
    }
    t_m = {
        "gmv": Decimal("2500"),
        "visitors": Decimal("500"),
        "buyers": Decimal("25"),
    }
    diag = _decompose_gmv_dod(t_m, y_m)
    assert diag is not None
    assert diag["primary_driver"] == "visitors"
    assert "访客流量" in diag["driver_label"]
    # 访客贡献应接近 -50%，转化与客单价贡献接近 0
    assert diag["contributions"]["visitors"] == pytest.approx(-0.5, abs=0.01)
    assert diag["contributions"]["conversion_rate"] == pytest.approx(0.0, abs=0.01)
    assert diag["contributions"]["avg_order_value"] == pytest.approx(0.0, abs=0.01)
    assert "主要由商品访客数减少" in diag["explanation"]


def test_rolling_7d_refund_rate(db_session):
    """测试 7 日滚动退款率：
    过去 6 天共 GMV 6000，退款 600；
    今天 GMV 骤降到 100，但突发退款 200（单日退款率 200%）。
    7 日滚动退款率应为 (600 + 200) / (6000 + 100) = 800 / 6100 ≈ 13.11%，不会被单日失真破坏。
    """
    up = Upload(filename="t_refund.csv", sha256="ref001", platform_key="taobao", status="committed")
    db_session.add(up)
    db_session.flush()

    # 填充前 6 天数据 (每天 GMV 1000, 退款 100)
    for i in range(1, 7):
        d = D0 - timedelta(days=i)
        _insert_fact(db_session, up.id, d, "taobao", f"P{i}", gmv="1000", refund="100",
                     visitors=200, buyers=20, paid_qty=20)

    # 今天数据：GMV 100, 退款 200 (突发单日退款 > GMV)
    _insert_fact(db_session, up.id, D0, "taobao", "P0", gmv="100", refund="200",
                 visitors=50, buyers=2, paid_qty=2)
    db_session.commit()

    report = build_daily_report(db_session, D0)
    ov = report["overview"]

    # 单日退款率 200%
    assert ov["refund_rate"]["value"] == 2.0
    # 7 日滚动退款率约 13.11% (0.1311)
    assert "refund_rate_7d" in ov
    assert ov["refund_rate_7d"]["value"] == pytest.approx(800 / 6100, abs=1e-3)

    # 检查诊断字段是否存在
    assert "diagnosis" in report
    diag = report["diagnosis"]
    assert diag is not None
    assert diag["primary_driver"] in ("visitors", "conversion_rate", "avg_order_value", "stable")
