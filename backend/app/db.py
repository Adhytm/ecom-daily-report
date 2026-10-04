"""数据库基础设施：engine / SessionLocal / Base / init_db。

SQLite 以 WAL 模式运行（规划 2.1），通过连接级 PRAGMA 设置；
金额字段在 ORM 层统一使用 Numeric，禁止 float（规划 4.1）。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.app.config import settings


class Base(DeclarativeBase):
    """全部 ORM 模型的公共基类。"""


def _create_engine(db_url: str):
    engine = create_engine(
        db_url,
        # FastAPI 多线程访问 SQLite，需要放开同线程检查；内存库配合 StaticPool 共享连接
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
        # WAL 提升并发读写；外键约束 SQLite 默认关闭，必须显式开启；
        # busy_timeout 避免并发写时立即报 database is locked（默认 0 不重试）
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


# 数据库 URL：内存库（测试）与文件库（生产）由环境变量区分
_db_path = settings.db_path
_db_path.parent.mkdir(parents=True, exist_ok=True)
engine = _create_engine(f"sqlite:///{_db_path}")

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator:
    """FastAPI 依赖注入：请求级 DB 会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表 + 幂等插入默认预警规则（规划 4.2 / T09）。

    MVP 阶段不使用迁移工具，直接 create_all；表结构变更时删除
    data/app.db 重建。
    """
    # 导入实体模块以注册到 Base.metadata
    from backend.app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed_default_anomaly_rules(db)
        db.commit()


def seed_default_anomaly_rules(db) -> None:
    """幂等插入 6 条默认异常预警规则（规划 6.5 表格）。

    接收一个 Session，便于测试库复用。
    """
    from decimal import Decimal

    from backend.app.models.entities import AnomalyRule

    default_rules = [
        ("GMV 环比大幅波动", "gmv_dod", "platform", "abs_gt", "0.30", "1000"),
        ("SKU GMV 环比骤降", "gmv_dod", "sku", "lt", "-0.40", "500"),
        ("退款率超限", "refund_rate", "sku", "gt", "0.15", "500"),
        ("整体退款率超限", "refund_rate", "overall", "gt", "0.20", "0"),
        ("转化率异常下滑", "conversion_rate_dod_pp", "platform", "lt", "-2.0", "1000"),
        ("推广 ROI 过低", "roi", "platform", "lt", "1.0", "500"),
    ]

    existing_names = {r.name for r in db.query(AnomalyRule.name).all()}
    for name, metric, scope, operator, threshold, min_base in default_rules:
        if name in existing_names:
            continue
        db.add(
            AnomalyRule(
                name=name,
                metric=metric,
                scope=scope,
                operator=operator,
                threshold=Decimal(threshold),
                min_base=Decimal(min_base),
                enabled=True,
            )
        )


def check_db() -> bool:
    """健康检查：验证 DB 连通性。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
