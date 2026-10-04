"""文字摘要测试（T11）：模板逐字符匹配、万元简写、emoji、三级裁剪。"""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.app.exporters.text_summary import generate_text_summary


def _base_report() -> dict:
    """构造可逐字符断言的最小报告。"""
    def m(value, dod=None, wow=None, dod_pp=None, wow_pp=None):
        return {"value": value, "dod": dod, "wow": wow, "dod_pp": dod_pp, "wow_pp": wow_pp}

    return {
        "report_date": "2026-09-06",
        "weekday": "周日",
        "overview": {
            "gmv": m(12345.0, dod=0.123, wow=-0.045),
            "net_amount": m(11000.0),
            "refund_amount": m(1345.0),
            "refund_rate": m(0.109, dod_pp=1.2, wow_pp=-0.5),
            "paid_qty": m(100),
            "buyers": m(80),
            "visitors": m(1000),
            "conversion_rate": m(0.08, dod_pp=-0.3),
            "avg_order_value": m(137.5),
            "ad_cost": m(500.0),
            "roi": m(3.2),
        },
        "by_platform": [
            {"key": "taobao", "name": "淘宝/天猫",
             "metrics": {"net_amount": m(9000.0), "gmv": m(9000.0, dod=0.1)}, "share": 0.8},
            {"key": "jd", "name": "京东（京东商智）",
             "metrics": {"net_amount": m(2000.0), "gmv": m(2000.0, dod=None)}, "share": 0.2},
        ],
        "top": {
            "by_gmv": [
                {"sku_code": "SKU0001", "name": "商品甲", "net_amount": 5000.0, "gmv_dod": 0.08},
                {"sku_code": "SKU0002", "name": "商品乙", "net_amount": 3000.0, "gmv_dod": None},
                {"sku_code": "SKU0003", "name": "商品丙", "net_amount": 1000.0, "gmv_dod": -0.02},
            ]
        },
        "anomalies": [
            {"rule_name": "SKU GMV 环比骤降", "severity": "warning", "scope": "sku",
             "subject": "SKU0001 商品甲", "metric": "gmv_dod", "value": -0.55,
             "threshold": -0.4, "message": "SKU0001 商品甲 GMV 环比 -55.0%，低于阈值 -40.0%"}
        ],
        "data_completeness": {
            "expected_platforms": [], "present_platforms": [],
            "missing_platforms": [], "warning": None,
        },
    }


EXPECTED_TEMPLATE = """【测试公司电商销售日报】2026-09-06 周日

◆ 整体
GMV ¥12,345.00｜环比 +12.3%｜周同比 -4.5%
实际成交 ¥11,000.00｜退款率 10.9%｜支付件数 100｜买家数 80
客单价 ¥137.50｜转化率 8.0%｜推广ROI 3.2

◆ 分平台
· 淘宝/天猫 ¥9,000.00（环比 +10.0%）
· 京东（京东商智） ¥2,000.00（环比 —）

◆ TOP3 单品
1. 商品甲 ¥5,000.00（环比 +8.0%）
2. 商品乙 ¥3,000.00（环比 —）
3. 商品丙 ¥1,000.00（环比 -2.0%）

◆ 需关注
· [⚠️] SKU0001 商品甲 GMV 环比 -55.0%，低于阈值 -40.0%

—— 由 电商日报工具 自动生成 2026-09-06 20:30"""


@pytest.fixture()
def no_wan(monkeypatch):
    monkeypatch.setattr("backend.app.config.settings.summary_use_wan", False)
    monkeypatch.setattr("backend.app.config.settings.company_name", "测试公司")


def test_template_exact_match(no_wan):
    fixed_at = datetime(2026, 9, 6, 20, 30)
    text = generate_text_summary(_base_report(), company="测试公司", generated_at=fixed_at)
    assert text == EXPECTED_TEMPLATE


def test_none_values_show_dash(no_wan):
    report = _base_report()
    report["overview"]["gmv"]["dod"] = None
    report["overview"]["roi"] = {"value": None, "dod": None, "wow": None,
                                 "dod_pp": None, "wow_pp": None}
    fixed_at = datetime(2026, 9, 6, 20, 30)
    text = generate_text_summary(report, company="测试公司", generated_at=fixed_at)
    assert "环比 —" in text
    assert "推广ROI —" in text


