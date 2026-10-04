"""日报服务编排（规划 T12）：聚合 → 异常 → Excel → 摘要 → 落库。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core import ReportNotFoundError
from backend.app.core.aggregator import build_daily_report
from backend.app.exporters.excel_report import generate_excel_report
from backend.app.exporters.text_summary import generate_text_summary
from backend.app.models.entities import DailyReport, SalesFact, Upload
from backend.app.schemas.report import DailyReportData, GenerateReportOut


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError) as e:
        from backend.app.core import ValidationError

        raise ValidationError(f"日期格式非法：{s}（应为 YYYY-MM-DD）") from e


def get_report_data(db: Session, date_str: str, platforms: list[str] | None = None,
                    shops: list[str] | None = None,
                    compare_enabled: bool = True) -> DailyReportData:
    """聚合看板数据（不强校验为 pydantic 由 API 层负责，此处返回结构化模型）。"""
    d = _parse_date(date_str)
    raw = build_daily_report(
        db, d,
        scope={"platforms": platforms or [], "shops": shops or []},
        compare_enabled=compare_enabled,
    )
    return DailyReportData.model_validate(raw)


def generate_daily_report(db: Session, date_str: str,
                          platforms: list[str] | None = None,
                          shops: list[str] | None = None) -> GenerateReportOut:
    """生成日报（幂等：同日期重复生成覆盖而非新增）。

    **带 scope（平台/店铺筛选）的生成不落库**：DailyReport 按 report_date
    唯一，若把子集口径写进去会静默覆盖同日期全量日报，历史 Excel 的
    口径被掉包且无任何痕迹。scope 版只计算并产出 Excel/摘要返回，
    report_id / excel_url 为 None（导出请用全量日报）。
    """
    d = _parse_date(date_str)
    persist = not platforms and not shops

    raw = build_daily_report(
        db, d,
        scope={"platforms": platforms or [], "shops": shops or []},
        compare_enabled=True,
    )

    # 数据来源文件清单（口径说明 Sheet 用）
    upload_ids = (
        db.query(SalesFact.upload_id)
        .filter(SalesFact.stat_date == d)
        .distinct()
        .all()
    )
    ids = [r[0] for r in upload_ids]
    uploads_info = []
    if ids:
        for u in db.query(Upload).filter(Upload.id.in_(ids)).all():
            uploads_info.append(
                {
                    "filename": u.filename,
                    "platform_key": u.platform_key,
                    "created_at": u.created_at.strftime("%Y-%m-%d %H:%M"),
                    "row_count_total": u.row_count_total,
                    "row_count_valid": u.row_count_valid,
                }
            )
    uploads_info.sort(key=lambda x: x["filename"])

    excel_path = generate_excel_report(
        raw, settings.export_dir, uploads_info=uploads_info,
        company=settings.company_name,
    )
    summary_text = generate_text_summary(raw, company=settings.company_name)

    # upsert：同日期覆盖（仅全量日报落库，scope 版见 docstring）
    if not persist:
        return GenerateReportOut(
            report_id=None,
            report_date=d.isoformat(),
            excel_url=None,
            summary_text=summary_text,
            char_count=len(summary_text),
        )
    row = db.query(DailyReport).filter(DailyReport.report_date == d).one_or_none()
    metrics_snapshot = {
        "overview": raw["overview"],
        "overview_raw": raw.get("overview_raw", {}),
        "data_completeness": raw["data_completeness"],
        "weekday": raw["weekday"],
        "anomaly_count": len(raw["anomalies"]),
    }
    if row is None:
        row = DailyReport(report_date=d)
        db.add(row)
    row.generated_at = datetime.now()
    row.scope_json = {"platforms": platforms or [], "shops": shops or []}
    row.excel_path = excel_path
    row.summary_text = summary_text
    row.metrics_json = metrics_snapshot
    db.commit()
    db.refresh(row)

    return GenerateReportOut(
        report_id=row.id,
        report_date=d.isoformat(),
        excel_url=f"/api/reports/daily/{d.isoformat()}/export",
        summary_text=summary_text,
        char_count=len(summary_text),
    )


def get_export_file(db: Session, date_str: str) -> Path:
    """取已生成日报的 Excel 文件路径（不存在时若有数据自动现场生成，无数据抛 REPORT_NOT_FOUND）。"""
    d = _parse_date(date_str)
    row = db.query(DailyReport).filter(DailyReport.report_date == d).one_or_none()
    if row is None or not row.excel_path or not Path(row.excel_path).is_file():
        has_sales = db.query(SalesFact.id).filter(SalesFact.stat_date == d).first() is not None
        if has_sales:
            generate_daily_report(db, date_str)
            row = db.query(DailyReport).filter(DailyReport.report_date == d).one_or_none()
            if row and row.excel_path and Path(row.excel_path).is_file():
                return Path(row.excel_path)
        raise ReportNotFoundError(f"{date_str} 的日报尚未生成且无有效销售数据")
    return Path(row.excel_path)


def get_summary(db: Session, date_str: str) -> dict:
    d = _parse_date(date_str)
    row = db.query(DailyReport).filter(DailyReport.report_date == d).one_or_none()
    if row is None or not row.summary_text:
        has_sales = db.query(SalesFact.id).filter(SalesFact.stat_date == d).first() is not None
        if has_sales:
            gen = generate_daily_report(db, date_str)
            return {"summary_text": gen.summary_text, "char_count": gen.char_count}
        raise ReportNotFoundError(f"{date_str} 的日报尚未生成且无有效销售数据")
    return {"summary_text": row.summary_text, "char_count": len(row.summary_text)}


def list_reports(db: Session, page: int = 1, size: int = 20) -> dict:
    query = db.query(DailyReport).order_by(DailyReport.report_date.desc())
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    items = [
        {
            "id": r.id,
            "report_date": r.report_date.isoformat(),
            "generated_at": r.generated_at.isoformat(timespec="seconds"),
            # 不暴露服务器物理路径，前端走导出端点
            "excel_url": f"/api/reports/daily/{r.report_date.isoformat()}/export",
            "has_summary": bool(r.summary_text),
            "scope": r.scope_json,
        }
        for r in rows
    ]
    return {"total": total, "page": page, "size": size, "items": items}


def latest_data_date(db: Session) -> str | None:
    """最新有数据的日期（看板默认日期）。"""
    row = db.query(SalesFact.stat_date).order_by(SalesFact.stat_date.desc()).first()
    return row[0].isoformat() if row else None


def meta_options(db: Session) -> dict:
    """/api/meta/* 下拉数据源。"""
    platforms = sorted({r[0] for r in db.query(SalesFact.platform_key).distinct().all()})
    categories = sorted(
        {r[0] for r in db.query(SalesFact.category).distinct().all() if r[0]}
    )
    shops = sorted(
        {
            r[0]
            for r in db.query(SalesFact.shop_name).distinct().all()
            if r[0]
        }
    )
    return {"categories": categories, "platforms": platforms, "shops": shops}
