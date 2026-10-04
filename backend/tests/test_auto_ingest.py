"""自动文件接入（Drop Folder 监听与批量落库）测试。"""

from __future__ import annotations

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.models.entities import SalesFact, Upload
from backend.app.services.auto_ingest_service import scan_and_ingest_incoming


def _make_sample_csv(path: Path) -> None:
    # 模拟一份淘宝生意参谋真实 GBK/GB18030 导出的 CSV
    content = "统计日期,商品ID,商品名称,支付金额,支付件数\n2026-09-06,TB001,秋季卫衣,100.00,2\n"
    path.write_bytes(content.encode("gb18030"))


def test_auto_ingest_flow(tmp_path: Path, db_session):
    incoming = tmp_path / "incoming"
    incoming.mkdir()

    # 放入一份有效文件和一个临时下载文件（应忽略）
    f1 = incoming / "淘宝生意参谋_sales.csv"
    _make_sample_csv(f1)
    temp_f = incoming / "downloading.tmp"
    temp_f.write_text("incomplete content", encoding="utf-8")

    res = scan_and_ingest_incoming(db_session, folder=incoming)

    assert res["scanned_files"] == 1
    assert len(res["committed"]) == 1
    assert res["committed"][0]["platform"] == "taobao"
    assert res["committed"][0]["valid_rows"] == 1

    # 验证原文件已被移动到 processed 目录，incoming 下已无该文件
    assert not f1.exists()
    processed_files = list((incoming / "processed").glob("*淘宝生意参谋_sales.csv"))
    assert len(processed_files) == 1

    # 验证数据库中已经有 Upload 与 SalesFact
    upload = db_session.query(Upload).filter(Upload.filename == "淘宝生意参谋_sales.csv").first()
    assert upload is not None
    assert upload.status == "committed"
    assert db_session.query(SalesFact).filter(SalesFact.upload_id == upload.id).count() == 1

    # 再次放入相同文件测试防重逻辑
    _make_sample_csv(f1)
    res2 = scan_and_ingest_incoming(db_session, folder=incoming)
    assert res2["scanned_files"] == 1
    assert len(res2["committed"]) == 0
    assert len(res2["skipped_duplicates"]) == 1
    assert not f1.exists()


def test_auto_ingest_failed_file(tmp_path: Path, db_session):
    incoming = tmp_path / "incoming"
    incoming.mkdir()

    bad_f = incoming / "corrupt_sales.csv"
    bad_f.write_text("xxxx,yyyy,zzzz\n1,2,3\n", encoding="utf-8")

    res = scan_and_ingest_incoming(db_session, folder=incoming)
    assert res["scanned_files"] == 1
    assert len(res["committed"]) == 0
    assert len(res["failed"]) == 1
    assert not bad_f.exists()
    assert len(list((incoming / "failed").glob("*corrupt_sales.csv"))) == 1


def test_api_scan_incoming(tmp_path: Path, monkeypatch, db_session):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    f1 = incoming / "淘宝生意参谋_api_test.csv"
    _make_sample_csv(f1)

    monkeypatch.setattr("backend.app.config.settings.incoming_dir", incoming)

    client = TestClient(app)
    resp = client.post("/api/uploads/scan-incoming")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["data"]["scanned_files"] == 1
    assert len(data["data"]["committed"]) == 1