def test_wan_shorthand(monkeypatch):
    monkeypatch.setattr("backend.app.config.settings.summary_use_wan", True)
    report = _base_report()
    report["overview"]["gmv"]["value"] = 123456.78
    fixed_at = datetime(2026, 9, 6, 20, 30)
    text = generate_text_summary(report, company="XX", generated_at=fixed_at)
    assert "GMV ¥12.35万" in text
    # 不足一万仍用完整格式
    assert "客单价 ¥137.50" in text


def test_critical_emoji(no_wan):
    report = _base_report()
    report["anomalies"][0]["severity"] = "critical"
    text = generate_text_summary(report, company="X",
                                 generated_at=datetime(2026, 9, 6, 20, 30))
    assert "· [🔴]" in text


def test_no_anomaly_line(no_wan):
    report = _base_report()
    report["anomalies"] = []
    text = generate_text_summary(report, company="X",
                                 generated_at=datetime(2026, 9, 6, 20, 30))
    assert "· 本日无异常预警" in text


def test_missing_platform_warning(no_wan):
    report = _base_report()
    report["data_completeness"]["warning"] = "缺失 2 个平台数据，合计值不完整"
    text = generate_text_summary(report, company="X",
                                 generated_at=datetime(2026, 9, 6, 20, 30))
    assert "缺失 2 个平台数据，合计值不完整" in text


def test_platform_anomaly_mark(no_wan):
    report = _base_report()
    report["anomalies"].append(
        {"rule_name": "推广 ROI 过低", "severity": "critical", "scope": "platform",
         "subject": "淘宝/天猫", "metric": "roi", "value": 0.5,
         "threshold": 1.0, "message": "淘宝/天猫 推广 ROI 过低"}
    )
    text = generate_text_summary(report, company="X",
                                 generated_at=datetime(2026, 9, 6, 20, 30))
    assert "（环比 +10.0%）🔴" in text


def _huge_report() -> dict:
    """构造超长场景：大量异常 + 大量平台 + 长 TOP 榜。"""
    report = _base_report()
    report["by_platform"] = [
        {"key": f"p{i}", "name": f"平台{i:02d}超长名称超长名称",
         "metrics": {"net_amount": {"value": 9000.0 - i},
                     "gmv": {"value": 9000.0 - i, "dod": 0.1 - i * 0.01}},
         "share": 0.1}
        for i in range(12)
    ]
    report["top"]["by_gmv"] = [
        {"sku_code": f"SKU{i:04d}",
         "name": "超长商品名称" * 5 + str(i),
         "net_amount": 9000.0 - i, "gmv_dod": 0.5 - i * 0.01}
        for i in range(10)
    ]
    report["anomalies"] = [
        {"rule_name": f"规则{i}", "severity": "warning" if i % 2 else "critical",
         "scope": "sku", "subject": f"SKU{i:04d}", "metric": "gmv_dod",
         "value": -0.5, "threshold": -0.4,
         "message": f"SKU{i:04d} 商品名称非常长非常长 GMV 环比 -55.0%，低于阈值 -40.0%"}
        for i in range(30)
    ]
    return report


def test_length_limit_and_trim_order(no_wan):
    """超 800 字符时三级裁剪：先裁 TOP3 → 再分平台留 4 → 最后只留 critical。"""
    fixed_at = datetime(2026, 9, 6, 20, 30)
    text = generate_text_summary(_huge_report(), company="X", generated_at=fixed_at)
    assert len(text) <= 800
    # 三级裁剪后：TOP3 整段消失（含标题）、分平台只留 4、需关注只剩 critical
    assert "◆ TOP3 单品" not in text
    platform_lines = [l for l in text.splitlines() if l.startswith("· 平台")]
    assert len(platform_lines) == 4
    # 只剩 critical：warning 的 ⚠️ 标记不应出现（直接断言字面 "warning" 恒真，
    # 因为模板输出的是 emoji 而非英文单词，起不到守卫作用）
    assert "⚠️" not in text
    # critical 异常仍在
    assert "[🔴]" in text
