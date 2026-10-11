"""回归测试：锁定代码审查发现的缺陷，防止再次退化。

每条用例都对应一个曾经真实存在、却被当时测试漏掉的 Bug。断言以
「旧实现的错误行为必然失败」为准，而不是仅验证新实现能跑通。

Bug 编号与修复内容：
    1  raw_row_json 溯源快照整体错位一行
    2  reparse 后再次 commit 导致销售明细翻倍
    3  lt + 负阈值时 severity 恒为 critical，warning 级失效
    4  聚合层不输出 unit_price，Excel「分类目」件单价列整列为空
    5  mock 生成接口在请求内新建锁，并发保护形同虚设
    6  归一化在「行几乎唯一」的真实形态下严重超时
    7  文件大小校验发生在全量读入内存之后
    8  全局异常处理向客户端泄漏内部细节
    9  整日只有 warning 时，超长裁剪把预警全部吞掉并谎报「本日无异常」
   10  TOP3 榜单为空或被裁掉时，摘要残留一个空标题
   11  固定种子的模拟数据生成受 PYTHONHASHSEED 影响，同 seed 重跑不可复现
   12  commit 落库时 to_db_dict 丢弃行上的 sku_id，精确匹配/查表命中
       的映射永远写不进 SalesFact（看板已映射恒空、SKU 级预警失效）
   13  批量映射接口只改 SkuMapping 不回填明细，看板【待映射】永不消掉
   14  幂等 commit 的 unmapped_count 按行数统计，与首次按商品码统计不一致
   15  compare=false 时 SKU 级"级别型"规则（退款率超限）被整体跳过
   16  「已确认」Tab 未按 sku_id 过滤，未映射行混进已确认列表
   17  PUT /skus 显式传 null 绕过校验，触发 NOT NULL 约束变 500
   18  generate 接口不校验 body，platforms 传字符串穿透到 pandas 变 500

其中 9、10 是 Bug3（severity 分级）修好后才暴露出来的二阶缺陷：
分级正确使得「整日全是 warning」成为常态，而裁剪逻辑原本假定
critical 总是存在。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.app.config import PROJECT_ROOT
from backend.app.core.aggregator import build_daily_report
from backend.app.core.anomaly import evaluate_rule
from backend.app.core.normalizer import normalize
from backend.app.exporters import text_summary as ts
from backend.app.models.entities import AnomalyRule, SalesFact, Sku, SkuMapping, Upload
from backend.tests.test_api import _row, _seed_facts, _taobao_csv
from backend.tests.test_normalizer import _df, _spec
from backend.tests.test_text_summary import _base_report


def _fact_count(db_session, upload_id: int) -> int:
    return (
        db_session.query(SalesFact)
        .filter(SalesFact.upload_id == upload_id)
        .count()
    )


# ---------------------------------------------------------------------------
# Bug 1 · 原始快照溯源必须与所在行严格对应
# ---------------------------------------------------------------------------


def test_raw_row_json_snapshot_matches_its_own_row(client, db_session):
    """中间行被过滤后，幸存行的快照不得错位到被剔除的那一行。

    旧实现用「去重/过滤后的位置序号」去索引原始记录，只要有任何一行被
    剔除，其后所有行的快照都会整体前移一位——CCC 会拿到 BBB 的原始数据。
    """
    rows = [
        _row("2026-09-06", "AAA", "111.11", name="第一个商品"),
        _row("2026-09-06", "BBB", "222.22", name="测试商品应被过滤"),
        _row("2026-09-06", "CCC", "333.33", name="第三个商品"),
    ]
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_商品效果_20260906.csv", _taobao_csv(rows), "text/csv")},
        data={"platform_key": "taobao"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]["files"][0]
    assert body["stats"]["total"] == 3
    assert body["stats"]["filtered"] == 1, "含「测试」的行应被 row_filters 剔除"
    assert body["stats"]["valid"] == 2

    client.post(f"/api/uploads/{body['upload_id']}/commit")
    facts = (
        db_session.query(SalesFact)
        .filter(SalesFact.upload_id == body["upload_id"])
        .order_by(SalesFact.platform_product_code)
        .all()
    )
    assert [f.platform_product_code for f in facts] == ["AAA", "CCC"]

    # 逐行核对快照：三个字段全部取自本行，而非相邻行
    expected = {
        "AAA": ("111.11", "第一个商品"),
        "CCC": ("333.33", "第三个商品"),
    }
    for f in facts:
        raw = f.raw_row_json
        gmv_text, name_text = expected[f.platform_product_code]
        assert raw["商品ID"] == f.platform_product_code
        assert raw["支付金额"] == gmv_text
        assert raw["商品名称"] == name_text
        assert raw["商品名称"] == f.product_name


# ---------------------------------------------------------------------------
# Bug 2 · reparse → commit 必须幂等，明细不得翻倍
# ---------------------------------------------------------------------------


def test_reparse_then_recommit_does_not_duplicate_facts(client, db_session):
    """reparse 会把状态回退为 parsed，旧实现在此之后 commit 会再插一遍明细。

    后果是同一份文件的销售数据在库里出现两遍，日报所有金额直接翻倍。
    """
    csv_bytes = _taobao_csv([
        _row("2026-09-06", "TB1001", "100"),
        _row("2026-09-06", "TB1002", "200", name="面膜"),
    ])
    resp = client.post(
        "/api/uploads",
        files={"files": ("t.csv", csv_bytes, "text/csv")},
        data={"platform_key": "taobao"},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]

    client.post(f"/api/uploads/{upload_id}/commit")
    assert _fact_count(db_session, upload_id) == 2

    # 改选平台重新解析后再次提交：明细仍应是 2 条，而非 4 条
    resp = client.post(f"/api/uploads/{upload_id}/reparse", json={"platform_key": "taobao"})
    assert resp.status_code == 200
    client.post(f"/api/uploads/{upload_id}/commit")
    assert _fact_count(db_session, upload_id) == 2

    # 继续提交也不得随次数累积
    client.post(f"/api/uploads/{upload_id}/commit")
    assert _fact_count(db_session, upload_id) == 2

    total = (
        db_session.query(SalesFact.gmv)
        .filter(SalesFact.upload_id == upload_id)
        .all()
    )
    assert sum(Decimal(str(v[0])) for v in total) == Decimal("300")


def test_commit_is_idempotent_after_status_reverts_to_parsed(client, db_session):
    """commit 自身的幂等不变量：只要状态回到 parsed，必须先清旧明细再写。

    上一条用例走 reparse 路径，而 reparse 自身也会清理明细，两处删除互相
    掩盖，单独抽掉 commit 里的那一处仍然能过。这里绕过 reparse 直接改
    状态，把 commit 内部的幂等删除隔离出来钉住。
    """
    csv_bytes = _taobao_csv([
        _row("2026-09-06", "TB1001", "100"),
        _row("2026-09-06", "TB1002", "200", name="面膜"),
    ])
    resp = client.post(
        "/api/uploads",
        files={"files": ("t.csv", csv_bytes, "text/csv")},
        data={"platform_key": "taobao"},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]
    client.post(f"/api/uploads/{upload_id}/commit")
    assert _fact_count(db_session, upload_id) == 2

    db_session.expire_all()
    upload = db_session.get(Upload, upload_id)
    upload.status = "parsed"
    db_session.commit()

    client.post(f"/api/uploads/{upload_id}/commit")
    assert _fact_count(db_session, upload_id) == 2, "commit 幂等删除失效，明细翻倍"


# ---------------------------------------------------------------------------
# Bug 3 · severity 分级对正负阈值都要成立
# ---------------------------------------------------------------------------


def _rule(**overrides) -> AnomalyRule:
    base = dict(
        name="SKU GMV 环比骤降", metric="gmv_dod", scope="sku", operator="lt",
        threshold=Decimal("-0.40"), min_base=Decimal("500"), enabled=True,
    )
    base.update(overrides)
    return AnomalyRule(**base)


@pytest.mark.parametrize(
    "value,expected",
    [
        (-0.41, "warning"),    # 越过量 0.01/0.40 = 0.025
        (-0.60, "warning"),    # 0.50
        (-0.79, "warning"),    # 0.975
        (-0.80, "warning"),    # 恰好 1.0，未超过
        (-0.81, "critical"),   # 1.025
        (-0.95, "critical"),   # 1.375
    ],
)
def test_severity_grading_with_negative_lt_threshold(value, expected):
    """阈值 -0.40 时，刚过线（-0.41）应为 warning，跌到两倍以内才 critical。

    旧实现用 abs(threshold) - value，负阈值下 -0.41 就得到 1.025，
    于是所有命中一律 critical，分级完全失效。
    """
    ctx = {"gmv_dod": Decimal(str(value)), "gmv": Decimal("10000")}
    result = evaluate_rule(_rule(), "SKU-1", ctx)
    assert result is not None, f"gmv_dod={value} 应已触发规则"
    assert result["severity"] == expected


@pytest.mark.parametrize(
    "value,expected",
    [(0.16, "warning"), (0.25, "warning"), (0.30, "warning"), (0.31, "critical")],
)
def test_severity_grading_with_positive_gt_threshold(value, expected):
    """对照组：gt + 正阈值的分级本来就正确，修 lt 时不得把它改坏。"""
    rule = _rule(
        name="退款率超限", metric="refund_rate", operator="gt",
        threshold=Decimal("0.15"), min_base=Decimal("500"),
    )
    ctx = {"refund_rate": Decimal(str(value)), "gmv": Decimal("10000")}
    result = evaluate_rule(rule, "SKU-1", ctx)
    assert result is not None
    assert result["severity"] == expected


def test_min_base_suppresses_small_sample():
    """小样本门槛仍然生效：gmv 未达 min_base 不评估。"""
    ctx = {"gmv_dod": Decimal("-0.95"), "gmv": Decimal("100")}
    assert evaluate_rule(_rule(), "SKU-1", ctx) is None


# ---------------------------------------------------------------------------
# Bug 4 · 件单价必须从聚合层贯通到 Excel
# ---------------------------------------------------------------------------


def test_category_unit_price_is_populated_end_to_end(client, db_session):
    """聚合层曾不输出 unit_price，导致「分类目」Sheet 件单价列整列为空。"""
    d0 = date(2026, 9, 6)
    _seed_facts(db_session, d0)          # net_amount=950, paid_qty=5 → 件单价 190

    data = client.get(f"/api/reports/daily?date={d0.isoformat()}").json()["data"]
    cat = data["by_category"][0]
    assert "unit_price" in cat["metrics"], "聚合契约缺少 unit_price 派生指标"
    assert cat["metrics"]["unit_price"]["value"] == pytest.approx(190.0)

    client.post("/api/reports/daily/generate", json={"date": d0.isoformat()})
    xlsx = client.get(f"/api/reports/daily/{d0.isoformat()}/export").content
    ws = load_workbook(io.BytesIO(xlsx))["分类目"]

    header_row = col = None
    for row in ws.iter_rows(min_row=1, max_row=5):
        values = [c.value for c in row]
        if "件单价" in values:
            header_row, col = row[0].row, values.index("件单价") + 1
            break
    assert header_row is not None, "「分类目」Sheet 缺少件单价列"

    cell = ws.cell(row=header_row + 1, column=col)
    assert cell.value == pytest.approx(190.0), "件单价单元格为空，列未真正落数"


# ---------------------------------------------------------------------------
# Bug 5 · 模拟数据生成必须受模块级共享锁保护
# ---------------------------------------------------------------------------


def test_mock_generate_holds_module_level_lock(client, monkeypatch):
    """旧实现在请求体内 with threading.Lock()，每次新建一把无人竞争的锁。"""
    from backend.app.api import mock as mock_api

    held_by_endpoint: list[bool] = []

    def _fake_generate(days, skus, outdir):
        # 端点若已持有共享锁，这里非阻塞获取必然失败
        got = mock_api._GEN_LOCK.acquire(blocking=False)
        if got:
            mock_api._GEN_LOCK.release()
        held_by_endpoint.append(not got)
        return {"files": {}, "report_date": "2026-09-06"}

    monkeypatch.setattr(mock_api, "generate_mock_files", _fake_generate)
    for _ in range(3):
        resp = client.post("/api/mock-data/generate", json={"days": 7, "skus": 4})
        assert resp.status_code == 200

    assert held_by_endpoint == [True, True, True], "生成过程未在共享锁保护下执行"


# ---------------------------------------------------------------------------
# Bug 6 · 归一化性能（真实形态：行几乎唯一）
# ---------------------------------------------------------------------------


def test_performance_with_mostly_unique_rows():
    """既有 5 万行用例只有 500 个去重组，掩盖了真实形态下的退化。

    真实日报里每个 sku-day 基本唯一，去重不合并任何行，此时组数等于行数，
    旧实现的逐组 Python 聚合把 5 万行拖到 ~32s，远超规划的 10s 上限。
    这里沿用规划原始口径（不收紧阈值），避免在慢机器上误报。

    守卫边界（经变异测试实测）：把主循环换回 ``iterrows`` 约 7.9s，
    仍落在 10s 预算内，本用例拦不住。本用例钉的是「不得违反规划 SLA」，
    而非「不得出现任何常数倍退化」；后者需要基准对比才能发现。
    """
    spec = _spec()
    n = 50000
    df = _df({
        "统计时间": [(date(2026, 8, 1) + timedelta(days=i % 35)).isoformat()
                     for i in range(n)],
        "商品ID": [f"P{i:06d}" for i in range(n)],
        "商品名称": [f"商品{i}" for i in range(n)],
        "支付金额": [f"{(i % 900) + 10}.5" for i in range(n)],
        "访客数": [str(i % 300) for i in range(n)],
    })
    start = time.perf_counter()
    result = normalize(df, spec)
    elapsed = time.perf_counter() - start

    assert result.stats["valid"] == n, "行应全部唯一，不发生去重合并"
    assert elapsed < 10.0, f"5 万行唯一行归一化耗时 {elapsed:.2f}s，超过 10s 上限"


def test_dedup_sum_takes_first_nonempty_for_text_columns():
    """锁定 Bug 6 的向量化改造未改变语义：非数值列取组内首个非空值。

    改造把逐组回调 Python 函数换成 groupby.first()，两者必须等价——
    既不是「取第一行」（首行可能为空），也不是「取最后一行」。
    注：category 的空串已由 _transform_string 置为 NA，因此本用例钉住的是
    first/last 的选择与「跳过空值」这两个真正会变的行为契约。
    """
    spec = _spec()
    df = _df([
        {"统计时间": "2026-09-06", "商品ID": "P1", "商品名称": "商品甲",
         "一级类目": "", "支付金额": "100"},
        {"统计时间": "2026-09-06", "商品ID": "P1", "商品名称": "商品甲",
         "一级类目": "美妆", "支付金额": "50"},
        {"统计时间": "2026-09-06", "商品ID": "P1", "商品名称": "商品甲",
         "一级类目": "护肤", "支付金额": "25"},
        {"统计时间": "2026-09-06", "商品ID": "P2", "商品名称": "商品乙",
         "一级类目": "", "支付金额": "10"},
        {"统计时间": "2026-09-06", "商品ID": "P2", "商品名称": "商品乙",
         "一级类目": "", "支付金额": "20"},
    ])
    result = normalize(df, spec)

    assert result.stats["duplicated"] == 3, "5 行应合并为 2 行"
    rows = {r.platform_product_code: r for r in result.rows}
    assert set(rows) == {"P1", "P2"}

    assert rows["P1"].gmv == Decimal("175.00"), "数值列应求和"
    assert rows["P1"].category == "美妆", "应取首个非空值，而非首行的空串"
    assert rows["P1"].net_amount == Decimal("175.00")

    assert rows["P2"].gmv == Decimal("30.00")
    assert rows["P2"].category is None, "组内全空应得 None"


# ---------------------------------------------------------------------------
# Bug 7 · 超限文件必须在读入内存前就被拒
# ---------------------------------------------------------------------------


def test_oversize_file_rejected_before_parsing(client, monkeypatch):
    """旧实现先 await f.read() 再校验大小，50MB 上限的防护形同虚设。

    单看「返回了大小错误」不够：读完再报错的路径同样会返回该错误。
    这里跟踪 UploadFile.read 是否被调用，才能真正钉住「校验先于读入」。
    """
    from starlette.datastructures import UploadFile

    from backend.app.api import uploads as uploads_api

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("超限文件不应进入解析流程")

    read_calls: list[str] = []
    original_read = UploadFile.read

    async def _tracking_read(self, size: int = -1):
        read_calls.append(self.filename or "")
        return await original_read(self, size)

    monkeypatch.setattr(uploads_api.upload_service, "ingest_file", _must_not_be_called)
    monkeypatch.setattr(UploadFile, "read", _tracking_read)
    monkeypatch.setattr(uploads_api.settings, "max_upload_mb", 0)

    payload = _taobao_csv([_row("2026-09-06", "TB1001", "100")])
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_商品效果_20260906.csv", payload, "text/csv")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["detail"]["files"][0]["error"]["detail"]["size"] == len(payload)
    assert read_calls == [], f"超限文件仍被读入内存：{read_calls}"


def test_file_within_limit_is_accepted(client, monkeypatch):
    """对照组：放开上限后同一文件应正常解析，确保上一条不是恒真断言。"""
    from backend.app.api import uploads as uploads_api

    monkeypatch.setattr(uploads_api.settings, "max_upload_mb", 50)
    payload = _taobao_csv([_row("2026-09-06", "TB1001", "100")])
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_商品效果_20260906.csv", payload, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["files"][0]["stats"]["valid"] == 1


# ---------------------------------------------------------------------------
# Bug 8 · 未处理异常不得向客户端泄漏内部细节
# ---------------------------------------------------------------------------


def test_unhandled_exception_hides_internals(db_engine, monkeypatch):
    """旧实现把 str(exc) 直接回给客户端，会带出路径、SQL 片段等内部信息。"""
    from backend.app.main import app

    secrets = ("D:\\secret\\app.db", "token=abc123", "RuntimeError")

    def _boom():
        raise RuntimeError(f"连接失败 {secrets[0]} {secrets[1]}")

    monkeypatch.setattr("backend.app.api.deps.SessionLocal", _boom)

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/skus")

    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INTERNAL_ERROR"
    for s in (*secrets, "Traceback", "site-packages"):
        assert s not in resp.text, f"响应体泄漏内部细节：{s}"


# ---------------------------------------------------------------------------
# Bug 9 · 零 critical 的日子不得在摘要里谎报「本日无异常预警」
# ---------------------------------------------------------------------------


@pytest.fixture()
def summary_env(monkeypatch):
    """固定摘要渲染环境，避开万元简写与默认公司名带来的长度飘移。"""
    monkeypatch.setattr("backend.app.config.settings.summary_use_wan", False)
    monkeypatch.setattr("backend.app.config.settings.company_name", "X")


def _warning_only_report() -> dict:
    """构造「整日只有 warning + 全文超 800 字符」的报告。

    这正是 Bug3 修好后的真实形态：冒烟数据里 13 条异常全部降级为
    warning，全文 1065 字符触发裁剪。
    """
    report = _base_report()
    report["by_platform"] = [
        {"key": f"p{i}", "name": f"平台{i:02d}超长名称超长名称",
         "metrics": {"net_amount": {"value": 9000.0 - i},
                     "gmv": {"value": 9000.0 - i, "dod": 0.1 - i * 0.01}},
         "share": 0.1}
        for i in range(12)
    ]
    report["top"]["by_gmv"] = [
        {"sku_code": f"SKU{i:04d}", "name": "超长商品名称" * 5 + str(i),
         "net_amount": 9000.0 - i, "gmv_dod": 0.5 - i * 0.01}
        for i in range(10)
    ]
    report["anomalies"] = [
        {"rule_name": f"规则{i}", "severity": "warning", "scope": "sku",
         "subject": f"SKU{i:04d}", "metric": "gmv_dod",
         "value": -0.55, "threshold": -0.4,
         "message": f"SKU{i:04d} 商品名称非常长非常长 GMV 环比 -55.0%，低于阈值 -40.0%"}
        for i in range(13)
    ]
    return report


def test_warning_only_day_never_claims_no_anomaly(summary_env, monkeypatch):
    """裁剪到「只留 critical」而当日 critical 为 0 时，不得反过来清空异常。

    旧实现 ``anomalies = criticals`` 在 criticals 为空时得到空列表，
    紧接着走 else 分支输出「· 本日无异常预警」——一个有 13 条真实
    预警的日子，群发消息会告诉运营「今天一切正常」。
    """
    fixed_at = datetime(2026, 9, 6, 20, 30)
    report = _warning_only_report()

    # 用例前提：未裁剪全文必须超限，否则走不到裁剪分支，下面的断言会恒真
    monkeypatch.setattr(ts, "MAX_SUMMARY_LEN", 10 ** 6)
    untrimmed = ts.generate_text_summary(report, company="X", generated_at=fixed_at)
    monkeypatch.setattr(ts, "MAX_SUMMARY_LEN", 800)
    assert len(untrimmed) > 800, "前提失效：全文未超限，裁剪路径未被触发"

    text = ts.generate_text_summary(report, company="X", generated_at=fixed_at)
    assert len(text) <= 800
    assert "本日无异常预警" not in text, "13 条真实预警被裁剪逻辑吞掉，摘要谎报平安"
    assert "[⚠️]" in text, "warning 异常应保留在「需关注」段中"


# ---------------------------------------------------------------------------
# Bug 10 · TOP3 段无数据或被裁掉时不得残留空标题
# ---------------------------------------------------------------------------


def test_top3_section_omitted_when_ranking_empty(summary_env):
    """榜单为空（当日无销售明细）时整段省略，不留一个孤零零的标题。"""
    report = _base_report()
    report["top"]["by_gmv"] = []
    text = ts.generate_text_summary(
        report, company="X", generated_at=datetime(2026, 9, 6, 20, 30)
    )
    assert "◆ TOP3 单品" not in text, "无榜单数据仍输出空标题"
    # 其余段落不受影响
    assert "◆ 分平台" in text
    assert "◆ 需关注" in text


def test_trimmed_summary_keeps_anomaly_section_heading(summary_env):
    """裁剪后 TOP3 整段消失（含标题），但「需关注」标题必须保留。

    旧实现在裁剪路径里无条件 append「◆ TOP3 单品」，top_count=0 时
    标题下面直接接空行再接下一段，形成只有 11 字符的空壳段落。
    """
    text = ts.generate_text_summary(
        _warning_only_report(), company="X",
        generated_at=datetime(2026, 9, 6, 20, 30),
    )
    assert len(text) <= 800
    assert "◆ TOP3 单品" not in text, "裁剪后残留空的 TOP3 标题"
    assert "◆ 需关注" in text


# ---------------------------------------------------------------------------
# Bug 11 · 固定种子的模拟数据生成必须与 PYTHONHASHSEED 无关
# ---------------------------------------------------------------------------

# 子进程里跑生成器并回传全部预期数字。只比 expected，不比文件字节：
# xlsx 是 zip，内部带写入时间戳，同内容也会得到不同哈希。
_GEN_FINGERPRINT = (
    "import json,sys;"
    "from backend.scripts.gen_mock_data import generate, DEFAULT_SEED;"
    "m=generate(days=3, skus=12, outdir=sys.argv[1], seed=DEFAULT_SEED);"
    "print(json.dumps({'expected': m['expected'],"
    " 'high_refund': m['high_refund_skus']}, sort_keys=True))"
)


def _generate_fingerprint(hash_seed: str, tmp_path) -> str:
    out = tmp_path / f"h{hash_seed}"
    out.mkdir(parents=True)
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-c", _GEN_FINGERPRINT, str(out)],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    assert proc.returncode == 0, (proc.stderr or "")[-800:]
    return proc.stdout.strip().splitlines()[-1]


def test_mock_generation_reproducible_across_hash_seeds(tmp_path):
    """同一 seed 在不同 PYTHONHASHSEED 下必须产出完全相同的数字。

    旧实现用 ``for code in high_refund_codes`` 遍历字符串集合来分配价格，
    而集合迭代序由 PYTHONHASHSEED 决定，rng.uniform 的调用次序因此逐进程
    漂移：实测同 seed 连跑 8 次得到 4 种不同 GMV。manifest 里写着固定
    seed，却无法跨次重放，冒烟结果也随之漂移。

    必须跨进程测：同进程内集合迭代序恒定，测不出这个缺陷。取 6 个
    不同哈希种子：3 个高退款 SKU 共有 6 种价格分配次序，只取 3 次有
    概率撞上同一排列而漏报。
    """
    seeds = tuple(str(i) for i in range(6))
    fps = [_generate_fingerprint(s, tmp_path) for s in seeds]
    assert len(set(fps)) == 1, (
        f"同一种子产出 {len(set(fps))} 种不同结果，生成不可复现：{fps}"
    )


# ---------------------------------------------------------------------------
# Bug 12 · commit 落库必须携带映射结果（sku_id 不得全部为 NULL）
# ---------------------------------------------------------------------------


def test_commit_persists_exact_match_sku_id(client, db_session):
    """to_db_dict 用的是未传参的 sku_id 形参（恒 None），attach_sku_ids
    回填到行上的映射结果被整体丢弃。

    后果：无论匹配与否，SalesFact.sku_id 全为 NULL —— 看板「已映射」
    分组永远为空、SKU 级异常规则永不触发。旧测试只断言 commit 的
    unmapped_count（内存统计），根本查不到这个错。
    """
    sku = Sku(sku_code="TB1001", name="精华液30ml", category="美妆")
    db_session.add(sku)
    db_session.commit()

    csv_bytes = _taobao_csv([
        _row("2026-09-06", "TB1001", "1,000.50"),
        _row("2026-09-06", "TB9999", "500.00", name="未知商品XX"),
    ])
    resp = client.post(
        "/api/uploads",
        files={"files": ("t.csv", csv_bytes, "text/csv")},
        data={"platform_key": "taobao"},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]
    resp = client.post(f"/api/uploads/{upload_id}/commit")
    assert resp.status_code == 200

    db_session.expire_all()
    facts = {
        f.platform_product_code: f.sku_id
        for f in db_session.query(SalesFact)
        .filter(SalesFact.upload_id == upload_id)
        .all()
    }
    assert facts["TB1001"] == sku.id, "精确匹配的 sku_id 未落库"
    assert facts["TB9999"] is None


# ---------------------------------------------------------------------------
# Bug 13 · 批量映射必须回填历史明细
# ---------------------------------------------------------------------------


def test_batch_mapping_backfills_sales_fact(client, db_session):
    """批量映射接口旧实现只改 SkuMapping：采纳后看板【待映射】分组
    永远保留这些商品（明细行的 sku_id 仍是 NULL），与单条采纳
    （apply_suggestion 会回填）行为不一致。"""
    sku = Sku(sku_code="SKU-B1", name="商品B1", category="美妆")
    db_session.add(sku)
    db_session.flush()
    up = Upload(filename="b.csv", sha256="b1", status="committed")
    db_session.add(up)
    db_session.flush()
    db_session.add(SalesFact(
        upload_id=up.id, stat_date=date(2026, 9, 6), platform_key="taobao",
        platform_product_code="PB-1", gmv=Decimal("100"),
        net_amount=Decimal("100"), raw_row_json={},
    ))
    db_session.commit()

    resp = client.post(
        "/api/sku-mappings/batch",
        json={"items": [{"platform_key": "taobao", "platform_product_code": "PB-1",
                         "sku_id": sku.id}]},
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    fact = (
        db_session.query(SalesFact)
        .filter(SalesFact.platform_product_code == "PB-1")
        .one()
    )
    assert fact.sku_id == sku.id, "批量映射未回填历史明细的 sku_id"


# ---------------------------------------------------------------------------
# Bug 14 · 幂等 commit 的 unmapped_count 必须与首次口径一致（按商品码）
# ---------------------------------------------------------------------------


def test_idempotent_commit_unmapped_count_counts_codes(client):
    """幂等分支旧实现按「行数」统计未映射：同一未映射商品码出现 N 天
    就返回 N，与首次 commit（按码统计）对不上，前端提示数凭空翻倍。"""
    csv_bytes = _taobao_csv([
        _row("2026-09-05", "TB7777", "100"),
        _row("2026-09-06", "TB7777", "200"),
    ])
    resp = client.post(
        "/api/uploads",
        files={"files": ("t.csv", csv_bytes, "text/csv")},
        data={"platform_key": "taobao"},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]

    first = client.post(f"/api/uploads/{upload_id}/commit").json()["data"]
    assert first["unmapped_count"] == 1

    second = client.post(f"/api/uploads/{upload_id}/commit").json()["data"]
    assert second["unmapped_count"] == first["unmapped_count"], (
        "幂等提交与首次提交的未映射统计口径不一致"
    )


# ---------------------------------------------------------------------------
# Bug 15 · compare=false 时级别型 SKU 规则不得被跳过
# ---------------------------------------------------------------------------


def test_sku_level_rules_fire_without_compare(db_session):
    """compare_enabled=False 时旧实现直接传空 SKU 上下文，「退款率超限」
    这类只看当日值、不依赖环比的规则被连坐跳过。"""
    d0 = date(2026, 9, 6)
    sku = Sku(sku_code="SKU-R1", name="高退款商品", category="美妆")
    db_session.add(sku)
    db_session.flush()
    up = Upload(filename="r.csv", sha256="r1", status="committed")
    db_session.add(up)
    db_session.flush()
    db_session.add(SalesFact(
        upload_id=up.id, stat_date=d0, platform_key="taobao",
        shop_code="S1", shop_name="店铺", platform_product_code="PR-1",
        product_name="高退款商品", category="美妆", sku_id=sku.id,
        gmv=Decimal("1000"), refund_amount=Decimal("200"),
        net_amount=Decimal("800"), visitors=100, buyers=10, paid_qty=5,
        raw_row_json={},
    ))
    db_session.commit()

    report = build_daily_report(db_session, d0, compare_enabled=False)
    sku_rules = {
        a["rule_name"] for a in report["anomalies"] if a["scope"] == "sku"
    }
    assert "退款率超限" in sku_rules, (
        f"compare=false 时级别型 SKU 规则未评估：{report['anomalies']}"
    )


# ---------------------------------------------------------------------------
# Bug 16 · 「已确认」Tab 不得混入未映射行
# ---------------------------------------------------------------------------


def test_mapping_confirmed_filter_excludes_unmapped(client, db_session):
    """前端「已确认」Tab 旧实现传 match_type=undefined（不过滤），
    未映射行与已确认行混在一起展示，与 Tab 语义矛盾。"""
    db_session.add_all([
        SkuMapping(platform_key="taobao", platform_product_code="PC-1",
                   platform_product_name="未映射商品", sku_id=None,
                   match_type="unmapped"),
        SkuMapping(platform_key="taobao", platform_product_code="PC-2",
                   platform_product_name="已确认商品", sku_id=None,
                   match_type="unmapped"),
    ])
    sku = Sku(sku_code="SKU-C1", name="商品C1")
    db_session.add(sku)
    db_session.flush()
    db_session.query(SkuMapping).filter(
        SkuMapping.platform_product_code == "PC-2"
    ).update({"sku_id": sku.id, "match_type": "manual"},
             synchronize_session=False)
    db_session.commit()

    resp = client.get("/api/sku-mappings?match_type=confirmed")
    assert resp.status_code == 200
    codes = {i["platform_product_code"] for i in resp.json()["data"]["items"]}
    assert codes == {"PC-2"}, f"已确认过滤结果混入了未映射行：{codes}"


# ---------------------------------------------------------------------------
# Bug 17 · SKU 更新显式传 null 必须 422，而非触发约束后 500
# ---------------------------------------------------------------------------


def test_sku_update_explicit_null_returns_422(client, db_session):
    """exclude_unset 只区分「未传」与「传了」：显式 name=null 能通过
    pydantic（类型允许 None），setattr 后触发 NOT NULL 约束，
    客户端拿到的是 INTERNAL_ERROR 500。"""
    sku = Sku(sku_code="SKU-N1", name="商品N1")
    db_session.add(sku)
    db_session.commit()

    resp = client.put(f"/api/skus/{sku.id}", json={"name": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # 正常更新不受影响
    resp = client.put(f"/api/skus/{sku.id}", json={"name": "商品N1改"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bug 18 · generate 请求体必须强校验（platforms 传字符串 → 422）
# ---------------------------------------------------------------------------


def test_generate_with_scalar_platforms_returns_422(client, db_session):
    """generate 旧实现用裸 dict 接 body：platforms 传字符串会一路
    穿透到聚合层 pandas isin() 抛 TypeError，客户端拿到 500。"""
    _seed_facts(db_session, date(2026, 9, 6))

    resp = client.post(
        "/api/reports/daily/generate",
        json={"date": "2026-09-06", "platforms": "taobao"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # 传列表仍正常
    resp = client.post(
        "/api/reports/daily/generate",
        json={"date": "2026-09-06", "platforms": ["taobao"]},
    )
    assert resp.status_code == 200
