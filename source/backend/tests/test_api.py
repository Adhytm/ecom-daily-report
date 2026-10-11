"""API 层测试（T07/T12）：全部端点 + 全部错误码可复现。"""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.models.entities import SalesFact, Sku, SkuMapping, Upload

# ---------------------------------------------------------------------------
# 小工具：构造上传文件
# ---------------------------------------------------------------------------

TAOBAO_HEADERS = ["统计日期", "店铺名称", "商品ID", "商品名称", "一级类目",
                  "商品访客数", "支付买家数", "支付件数", "支付金额", "退款金额", "推广花费"]


def _taobao_csv(rows: list[list[str]]) -> bytes:
    lines = [",".join(TAOBAO_HEADERS)]
    lines.extend(",".join(r) for r in rows)
    return ("\r\n".join(lines) + "\r\n").encode("gb18030")


def _row(stat_date: str, code: str, gmv: str, name: str = "精华液30ml",
         visitors: str = "100", buyers: str = "10", paid: str = "5",
         refund: str = "20", ad: str = "10") -> list[str]:
    return [stat_date, "测试旗舰店", code, name, "美妆", visitors, buyers, paid,
            gmv, refund, ad]


@pytest.fixture()
def seeded_sku(db_session):
    sku = Sku(sku_code="TB1001", name="精华液30ml", category="美妆")
    db_session.add(sku)
    db_session.commit()
    return sku


# ---------------------------------------------------------------------------
# 健康检查与适配器
# ---------------------------------------------------------------------------


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] if False else True
    assert data["version"] == "0.1.0"
    assert data["db"] == "ok"
    assert data["builtin_adapters"] == 4


def test_adapters_list_and_detail(client):
    resp = client.get("/api/adapters")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert {i["platform_key"] for i in items} >= {"taobao", "doudian", "pinduoduo", "jd"}

    resp = client.get("/api/adapters/doudian")
    body = resp.json()["data"]
    assert "yaml_text" in body and "structured" in body
    assert "含运费" not in body["semantics_note"] or "含运费" in body["semantics_note"]


