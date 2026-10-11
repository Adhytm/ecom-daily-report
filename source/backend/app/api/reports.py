"""日报与预警规则 API（规划 7）。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.core import ValidationError
from backend.app.main import ok
from backend.app.models.entities import AnomalyRule
from backend.app.schemas.report import (
    AnomalyRuleIn,
    AnomalyRuleOut,
    DailyReportData,
    ScopeIn,
)
from backend.app.services import report_service

router = APIRouter(tags=["reports"])


def _parse_date_or_422(date_str: str | None) -> str:
    if not date_str:
        raise ValidationError("缺少 date 参数（YYYY-MM-DD）")
    return date_str


@router.get("/reports/daily")
def daily_report(
    date: str | None = Query(default=None),
    platforms: str | None = Query(default=None),
    shops: str | None = Query(default=None),
    compare: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """看板聚合数据（6.4 契约，pydantic 强校验）。无数据返回 404 NO_DATA_FOR_DATE。"""
    date_str = _parse_date_or_422(date)
    platform_list = [p for p in (platforms or "").split(",") if p]
    shop_list = [s for s in (shops or "").split(",") if s]
    data = report_service.get_report_data(
        db, date_str, platform_list or None, shop_list or None, compare_enabled=compare
    )
    return ok(data.model_dump(mode="json"))


@router.post("/reports/daily/generate")
def generate(body: ScopeIn, db: Session = Depends(get_db)):
    """聚合 → 异常 → Excel → 摘要 → upsert DailyReport（幂等）。

    用 ScopeIn 强校验：platforms/shops 必须是字符串列表，裸字符串
    会穿透到聚合层 pandas isin() 抛 TypeError 变 500。
    """
    result = report_service.generate_daily_report(
        db, body.date, body.platforms, body.shops
    )
    return ok(result.model_dump())


@router.get("/reports/daily/{date_str}/export")
def export_daily_report(date_str: str, db: Session = Depends(get_db)):
    """下载日报 Excel（Content-Disposition RFC 5987 中文文件名）。"""
    path = report_service.get_export_file(db, date_str)
    # 中文文件名：ASCII 兜底 + RFC 5987 扩展
    fallback = path.name.encode("ascii", "ignore").decode() or "daily_report.xlsx"
    encoded = quote(path.name)
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
        )
    }
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/reports/daily/{date_str}/summary")
def daily_summary(date_str: str, db: Session = Depends(get_db)):
    """日报文字摘要。"""
    return ok(report_service.get_summary(db, date_str))


# ---------------------------------------------------------------------------
# 可回灌明细导出（2026-10-04 追加）
# 日报 Excel 是成品报表（聚合/占比/环比），缺 stat_date 与 platform_product_code
# 两个必需维度，**不能**作为导入源；这里导出的是原始明细粒度、且列名取各平台
# 适配器认识的规范源列名，因此导出文件可直接拖回上传页重新入库。
# ---------------------------------------------------------------------------


@router.get("/reports/daily/{date_str}/detail-exports")
def list_detail_exports(date_str: str, db: Session = Depends(get_db)):
    """生成（或重新生成）该日各平台的可回灌明细 CSV，返回下载清单。"""
    from backend.app.exporters.detail_export import detail_export_manifest

    return ok(detail_export_manifest(db, date_str))


@router.get("/reports/daily/{date_str}/detail-exports/{platform_key}")
def download_detail_export(date_str: str, platform_key: str, db: Session = Depends(get_db)):
    """下载单个平台的可回灌明细 CSV（中文文件名 RFC 5987）。"""
    from backend.app.core import ReportNotFoundError
    from backend.app.exporters.detail_export import build_detail_exports

    items = {it["platform_key"]: it for it in build_detail_exports(db, date_str)}
    item = items.get(platform_key)
    if item is None:
        raise ReportNotFoundError(
            f"{date_str} 没有平台 {platform_key} 的明细可导出",
            detail={"available": sorted(items)},
        )
    path = Path(item["path"])
    fallback = "detail_export.csv"
    encoded = quote(item["filename"])
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
            )
        },
    )


@router.get("/reports")
def history_reports(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """历史日报列表。"""
    return ok(report_service.list_reports(db, page, size))


@router.get("/reports/latest-date")
def latest_date(db: Session = Depends(get_db)):
    """最新有数据的日期（看板默认）。"""
    return ok({"date": report_service.latest_data_date(db)})


# ---------------------------------------------------------------------------
# 异常预警规则
# ---------------------------------------------------------------------------


@router.get("/anomaly-rules")
def list_anomaly_rules(db: Session = Depends(get_db)):
    rows = db.query(AnomalyRule).order_by(AnomalyRule.id).all()
    return ok(
        {
            "items": [
                AnomalyRuleOut(
                    id=r.id, name=r.name, metric=r.metric, scope=r.scope,
                    operator=r.operator, threshold=float(r.threshold),
                    min_base=float(r.min_base), enabled=r.enabled,
                ).model_dump()
                for r in rows
            ]
        }
    )


@router.put("/anomaly-rules/{rule_id}")
def update_anomaly_rule(rule_id: int, body: AnomalyRuleIn, db: Session = Depends(get_db)):
    """规则启停 / 阈值调整。"""
    from decimal import Decimal

    row = db.query(AnomalyRule).filter(AnomalyRule.id == rule_id).one_or_none()
    if row is None:
        from backend.app.core import AppError

        raise AppError("ANOMALY_RULE_NOT_FOUND", f"规则不存在：{rule_id}", status_code=404)
    data = body.model_dump(exclude_unset=True)
    if "threshold" in data and data["threshold"] is not None:
        row.threshold = Decimal(str(data.pop("threshold")))
    if "min_base" in data and data["min_base"] is not None:
        row.min_base = Decimal(str(data.pop("min_base")))
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return ok(
        AnomalyRuleOut(
            id=row.id, name=row.name, metric=row.metric, scope=row.scope,
            operator=row.operator, threshold=float(row.threshold),
            min_base=float(row.min_base), enabled=row.enabled,
        ).model_dump()
    )


# ---------------------------------------------------------------------------
# 下拉数据源（规划 7 /api/meta/*）
# ---------------------------------------------------------------------------


@router.get("/meta/categories")
def meta_categories(db: Session = Depends(get_db)):
    return ok({"items": report_service.meta_options(db)["categories"]})


@router.get("/meta/platforms")
def meta_platforms(db: Session = Depends(get_db)):
    return ok({"items": report_service.meta_options(db)["platforms"]})


@router.get("/meta/shops")
def meta_shops(db: Session = Depends(get_db)):
    return ok({"items": report_service.meta_options(db)["shops"]})
