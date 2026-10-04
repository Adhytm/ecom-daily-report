"""API 依赖注入。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from backend.app.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """请求级 DB 会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