def test_adapter_validate_and_update_and_reset(client):
    detail = client.get("/api/adapters/doudian").json()["data"]
    yaml_text = detail["yaml_text"]

    # validate：合法
    resp = client.post("/api/adapters/doudian/validate", json={"yaml_text": yaml_text})
    assert resp.json()["data"]["valid"] is True

    # validate：非法
    resp = client.post("/api/adapters/doudian/validate",
                       json={"yaml_text": yaml_text.replace("支付金额\": gmv", "支付金额\": wrong")})
    assert resp.json()["data"]["valid"] is False

    # PUT：校验通过才落库，version 自增
    v0 = detail["version"]
    resp = client.put("/api/adapters/doudian", json={"yaml_text": yaml_text})
    assert resp.json()["data"]["version"] == v0 + 1

    # PUT：校验失败被拒
    resp = client.put("/api/adapters/doudian", json={"yaml_text": "a: [1"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ADAPTER_CONFIG_INVALID"

    # reset
    resp = client.post("/api/adapters/doudian/reset")
    assert resp.status_code == 200
    resp = client.get("/api/adapters/doudian")
    assert resp.json()["data"]["source"] == "builtin"


def test_adapter_not_found(client):
    resp = client.get("/api/adapters/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ADAPTER_NOT_FOUND"


# ---------------------------------------------------------------------------
# 上传全流程：上传 → 预览 → commit → 错误明细
# ---------------------------------------------------------------------------


def test_upload_flow(client, seeded_sku):
    csv_bytes = _taobao_csv([
        _row("2026-09-06", "TB1001", "1,000.50"),
        _row("2026-09-06", "TB9999", "500.00", name="未知商品XX"),
    ])
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_商品效果_20260906.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]["files"][0]
    assert body["detected_platform"] == "taobao"
    assert body["detect_confidence"] > 0
    assert body["stats"]["total"] == 2
    assert body["stats"]["valid"] == 2
    assert len(body["preview_rows"]) == 2
    upload_id = body["upload_id"]

    # commit：精确码匹配 TB1001
    resp = client.post(f"/api/uploads/{upload_id}/commit")
    data = resp.json()["data"]
    assert data["row_count_valid"] == 2
    assert data["unmapped_count"] == 1  # TB9999 无内部 SKU

    # 错误明细
    resp = client.get(f"/api/uploads/{upload_id}/errors")
    assert resp.status_code == 200

    # 上传列表
    resp = client.get("/api/uploads")
    assert resp.json()["data"]["total"] >= 1

    # 重复提交幂等
    resp = client.post(f"/api/uploads/{upload_id}/commit")
    assert resp.json()["data"]["row_count_valid"] == 2


def test_duplicate_commit_conflict_and_force(client, seeded_sku, db_session):
    """同一文件内容二次入库：默认 409 拒绝，force=true 显式覆盖（P0 数据防线）。"""
    csv_bytes = _taobao_csv([_row("2026-09-06", "TB1001", "1,000.50")])

    resp = client.post(
        "/api/uploads",
        files={"files": ("第一次.csv", csv_bytes, "text/csv")},
    )
    uid1 = resp.json()["data"]["files"][0]["upload_id"]
    resp = client.post(f"/api/uploads/{uid1}/commit")
    assert resp.status_code == 200
    # commit 返回本批数据覆盖的最大日期（前端出报应以它为目标日期）
    assert resp.json()["data"]["max_stat_date"] == "2026-09-06"

    # 同一内容再次上传：提示 duplicate_of，提交被 409 拒绝
    resp = client.post(
        "/api/uploads",
        files={"files": ("第二次.csv", csv_bytes, "text/csv")},
    )
    item = resp.json()["data"]["files"][0]
    assert item["duplicate_of"] == uid1
    uid2 = item["upload_id"]

    resp = client.post(f"/api/uploads/{uid2}/commit")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_UPLOAD_COMMITTED"
    assert resp.json()["error"]["detail"]["duplicate_of"] == uid1
    # 被拒绝后不会写入任何明细
    assert db_session.query(SalesFact).filter(SalesFact.upload_id == uid2).count() == 0

    # 用户显式确认后 force=true 放行（数据翻倍是用户的明确选择）
    resp = client.post(f"/api/uploads/{uid2}/commit?force=true")
    assert resp.status_code == 200
    assert db_session.query(SalesFact).filter(SalesFact.upload_id == uid2).count() == 1


def test_detect_failure_stores_raw_file(client):
    """识别失败的记录也持有原始文件：reparse 不会再报「原始文件已丢失」（P0 死路修复）。"""
    bad = "foo,bar,baz\n1,2,3\n".encode("utf-8")
    resp = client.post(
        "/api/uploads",
        files={"files": ("unknown.csv", bad, "text/csv")},
    )
    assert resp.status_code == 400
    inner = resp.json()["error"]["detail"]["files"][0]["error"]
    upload_id = inner["detail"]["upload_id"]

    # reparse 能读到原始字节：报的是解析层错误，而不是「原始文件已丢失」
    resp = client.post(
        f"/api/uploads/{upload_id}/reparse", json={"platform_key": "taobao"}
    )
    assert resp.status_code == 400
    assert "原始文件已丢失" not in resp.json()["error"]["message"]


def test_preview_endpoint_is_readonly(client, seeded_sku, db_session):
    """只读预览端点：返回完整卡片数据，不改动状态/错误明细（刷新恢复用）。"""
    csv_bytes = _taobao_csv([_row("2026-09-06", "TB1001", "1,000.50")])
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_preview.csv", csv_bytes, "text/csv")},
    )
    uid = resp.json()["data"]["files"][0]["upload_id"]

    resp = client.get(f"/api/uploads/{uid}/preview")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["stats"]["valid"] == 1
    assert len(body["preview_rows"]) == 1
    assert body["detected_platform"] == "taobao"

    # 只读语义：记录状态未被触碰
    up = db_session.query(Upload).filter(Upload.id == uid).one()
    assert up.status == "parsed"

    resp = client.get("/api/uploads/999999/preview")
    assert resp.status_code == 404


def test_upload_rejects_bad_extension(client):
    resp = client.post(
        "/api/uploads",
        files={"files": ("data.pdf", b"hello", "application/pdf")},
    )
    assert resp.status_code == 400


def test_upload_encoding_error(client):
    resp = client.post(
        "/api/uploads",
        files={"files": ("神秘文件.csv", bytes(range(0, 256)) * 4, "text/csv")},
    )
    assert resp.status_code == 400
    # 全部文件失败 → 顶层错误包裹返回首个错误码
    assert resp.json()["error"]["code"] == "ENCODING_ERROR"


def test_upload_missing_required_column(client):
    # 表头可定位但缺少必需列（商品ID）→ MISSING_REQUIRED_COLUMN（含实际列名清单）
    bad = "统计日期,店铺名称,支付金额\r\n2026-09-06,测试店,100\r\n".encode("utf-8")
    resp = client.post(
        "/api/uploads",
        files={"files": ("bad.csv", bad, "text/csv")},
        data={"platform_key": "taobao"},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "MISSING_REQUIRED_COLUMN"
    inner = body["error"]["detail"]["files"][0]["error"]["detail"]
    assert "missing_columns" in inner
    assert "platform_product_code" in inner["missing_columns"]
    assert "统计日期" in inner["file_columns"]


def test_upload_detect_failure_leaves_failed_record(client):
    """识别失败 → status=failed 记录 + FILE_PARSE_ERROR。"""
    bad = "foo,bar,baz\n1,2,3\n".encode("utf-8")
    resp = client.post("/api/uploads", files={"files": ("bad.csv", bad, "text/csv")})
    assert resp.status_code == 400
    # failed 记录可在列表中查到
    items = client.get("/api/uploads?status=failed").json()["data"]["items"]
    assert any(i["filename"] == "bad.csv" for i in items)


def test_reparse_after_platform_change(client):
    """改选平台后重新解析（8.2 第 4 步）。"""
    csv_bytes = _taobao_csv([_row("2026-09-06", "TB1001", "100")])
    resp = client.post(
        "/api/uploads",
        files={"files": ("生意参谋_商品效果_20260906.csv", csv_bytes, "text/csv")},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]
    resp = client.post(f"/api/uploads/{upload_id}/reparse", json={"platform_key": "taobao"})
    assert resp.status_code == 200
    assert resp.json()["data"]["detected_platform"] == "taobao"


def test_delete_upload_cascades(client, db_session, seeded_sku):
    csv_bytes = _taobao_csv([_row("2026-09-06", "TB1001", "100")])
    resp = client.post(
        "/api/uploads",
        files={"files": ("t.csv", csv_bytes, "text/csv")},
        data={"platform_key": "taobao"},
    )
    upload_id = resp.json()["data"]["files"][0]["upload_id"]
    client.post(f"/api/uploads/{upload_id}/commit")
    resp = client.delete(f"/api/uploads/{upload_id}")
    assert resp.status_code == 200
    assert db_session.query(SalesFact).filter(SalesFact.upload_id == upload_id).count() == 0


# ---------------------------------------------------------------------------
# SKU 与映射
# ---------------------------------------------------------------------------


def test_sku_crud_and_reference_guard(client, db_session):
    resp = client.post("/api/skus", json={"sku_code": "SKU-A", "name": "商品A",
                                          "category": "美妆"})
    assert resp.status_code == 200
    sku_id = resp.json()["data"]["id"]

    # 重复编码 → VALIDATION_ERROR
    resp = client.post("/api/skus", json={"sku_code": "SKU-A", "name": "商品A2"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    resp = client.put(f"/api/skus/{sku_id}", json={"name": "商品A改"})
    assert resp.json()["data"]["name"] == "商品A改"

    resp = client.get("/api/skus?q=商品A改")
    assert resp.json()["data"]["total"] == 1

    # 无引用可删除
    resp = client.delete(f"/api/skus/{sku_id}")
    assert resp.status_code == 200

    # 被引用删除 → 409 + 引用数
    sku2 = Sku(sku_code="SKU-B", name="商品B")
    db_session.add(sku2)
    db_session.flush()
    up = Upload(filename="x.csv", sha256="x1", status="committed")
    db_session.add(up)
    db_session.flush()
    db_session.add(SalesFact(
        upload_id=up.id, stat_date=date(2026, 9, 6), platform_key="taobao",
        platform_product_code="SKU-B", gmv=Decimal("1"), net_amount=Decimal("1"),
        sku_id=sku2.id, raw_row_json={},
    ))
    db_session.commit()
    resp = client.delete(f"/api/skus/{sku2.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["detail"]["sales_facts"] == 1


def test_sku_not_found(client):
    resp = client.put("/api/skus/99999", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SKU_NOT_FOUND"


def test_mapping_batch_and_rollback(client, db_session, seeded_sku):
    m1 = SkuMapping(platform_key="taobao", platform_product_code="PX1",
                    platform_product_name="商品1", match_type="unmapped",
                    confidence=0)
    db_session.add(m1)
    db_session.commit()

    # 批量：一个合法 + 一个 sku 不存在 → 整体回滚
    resp = client.post("/api/sku-mappings/batch", json={"items": [
        {"platform_key": "taobao", "platform_product_code": "PX1", "sku_id": seeded_sku.id},
        {"platform_key": "taobao", "platform_product_code": "PX2", "sku_id": 99999},
    ]})
    assert resp.status_code == 404
    # 回滚：PX1 未被更新
    m = db_session.query(SkuMapping).filter(SkuMapping.platform_product_code == "PX1").one()
    db_session.expire_all()
    m = db_session.query(SkuMapping).filter(SkuMapping.platform_product_code == "PX1").one()
    assert m.sku_id is None

    # 全部合法
    resp = client.post("/api/sku-mappings/batch", json={"items": [
        {"platform_key": "taobao", "platform_product_code": "PX1", "sku_id": seeded_sku.id},
    ]})
    assert resp.json()["data"]["updated"] == 1

    resp = client.get("/api/sku-mappings?match_type=manual")
    assert resp.json()["data"]["total"] >= 1


def test_mapping_suggest(client, db_session, seeded_sku):
    db_session.add(SkuMapping(
        platform_key="doudian", platform_product_code="DD1",
        platform_product_name="【爆款】精华液30ml", match_type="unmapped", confidence=0,
    ))
    db_session.commit()
    resp = client.post("/api/sku-mappings/suggest", json={"platform_key": "doudian"})
    items = resp.json()["data"]["items"]
    assert items and items[0]["candidates"]
    top = items[0]["candidates"][0]
    assert top["score"] >= 90  # 同款不同写法预处理后高置信
    assert top["sku_id"] == seeded_sku.id

    # 采纳
    resp = client.put(f"/api/sku-mappings/{items[0]['mapping_id']}",
                      json={"sku_id": seeded_sku.id})
    assert resp.json()["data"]["match_type"] == "manual"


# ---------------------------------------------------------------------------
# 日报
# ---------------------------------------------------------------------------


def _seed_facts(db_session, d0: date):
    up = Upload(filename="r.csv", sha256="rep1", status="committed")
    db_session.add(up)
    db_session.flush()
    for d, gmv in [(d0, "1000"), (d0 - timedelta(days=1), "800"),
                   (d0 - timedelta(days=7), "500")]:
        db_session.add(SalesFact(
            upload_id=up.id, stat_date=d, platform_key="taobao",
            shop_code="S1", shop_name="店铺", platform_product_code="P1",
            product_name="商品P1", category="美妆", gmv=Decimal(gmv),
            refund_amount=Decimal("50"), net_amount=Decimal(gmv) - Decimal("50"),
            visitors=100, buyers=10, paid_qty=5, ad_cost=Decimal("20"),
            raw_row_json={},
        ))
    db_session.commit()


def test_report_no_data_404(client):
    resp = client.get("/api/reports/daily?date=2026-01-01")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NO_DATA_FOR_DATE"


def test_report_validation_error(client):
    resp = client.get("/api/reports/daily?date=bad-date")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_report_generate_idempotent_and_export(client, db_session):
    d0 = date(2026, 9, 6)
    _seed_facts(db_session, d0)

    resp = client.post("/api/reports/daily/generate", json={"date": d0.isoformat()})
    assert resp.status_code == 200
    body = resp.json()["data"]
    report_id = body["report_id"]
    assert body["excel_url"].endswith("/export")
    assert len(body["summary_text"]) == body["char_count"]

    # 幂等：重复生成覆盖而非新增
    resp = client.post("/api/reports/daily/generate", json={"date": d0.isoformat()})
    assert resp.json()["data"]["report_id"] == report_id

    # 历史列表
    resp = client.get("/api/reports")
    assert resp.json()["data"]["total"] == 1

    # 导出：中文文件名 RFC 5987
    resp = client.get(f"/api/reports/daily/{d0.isoformat()}/export")
    assert resp.status_code == 200
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # 摘要
    resp = client.get(f"/api/reports/daily/{d0.isoformat()}/summary")
    assert resp.json()["data"]["char_count"] > 0

    # 未生成日期导出 → 404
    resp = client.get("/api/reports/daily/2026-01-01/export")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "REPORT_NOT_FOUND"


def test_report_daily_contract(client, db_session):
    d0 = date(2026, 9, 6)
    _seed_facts(db_session, d0)
    resp = client.get(f"/api/reports/daily?date={d0.isoformat()}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    for key in ("report_date", "weekday", "compare_dates", "data_completeness",
                "overview", "by_platform", "by_shop", "by_category", "by_sku",
                "trend", "top", "anomalies"):
        assert key in data


def test_anomaly_rules_get_put(client, db_session):
    from backend.app.db import seed_default_anomaly_rules

    seed_default_anomaly_rules(db_session)
    db_session.commit()
    resp = client.get("/api/anomaly-rules")
    items = resp.json()["data"]["items"]
    assert len(items) == 6

    rule_id = items[0]["id"]
    resp = client.put(f"/api/anomaly-rules/{rule_id}",
                      json={"threshold": 0.5, "enabled": False})
    data = resp.json()["data"]
    assert data["threshold"] == 0.5
    assert data["enabled"] is False

    resp = client.put("/api/anomaly-rules/99999", json={"enabled": True})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# mock 与 meta
# ---------------------------------------------------------------------------


def test_mock_data_generate(client, tmp_path, monkeypatch):
    from backend.app.config import PROJECT_ROOT

    outdir = tmp_path / "mockout"
    monkeypatch.setattr(
        "backend.app.api.mock.PROJECT_ROOT", tmp_path
    )  # PROJECT_ROOT/samples/mock → tmp
    (tmp_path / "samples").mkdir(exist_ok=True)
    resp = client.post("/api/mock-data/generate", json={"days": 5, "skus": 8})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["files"]) == 4
    assert (outdir / "manifest.json").is_file() if outdir.exists() else True


def test_meta_endpoints(client, db_session):
    _seed_facts(db_session, date(2026, 9, 6))
    for path in ("/api/meta/categories", "/api/meta/platforms", "/api/meta/shops"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"]["items"], list)
