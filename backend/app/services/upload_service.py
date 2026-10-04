"""上传编排服务（规划 7 / T07）：解析 → 归一化 → 映射 → 落库。

两阶段设计：
- ``ingest_file``：保存原始文件、自动识别平台、解析 + 归一化，
  生成 Upload（status=parsed）与行级错误明细，供前端预览；
- ``commit_upload``：正式把归一化行写入 SalesFact 并触发 SKU 映射
  （status=committed）。提交时以**当前**适配器配置重新解析，保证
  用户在预览与提交之间修改适配器后仍得到正确结果。
"""

from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import PROJECT_ROOT, settings
from backend.app.core import (
    AdapterNotFoundError,
    DuplicateUploadCommittedError,
    FileParseError,
    UploadNotFoundError,
)
from backend.app.core.adapter_registry import detect_platform, get_adapter
from backend.app.core.file_parser import parse_file
from backend.app.core.normalizer import NormalizedRow, normalize
from backend.app.core.sku_mapper import attach_sku_ids
from backend.app.models.entities import DailyReport, SalesFact, Upload, UploadError
from backend.app.schemas.upload import (
    CommitResult,
    DetectCandidate,
    NormalizeStats,
    ParseMeta,
    UploadPreviewItem,
)

# 原始文件存放目录（供"改选平台后重新解析"与提交时复用）
_UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"

_SAFE_NAME_RE = re.compile(r"[^\w.\-\u4e00-\u9fff]+")

# 提交互斥锁：sha256 查重是 check-then-act，无锁时同一文件的两个并发
# commit 会双双通过查重、同一份账单入库两次（GMV 静默翻倍）。
# 单进程 uvicorn 下一把进程内锁即可覆盖。
_COMMIT_LOCK = threading.Lock()


def _invalidate_reports_for_upload(db: Session, upload_id: int) -> None:
    """使受某上传影响的日报失效（删除 DailyReport 行）。

    删除/重解析上传后，其 SalesFact 已被清除，但已生成的日报行与磁盘
    Excel 仍在 —— 不处理就会出现「数据删了、旧报表照常导出」。这里只删
    数据库行，磁盘 Excel 保留（可能已被用户存档），下次 export/summary
    会按现有数据现场重生成。
    """
    dates = [
        r[0]
        for r in db.query(SalesFact.stat_date)
        .filter(SalesFact.upload_id == upload_id)
        .distinct()
        .all()
    ]
    if dates:
        db.query(DailyReport).filter(DailyReport.report_date.in_(dates)).delete(
            synchronize_session=False
        )


def _store_path(upload_id: int, filename: str) -> Path:
    safe = _SAFE_NAME_RE.sub("_", filename)[:120] or "upload.bin"
    return _UPLOADS_DIR / f"{upload_id}_{safe}"


def _to_preview_rows(rows: list[NormalizedRow], limit: int = 20) -> list[dict]:
    """归一化行的预览（前 20 行）。"""
    preview = []
    for r in rows[:limit]:
        preview.append(
            {
                "stat_date": r.stat_date.isoformat(),
                "platform_key": r.platform_key,
                "shop_code": r.shop_code,
                "shop_name": r.shop_name,
                "platform_product_code": r.platform_product_code,
                "product_name": r.product_name,
                "category": r.category,
                "order_qty": r.order_qty,
                "paid_qty": r.paid_qty,
                "gmv": str(r.gmv),
                "refund_amount": str(r.refund_amount) if r.refund_amount is not None else None,
                "net_amount": str(r.net_amount),
                "visitors": r.visitors,
                "buyers": r.buyers,
                "ad_cost": str(r.ad_cost) if r.ad_cost is not None else None,
            }
        )
    return preview


def _parse_with_adapter(db: Session, filename: str, raw: bytes, platform_key: str):
    """按指定平台适配器完成解析 + 归一化。"""
    bundle = get_adapter(db, platform_key)
    df, meta = parse_file(raw, filename, bundle.spec.file_matching)
    result = normalize(df, bundle.spec, meta.header_row_index)
    return bundle, meta, result


def _persist_errors(db: Session, upload: Upload, errors: list) -> None:
    """行级错误明细落库（含 filtered_out / duplicate_row 非失败记录）。"""
    for e in errors:
        db.add(
            UploadError(
                upload_id=upload.id,
                row_number=e.row_number,
                raw_content=e.raw_content,
                error_type=e.error_type,
                error_message=e.error_message,
            )
        )


def _mark_failed(db: Session, upload: Upload, error: Exception) -> None:
    """把上传记录标记为 failed 并落库（原始文件已提前存盘，可 reparse 复活）。"""
    upload.status = "failed"
    upload.error_message = str(error)
    db.commit()


