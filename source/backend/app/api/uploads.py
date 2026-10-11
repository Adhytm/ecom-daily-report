"""上传相关 API（规划 7）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.config import settings
from backend.app.core import AppError, ValidationError
from backend.app.core.file_parser import SUPPORTED_EXTENSIONS
from backend.app.main import fail, ok
from backend.app.models.entities import Upload, UploadError
from backend.app.schemas.upload import UploadErrorItem, UploadListItem
from backend.app.services import upload_service

router = APIRouter(tags=["uploads"])
logger = logging.getLogger(__name__)


def _error_payload(filename: str, e: Exception) -> dict:
    """单文件失败的错误载荷。

    AppError 保留业务码与文案；其余内部异常一律返回固定文案
    （绝对路径、磁盘错误等内部细节只进日志，不透给前端）。
    """
    if isinstance(e, AppError):
        return {
            "filename": filename,
            "error": {"code": e.code, "message": e.message, "detail": e.detail},
        }
    logger.exception("upload ingest failed: %s", filename)
    return {
        "filename": filename,
        "error": {
            "code": "FILE_PARSE_ERROR",
            "message": f"文件 {filename} 处理失败，请检查文件内容后重试",
            "detail": None,
        },
    }

# 扩展名白名单的单一事实来源在 file_parser.SUPPORTED_EXTENSIONS
ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS


def _validate_file_size(size: int, filename: str) -> None:
    if size > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError(
            f"文件 {filename} 超过 {settings.max_upload_mb}MB 限制",
            detail={"size": size},
        )


@router.post("/uploads")
async def upload_files(
    files: list[UploadFile] = File(...),
    platform_key: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """批量上传：multipart files[]，可选 platform_key 手工指定平台。

    返回每个文件的识别结果 + 前 20 行归一化预览 + 行数统计。
    """
    if not files:
        raise ValidationError("未收到任何文件")
    if platform_key == "":
        platform_key = None

    results = []
    errors = []
    for f in files:
        filename = f.filename or "unnamed"
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            errors.append(
                {
                    "filename": filename,
                    "error": {
                        "code": "FILE_PARSE_ERROR",
                        "message": (
                            f"不支持的文件类型：{filename}。"
                            f"当前支持：{'、'.join(e.lstrip('.').upper() for e in ALLOWED_EXTENSIONS)}"
                        ),
                        "detail": {"supported": list(ALLOWED_EXTENSIONS)},
                    },
                }
            )
            continue
        # 先用 Starlette 已统计的 size 拦截超限文件：否则 await f.read()
        # 会把整个文件读入内存后才报错，50MB 限制的防护形同虚设
        if f.size is not None:
            try:
                _validate_file_size(f.size, filename)
            except Exception as e:  # 单文件超限不影响其余文件
                errors.append(_error_payload(filename, e))
                continue
        raw = await f.read()
        try:
            # f.size 为 None 时，再按实际读到的字节数校验一次
            _validate_file_size(len(raw), filename)
            results.append(upload_service.ingest_file(db, filename, raw, platform_key).model_dump())
        except Exception as e:  # 单文件失败不影响其余文件
            # 必须 rollback：异常若发生在 DB 写途中，会话进入
            # PendingRollback 状态，不处理会让同批后续文件连锁失败
            db.rollback()
            errors.append(_error_payload(filename, e))
    if not results and errors:
        # 全部失败：返回首个错误的业务码与 400 状态
        first = errors[0]["error"]
        return fail(first["code"], first["message"], {"files": errors}, status_code=400)
    return ok({"files": results, "errors": errors})


@router.post("/uploads/{upload_id}/commit")
def commit(
    upload_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """正式落库 SalesFact 并触发 SKU 映射。

    同一文件内容已有 committed 记录时返回 409 拒绝；前端在用户显式
    确认后带 ``?force=true`` 覆盖。
    """
    result = upload_service.commit_upload(db, upload_id, force=force)
    return ok(result.model_dump())


@router.get("/uploads/{upload_id}/preview")
def preview(upload_id: int, db: Session = Depends(get_db)):
    """只读重解析预览（不改动状态）：前端刷新后恢复待入库卡片用。"""
    result = upload_service.preview_upload(db, upload_id)
    return ok(result.model_dump())


@router.post("/uploads/{upload_id}/reparse")
def reparse(upload_id: int, body: dict, db: Session = Depends(get_db)):
    """识别失败或用户改选平台后重新解析（规划 8.2 第 4 步）。"""
    platform_key = body.get("platform_key")
    if not platform_key:
        raise ValidationError("platform_key 不能为空")
    result = upload_service.reparse_upload(db, upload_id, platform_key)
    return ok(result.model_dump())


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: int, db: Session = Depends(get_db)):
    """删除上传及其 SalesFact（级联）。"""
    upload_service.delete_upload(db, upload_id)
    return ok({"deleted": upload_id})


@router.get("/uploads")
def list_uploads(
    status: str | None = Query(default=None),
    platform_key: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """上传记录分页列表。"""
    q = db.query(Upload)
    if status:
        q = q.filter(Upload.status == status)
    if platform_key:
        q = q.filter(Upload.platform_key == platform_key)
    total = q.count()
    rows = (
        q.order_by(Upload.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                UploadListItem.model_validate(u).model_dump(mode="json") for u in rows
            ],
        }
    )


@router.get("/uploads/{upload_id}/errors")
def upload_errors(
    upload_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """行级错误明细（分页）。"""
    q = db.query(UploadError).filter(UploadError.upload_id == upload_id)
    total = q.count()
    rows = q.order_by(UploadError.row_number).offset((page - 1) * size).limit(size).all()
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                UploadErrorItem.model_validate(e).model_dump(mode="json") for e in rows
            ],
        }
    )


@router.post("/uploads/scan-incoming")
def scan_incoming(
    auto_commit: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """扫描 data/incoming 目录，自动识别并落库新报表文件。"""
    from backend.app.services.auto_ingest_service import scan_and_ingest_incoming

    res = scan_and_ingest_incoming(db, auto_commit=auto_commit)
    return ok(res)

