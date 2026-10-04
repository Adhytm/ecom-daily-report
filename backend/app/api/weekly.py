"""电商销售周报 API。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.core import ValidationError
from backend.app.main import ok
from backend.app.services import weekly_service

router = APIRouter(tags=["weekly"])


def _parse_date_or_422(date_str: str | None) -> str:
    if not date_str:
        raise ValidationError("缺少 date 参数（YYYY-MM-DD）")
    return date_str


@router.get("/reports/weekly")
def weekly_report(
    date: str | None = Query(default=None, description="指定周内任意一天的日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """获取指定周的聚合周报数据（大盘、7天走势、分平台、Top爆款）。"""
    d_str = _parse_date_or_422(date)
    data = weekly_service.get_weekly_data(db, d_str)
    return ok(data)


@router.get("/reports/weekly/export")
def export_weekly_report(
    date: str | None = Query(default=None, description="指定周内任意一天的日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """一键下载周报 Excel（4 个 Sheet，带格式与周环比）。"""
    d_str = _parse_date_or_422(date)
    path = weekly_service.export_weekly_report(db, d_str)
    fallback = path.name.encode("ascii", "ignore").decode() or "weekly_report.xlsx"
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


@router.get("/reports/weekly/summary")
def weekly_summary(
    date: str | None = Query(default=None, description="指定周内任意一天的日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """获取周报微信/钉钉群发文字摘要。"""
    d_str = _parse_date_or_422(date)
    data = weekly_service.get_weekly_data(db, d_str)
    return ok({"summary_text": data["summary_text"], "char_count": len(data["summary_text"])})