def ingest_file(db: Session, filename: str, raw: bytes,
                platform_key: str | None = None) -> UploadPreviewItem:
    """单文件上传：识别 → 解析 → 归一化 → 登记为 parsed。

    - 用户显式指定平台时直接用该平台适配器解析（探测不作为门槛），
      必需列缺失会如实抛出 ``MissingRequiredColumnError``；
    - 未指定平台时先用宽松探测定位表头，再按 detect_keywords 打分；
    - **先登记 + 落盘原始文件，再解析**：识别/解析失败的记录同样持有
      原始字节，前端可改选平台走 ``reparse_upload`` 复活，不会出现
      "提示用户重新解析但原始文件根本没存"的死路。
    """
    sha = hashlib.sha256(raw).hexdigest()

    duplicate_of = (
        db.query(Upload.id).filter(Upload.sha256 == sha).order_by(Upload.id.desc()).first()
    )
    duplicate_of = duplicate_of[0] if duplicate_of else None

    # 先登记（status=parsing 为瞬时态）并落盘原始文件
    upload = Upload(
        filename=filename,
        sha256=sha,
        platform_key=None,
        status="parsing",
        created_at=datetime.now(),
    )
    db.add(upload)
    db.flush()  # 取 upload.id 用于存盘
    path = _store_path(upload.id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    candidates: list[dict] = []
    confidence = 0.0

    if platform_key is not None:
        try:
            bundle, meta, result = _parse_with_adapter(db, filename, raw, platform_key)
        except Exception as e:
            _mark_failed(db, upload, e)
            raise
        candidates = [
            {"platform_key": platform_key, "display_name": bundle.display_name,
             "score": 1.0, "confidence": 1.0}
        ]
        confidence = 1.0
    else:
        # 平台自动识别：宽松探测出原始表头后按 detect_keywords 打分
        from backend.app.core.file_parser import SUPPORTED_EXTENSIONS
        from backend.app.schemas.adapter import FileMatching

        probe_fm = FileMatching(
            extensions=list(SUPPORTED_EXTENSIONS),  # 探测阶段不设限，与显式指定平台的行为一致
            encoding_candidates=["utf-8-sig", "gb18030", "utf-8", "utf-16"],
            header_row="auto",
            auto_header_hints=["商品ID", "商品名称", "统计时间", "统计日期", "日期", "商品编号"],
        )
        try:
            df, meta = parse_file(raw, filename, probe_fm)
            columns = meta.detected_columns
        except FileParseError as e:
            _mark_failed(db, upload, e)
            # detail 里带上 upload_id（前端可据此直接发起 reparse），
            # 但保留原异常类型与错误码（ENCODING_ERROR 等不能被吞成 FILE_PARSE_ERROR）
            e.detail = (
                {**(e.detail or {}), "upload_id": upload.id}
                if isinstance(e.detail, dict)
                else {"upload_id": upload.id, "original": e.detail}
            )
            raise

        candidates = detect_platform(columns, filename)
        if candidates and candidates[0]["score"] > 0:
            platform_key = candidates[0]["platform_key"]
            confidence = candidates[0].get("confidence", 0.0)
        else:
            platform_key = None

        if platform_key is None:
            # 无法识别：标记 failed，前端可改选平台后重新解析
            err = FileParseError(
                "无法自动识别平台，请手工选择平台后重新解析",
                detail={"upload_id": upload.id, "columns": columns},
            )
            _mark_failed(db, upload, err)
            raise err
        try:
            bundle, meta, result = _parse_with_adapter(db, filename, raw, platform_key)
        except Exception as e:
            _mark_failed(db, upload, e)
            raise

    upload.platform_key = platform_key
    upload.adapter_version = bundle.version
    upload.status = "parsed"
    upload.row_count_total = result.stats["total"]
    upload.row_count_valid = result.stats["valid"]
    upload.row_count_error = (
        result.stats["failed"] + result.stats["filtered"] + result.stats["duplicated"]
    )
    _persist_errors(db, upload, result.errors)
    db.commit()

    return UploadPreviewItem(
        upload_id=upload.id,
        filename=filename,
        detected_platform=platform_key,
        detect_confidence=float(confidence),
        candidates=[
            DetectCandidate(
                platform_key=c["platform_key"],
                display_name=c["display_name"],
                score=c["score"],
            )
            for c in candidates
        ],
        parse_meta=ParseMeta(
            encoding=meta.encoding,
            header_row_index=meta.header_row_index,
            detected_columns=meta.detected_columns,
            dropped_empty_rows=meta.dropped_empty_rows,
            warnings=meta.warnings,
            sheet_name=meta.sheet_name,
            sheet_names=meta.sheet_names,
        ),
        preview_rows=_to_preview_rows(result.rows),
        stats=NormalizeStats(**result.stats),
        semantics_note=result.semantics_note,
        duplicate_of=duplicate_of,
    )


def reparse_upload(db: Session, upload_id: int, platform_key: str) -> UploadPreviewItem:
    """用户改选平台后重新解析（复用已存盘的原始文件）。"""
    upload = db.query(Upload).filter(Upload.id == upload_id).one_or_none()
    if upload is None:
        raise UploadNotFoundError(f"上传记录不存在：{upload_id}")

    path = _store_path(upload.id, upload.filename)
    if not path.is_file():
        raise FileParseError(f"原始文件已丢失，请重新上传：{upload.filename}")

    raw = path.read_bytes()
    bundle, meta, result = _parse_with_adapter(db, upload.filename, raw, platform_key)

    # 覆盖旧结果：清空错误明细与已落库明细（换适配器后旧明细已失效），
    # 重置统计与平台。状态回退到 parsed，等待用户重新 commit。
    _invalidate_reports_for_upload(db, upload.id)
    db.query(UploadError).filter(UploadError.upload_id == upload.id).delete()
    db.query(SalesFact).filter(SalesFact.upload_id == upload.id).delete(
        synchronize_session=False
    )
    upload.platform_key = platform_key
    upload.adapter_version = bundle.version
    upload.status = "parsed"
    upload.row_count_total = result.stats["total"]
    upload.row_count_valid = result.stats["valid"]
    upload.row_count_error = (
        result.stats["failed"] + result.stats["filtered"] + result.stats["duplicated"]
    )
    upload.error_message = None
    _persist_errors(db, upload, result.errors)
    db.commit()

    return UploadPreviewItem(
        upload_id=upload.id,
        filename=upload.filename,
        detected_platform=platform_key,
        detect_confidence=1.0,
        candidates=[
            DetectCandidate(platform_key=platform_key, display_name=bundle.display_name, score=1.0)
        ],
        parse_meta=ParseMeta(
            encoding=meta.encoding,
            header_row_index=meta.header_row_index,
            detected_columns=meta.detected_columns,
            dropped_empty_rows=meta.dropped_empty_rows,
            warnings=meta.warnings,
            sheet_name=meta.sheet_name,
            sheet_names=meta.sheet_names,
        ),
        preview_rows=_to_preview_rows(result.rows),
        stats=NormalizeStats(**result.stats),
        semantics_note=result.semantics_note,
    )


def preview_upload(db: Session, upload_id: int) -> UploadPreviewItem:
    """只读预览：按当前适配器重解析已存盘文件并返回预览，**不改动任何状态**。

    用途：前端刷新/重进后恢复「待入库文件清单」的卡片（parsed 记录只持久化
    了行数统计，预览行与识别元信息靠这个端点现场重算）。
    """
    upload = db.query(Upload).filter(Upload.id == upload_id).one_or_none()
    if upload is None:
        raise UploadNotFoundError(f"上传记录不存在：{upload_id}")
    if upload.platform_key is None:
        raise FileParseError("上传记录未识别平台，无法预览，请先选择平台重新解析")

    path = _store_path(upload.id, upload.filename)
    if not path.is_file():
        raise FileParseError(f"原始文件已丢失，请重新上传：{upload.filename}")

    raw = path.read_bytes()
    bundle, meta, result = _parse_with_adapter(db, upload.filename, raw, upload.platform_key)

    duplicate_of = (
        db.query(Upload.id)
        .filter(Upload.sha256 == upload.sha256, Upload.id != upload.id)
        .order_by(Upload.id.desc())
        .first()
    )

    return UploadPreviewItem(
        upload_id=upload.id,
        filename=upload.filename,
        detected_platform=upload.platform_key,
        detect_confidence=1.0,
        candidates=[
            DetectCandidate(
                platform_key=upload.platform_key,
                display_name=bundle.display_name,
                score=1.0,
            )
        ],
        parse_meta=ParseMeta(
            encoding=meta.encoding,
            header_row_index=meta.header_row_index,
            detected_columns=meta.detected_columns,
            dropped_empty_rows=meta.dropped_empty_rows,
            warnings=meta.warnings,
            sheet_name=meta.sheet_name,
            sheet_names=meta.sheet_names,
        ),
        preview_rows=_to_preview_rows(result.rows),
        stats=NormalizeStats(**result.stats),
        semantics_note=result.semantics_note,
        duplicate_of=duplicate_of[0] if duplicate_of else None,
    )


def commit_upload(db: Session, upload_id: int, force: bool = False) -> CommitResult:
    """正式落库：重新解析 → 写 SalesFact → SKU 映射（幂等）。

    重复内容防线：同一 sha256 若已有**其它** committed 记录，拒绝提交
    （409 DUPLICATE_UPLOAD_COMMITTED），防止同一份账单二次入库导致
    GMV/毛利静默翻倍。用户明确确认后由前端带 ``force=True`` 覆盖。

    查重与写入整体在进程内互斥锁下执行，避免两个并发 commit 同时通过
    check-then-act 查重。
    """
    with _COMMIT_LOCK:
        return _commit_upload_locked(db, upload_id, force=force)


def _commit_upload_locked(db: Session, upload_id: int, force: bool = False) -> CommitResult:
    upload = db.query(Upload).filter(Upload.id == upload_id).one_or_none()
    if upload is None:
        raise UploadNotFoundError(f"上传记录不存在：{upload_id}")
    if upload.status == "committed":
        # 幂等：已提交的上传直接返回既有统计。
        # unmapped 按平台商品码去重计数，与首次 commit 返回的
        # attach_sku_ids 统计口径一致（那里按"码"计数，不按行）
        from sqlalchemy import func

        facts = db.query(SalesFact).filter(SalesFact.upload_id == upload.id)
        valid = facts.count()
        unmapped = (
            db.query(SalesFact.platform_product_code)
            .filter(
                SalesFact.upload_id == upload.id,
                SalesFact.sku_id.is_(None),
            )
            .distinct()
            .count()
        )
        max_date = (
            db.query(func.max(SalesFact.stat_date))
            .filter(SalesFact.upload_id == upload.id)
            .scalar()
        )
        return CommitResult(
            upload_id=upload.id, row_count_valid=valid,
            unmapped_count=unmapped, suggestions_count=0,
            max_stat_date=max_date.isoformat() if max_date else None,
        )
    if upload.platform_key is None:
        raise FileParseError("上传记录未识别平台，无法提交")

    if not force:
        dup = (
            db.query(Upload)
            .filter(
                Upload.sha256 == upload.sha256,
                Upload.status == "committed",
                Upload.id != upload.id,
            )
            .order_by(Upload.id.desc())
            .first()
        )
        if dup is not None:
            raise DuplicateUploadCommittedError(
                f"该文件与已入库的上传 #{dup.id}（{dup.filename}）内容完全相同，"
                "重复入库会让统计数据翻倍。如确认需要重复入库，请显式勾选确认后重试。",
                detail={"duplicate_of": dup.id, "duplicate_filename": dup.filename},
            )

    path = _store_path(upload.id, upload.filename)
    if not path.is_file():
        raise FileParseError(f"原始文件已丢失，请重新上传：{upload.filename}")

    raw = path.read_bytes()
    bundle, meta, result = _parse_with_adapter(
        db, upload.filename, raw, upload.platform_key
    )

    # 重建错误明细
    db.query(UploadError).filter(UploadError.upload_id == upload.id).delete()
    _persist_errors(db, upload, result.errors)

    # 提交幂等：先清除本 upload 已有明细再写入。SalesFact 无唯一约束，
    # 若不先删除，「已提交 → reparse 回退到 parsed → 再次提交」会重复
    # 插入整批明细，导致聚合金额成倍放大。
    db.query(SalesFact).filter(SalesFact.upload_id == upload.id).delete(
        synchronize_session=False
    )

    # SKU 映射（4 级优先级；未映射行仅置 sku_id=NULL，不丢弃任何行）
    stats = attach_sku_ids(db, result.rows, upload.platform_key)

    for r in result.rows:
        db.add(SalesFact(**r.to_db_dict(upload_id=upload.id)))

    upload.status = "committed"
    upload.adapter_version = bundle.version
    upload.row_count_total = result.stats["total"]
    upload.row_count_valid = result.stats["valid"]
    upload.row_count_error = (
        result.stats["failed"] + result.stats["filtered"] + result.stats["duplicated"]
    )
    db.commit()

    max_date = max((r.stat_date for r in result.rows), default=None)
    return CommitResult(
        upload_id=upload.id,
        row_count_valid=result.stats["valid"],
        unmapped_count=stats["unmapped"],
        suggestions_count=stats["suggestions"],
        max_stat_date=max_date.isoformat() if max_date else None,
    )


def delete_upload(db: Session, upload_id: int) -> None:
    """删除上传及其 SalesFact / 错误明细（级联），并使受影响日报失效。"""
    upload = db.query(Upload).filter(Upload.id == upload_id).one_or_none()
    if upload is None:
        raise UploadNotFoundError(f"上传记录不存在：{upload_id}")
    _invalidate_reports_for_upload(db, upload.id)
    db.query(SalesFact).filter(SalesFact.upload_id == upload.id).delete(
        synchronize_session=False
    )
    db.query(UploadError).filter(UploadError.upload_id == upload.id).delete(
        synchronize_session=False
    )
    path = _store_path(upload.id, upload.filename)
    db.delete(upload)
    db.commit()
    if path.is_file():
        path.unlink(missing_ok=True)
