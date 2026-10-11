"""可回灌明细导出测试（2026-10-04）。

核心断言：**导出 → 重新上传 → 重新提交，聚合口径必须与原数据完全一致**。
这同时验证了两件事：
1. 导出文件的列名确实被平台适配器认识（不需任何额外配置）；
2. 店铺维度没丢（shop_code 由 shop_name 兜底，dedup 键与原数据一致）。
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal


def _csv_bytes(rows: list[list[str]], encoding: str = "utf-8") -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode(encoding)


# 抖店（utf-8）：含 BOM；金额带千分位与货币符号、商品名含逗号 —— 都是真实导出常见形态
DOUDIAN_ROWS = [
    ["统计时间", "店铺名称", "商品ID", "商品名称", "一级类目",
     "商品访客数", "支付人数", "支付商品件数", "支付金额", "退款金额", "推广消耗"],
    ["2026-09-06", "甲店", "P001", "精华液30ml, 新版", "美妆护肤",
     "1,100", "10", "12", "￥1,000.50", "￥50.25", "80.00"],
    ["2026-09-06", "乙店", "P002", "身体乳250ml", "美妆护肤",
     "200", "20", "30", "2000.00", "100.00", "120.00"],
]

# 京东（gb18030）：带表头前说明行
JD_ROWS = [
    ["京东商智-商品分析报表（2026-09-06 导出）"],
    [],
    ["日期", "店铺名称", "商品编号", "商品名称", "一级分类",
     "访客数", "付款人数", "下单件数", "付款件数", "下单金额", "退款金额", "快车花费"],
    ["2026-09-06", "京东自营店", "J001", "洁面乳120g", "美妆护肤",
     "300", "25", "40", "35", "3000.00", "150.00", "200.00"],
]

# 各平台 GMV 期望值（列名走通用「金额」列，适配器会优先认规范源列）
EXPECT_DOUDIAN_GMV = Decimal("3000.50")
EXPECT_JD_GMV = Decimal("3000.00")


def _seed(client) -> None:
    files = [
        ("抖店罗盘_商品分析.csv", _csv_bytes(DOUDIAN_ROWS, "utf-8-sig")),
        ("京东商智_商品分析.csv", _csv_bytes(JD_ROWS, "gb18030")),
    ]
    for name, raw in files:
        r = client.post("/api/uploads", files={"files": (name, raw, "application/octet-stream")})
        assert r.status_code == 200, r.text
        item = r.json()["data"]["files"][0]
        assert item["detected_platform"] in ("doudian", "jd"), item
        c = client.post(f"/api/uploads/{item['upload_id']}/commit")
        assert c.status_code == 200, c.text


def _gmv_by_platform(client, date: str = "2026-09-06") -> dict[str, Decimal]:
    """从看板契约取各平台 GMV（by_platform[].metrics.gmv.value）。"""
    resp = client.get("/api/reports/daily", params={"date": date})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    out: dict[str, Decimal] = {}
    for row in data.get("by_platform", []):
        block = row.get("metrics", {}).get("gmv") or {}
        out[row["key"]] = Decimal(str(block.get("value", "0")))
    return out


def _parse_amount(v) -> Decimal:
    """看板 overview 的金额可能是 "4,500.50" 这类字符串。"""
    if v is None:
        return Decimal("0")
    return Decimal(str(v).replace(",", "").replace("￥", "").replace("¥", "").strip() or "0")


def _total_gmv(client, date: str = "2026-09-06") -> Decimal:
    resp = client.get("/api/reports/daily", params={"date": date})
    assert resp.status_code == 200, resp.text
    block = resp.json()["data"]["overview"].get("gmv") or {}
    return _parse_amount(block.get("value"))


def test_detail_export_columns_match_adapter(client, db_session):
    """导出列名必须是适配器的规范源列名（回灌不需要额外配置）。"""
    _seed(client)
    resp = client.get("/api/reports/daily/2026-09-06/detail-exports")
    assert resp.status_code == 200, resp.text
    items = {it["platform_key"]: it for it in resp.json()["data"]["items"]}

    assert set(items) == {"doudian", "jd"}
    assert items["doudian"]["row_count"] == 2
    assert items["jd"]["row_count"] == 1

    # 必需列 + 店铺列必须在导出的表头里
    for key, required in (
        ("doudian", ["统计时间", "商品ID", "支付金额", "店铺名称"]),
        ("jd", ["日期", "商品编号", "下单金额", "店铺名称"]),
    ):
        cols = items[key]["columns"]
        for col in required:
            assert col in cols, f"{key} 缺少列 {col}：{cols}"


def test_detail_export_file_is_utf8sig_and_parseable(client, db_session):
    """下载内容应是 utf-8-sig（Excel 双击不乱码，上传端首选编码）。"""
    _seed(client)
    resp = client.get("/api/reports/daily/2026-09-06/detail-exports/doudian")
    assert resp.status_code == 200, resp.text
    raw = resp.content
    assert raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "统计时间"
    assert len(rows) == 3  # 表头 + 2 行
    assert "P001" in text and "甲店" in text


def test_detail_export_roundtrip_preserves_totals(client, db_session):
    """导出 → 重新上传 → 提交：回灌进去的金额必须一分不差地等于导出文件里的金额。

    注意：库里**本来就有**这份数据，所以回灌后总量必然翻倍 —— 断言必须落在
    「总量增量 == 导出数据自身的合计」上，而不是「总量不变」。
    """
    _seed(client)
    before_platform = _gmv_by_platform(client)
    assert before_platform.get("doudian") == EXPECT_DOUDIAN_GMV, before_platform
    assert before_platform.get("jd") == EXPECT_JD_GMV, before_platform
    before_total = _total_gmv(client)

    # 先把清单与文件内容取完再上传，避免边读边写互相影响
    resp = client.get("/api/reports/daily/2026-09-06/detail-exports")
    assert resp.status_code == 200, resp.text
    plan = []
    for it in resp.json()["data"]["items"]:
        dl = client.get(it["download_url"])
        assert dl.status_code == 200, dl.text
        # 防回归：download_url 必须真的命中导出端点。路径写错会被 SPA 静态兜底
        # 接走并返回 200 + index.html，只断言 status_code 是查不出来的。
        ctype = dl.headers.get("content-type", "")
        assert "text/csv" in ctype, f"下载返回的不是 CSV：{ctype} {dl.content[:80]!r}"
        assert it["columns"][0].encode("utf-8") in dl.content
        plan.append((it, dl.content))

    for it, content in plan:
        up = client.post(
            "/api/uploads",
            files={"files": (it["filename"], content, "application/octet-stream")},
        )
        assert up.status_code == 200, up.text
        new_item = up.json()["data"]["files"][0]
        # 自动识别必须命中同一个平台（不需要手选）
        assert new_item["detected_platform"] == it["platform_key"], new_item
        assert new_item["stats"]["valid"] == it["row_count"], new_item
        # 未触发格式兜底 warning → 走的是正常 hints 命中路径
        assert not any(
            "auto_header_hints" in w for w in new_item["parse_meta"]["warnings"]
        ), new_item["parse_meta"]["warnings"]
        c = client.post(f"/api/uploads/{new_item['upload_id']}/commit")
        assert c.status_code == 200, c.text

    # 增量必须等于原始数据的合计（doudian 3000.50 + jd 3000.00）
    delta = _total_gmv(client) - before_total
    expected = EXPECT_DOUDIAN_GMV + EXPECT_JD_GMV
    assert delta == expected, f"回灌金额对不上：增量={delta} 期望={expected}"
    # 再按平台核对一次，确保没有串平台
    after_platform = _gmv_by_platform(client)
    assert after_platform["doudian"] == before_platform["doudian"] * 2, after_platform
    assert after_platform["jd"] == before_platform["jd"] * 2, after_platform


def test_detail_export_requires_data(client, db_session):
    """没有明细的日期要明确报错，而不是产出空文件。"""
    resp = client.get("/api/reports/daily/2020-01-01/detail-exports")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_DATA_FOR_DATE"


def test_detail_export_unknown_platform_404(client, db_session):
    _seed(client)
    resp = client.get("/api/reports/daily/2026-09-06/detail-exports/taobao")
    assert resp.status_code == 404
    assert "taobao" in resp.json()["error"]["message"]


def test_detail_export_satisfies_every_adapter_read_contract(client, db_session, tmp_path):
    """回灌文件必须满足**每个平台自己**的读取契约，逐平台用其适配器实解析核对。

    这是"合成数据测不出来、真数据一跑就露"的那类坑：
    拼多多适配器 skip_footer=2（真实导出末尾有 2 行汇总说明），
    若回灌文件不补这两行，适配器会把**真实数据行**当脚注丢掉（25 行只剩 23 行）。
    """
    import pandas as pd  # noqa: PLC0415

    from backend.app.core.adapter_registry import load_builtin
    from backend.app.core.file_parser import parse_file

    # 造 5 行明细（奇数行，便于暴露"尾部被吃掉 N 行"）
    rows = [["统计日期", "店铺名称", "商品ID", "商品名称", "支付金额"]]
    for i in range(5):
        rows.append(["2026-09-06", "甲店", f"P{i:03d}", f"商品{i}", f"{100 + i}.50"])
    raw = _csv_bytes(rows, "utf-8-sig")

    # 用每个平台适配器的列名各导出一份，再逐平台自解析
    for key in ("taobao", "doudian", "pinduoduo", "jd"):
        spec = load_builtin(key).spec
        canonical = {dst: src for src, dst in spec.column_map.items()}
        for dst, aliases in spec.column_aliases.items():
            canonical.setdefault(dst, aliases[0])
        header = [canonical[f] for f in ("stat_date", "platform_product_code", "gmv")
                  if f in canonical]
        assert len(header) == 3, f"{key} 必需列不全：{canonical}"

        body = [[r[0], r[2], r[4]] for r in rows[1:]]
        # 复用产品代码：导出时补 skip_footer 占位脚注
        from backend.app.exporters.detail_export import _footer_rows

        out_rows = [header] + body + _footer_rows(
            spec, stat_date="2026-09-06", row_count=len(body)
        )
        path = tmp_path / f"回灌_{key}.csv"
        path.write_bytes(_csv_bytes(out_rows, "utf-8-sig"))

        df, meta = parse_file(path.read_bytes(), path.name, spec.file_matching)
        # 表头必须精确等于导出的列（脚注若没被丢干净，这里会多出一列"以上数据仅供参考…"）
        assert meta.detected_columns == header, (
            f"{key} 解析出的表头不是导出的列：{meta.detected_columns}"
        )
        assert isinstance(df, pd.DataFrame)
        # 关键：数据行一行都不能少（拼多多 skip_footer=2 曾吃掉尾部 2 行）
        assert len(df) == 5, f"{key} 回灌丢了行：解析出 {len(df)} 行，应为 5 行"
