"""pytest 公共 fixture：内存 SQLite、模拟文件工厂、TestClient。

测试隔离策略：每个用例使用独立的内存库（StaticPool 共享同一连接），
并通过环境变量把 DB/导出路径指向临时目录。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import db as db_module
from backend.app.db import Base, seed_default_anomaly_rules


@pytest.fixture()
def db_engine(tmp_path, monkeypatch):
    """内存 SQLite 引擎；同时把全局 db 模块的 engine/SessionLocal 替换掉。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSession)
    monkeypatch.setattr("backend.app.api.deps.SessionLocal", TestingSession)
    monkeypatch.setattr("backend.app.db.SessionLocal", TestingSession)
    # 原始上传文件存盘目录同样隔离：否则任何走 ingest_file 的测试
    # （API 层 + 服务层直调）都会往真实 data/uploads/ 写垃圾文件
    monkeypatch.setattr(
        "backend.app.services.upload_service._UPLOADS_DIR", tmp_path / "uploads"
    )

    # 确保实体模块已注册到 Base.metadata（与生产 init_db 一致）
    from backend.app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with TestingSession() as db:
        seed_default_anomaly_rules(db)
        db.commit()

    yield engine

    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Generator:
    """直接提供 DB 会话（服务层单测用）。"""
    session_factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def client(db_engine, tmp_path, monkeypatch):
    """FastAPI TestClient：使用内存库与临时导出目录。"""
    monkeypatch.setattr("backend.app.config.settings.export_dir", tmp_path / "exports")
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def export_dir(tmp_path, monkeypatch):
    """导出目录指向临时路径。"""
    d = tmp_path / "exports"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("backend.app.config.settings.export_dir", d)
    return d
