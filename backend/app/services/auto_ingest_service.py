"""自动文件接入服务：Drop Folder 目录监听与断档批量扫描落库。

功能：
- 扫描 settings.incoming_dir（默认 data/incoming/）目录下的所有 .csv / .xlsx 文件
- 忽略隐藏文件、临时下载文件（.crdownload, .tmp, ~*）
- 重复文件（sha256 已存在）自动归档到 incoming/processed/，不重复解析
- 识别平台成功后自动调用 ingest_file + commit_upload 落库
- 成功文件移动到 incoming/processed/，错误文件移动到 incoming/failed/
- 既支持 CLI 单次扫描 / 循环轮询，也支持 API 触发
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.file_parser import SUPPORTED_EXTENSIONS
from backend.app.models.entities import Upload
from backend.app.services import upload_service

IGNORED_PREFIXES = (".", "~")
IGNORED_SUFFIXES = (".tmp", ".crdownload", ".part")
# 扩展名白名单的单一事实来源在 file_parser.SUPPORTED_EXTENSIONS
ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS


def scan_and_ingest_incoming(
    db: Session,
    folder: Path | None = None,
    auto_commit: bool = True,
) -> dict[str, Any]:
    incoming = folder or settings.incoming_dir
    processed_dir = incoming / "processed"
    failed_dir = incoming / "failed"

    incoming.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    for item in incoming.iterdir():
        if not item.is_file():
            continue
        name = item.name
        if any(name.startswith(p) for p in IGNORED_PREFIXES):
            continue
        if any(name.lower().endswith(s) for s in IGNORED_SUFFIXES):
            continue
        if not name.lower().endswith(ALLOWED_EXTENSIONS):
            continue
        candidates.append(item)

    candidates.sort(key=lambda p: p.stat().st_mtime)

    committed: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for file_path in candidates:
        fname = file_path.name
        try:
            content = file_path.read_bytes()
        except Exception as e:
            failed.append({"filename": fname, "error": f"读取文件失败: {e}"})
            continue

        if not content:
            continue

        sha256 = hashlib.sha256(content).hexdigest()
        existing = (
            db.query(Upload)
            .filter(Upload.sha256 == sha256, Upload.status == "committed")
            .first()
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if existing is not None:
            target = processed_dir / f"{timestamp}_{fname}"
            try:
                shutil.move(str(file_path), str(target))
            except Exception:
                file_path.unlink(missing_ok=True)
            skipped_duplicates.append(
                {"filename": fname, "sha256": sha256, "reason": "已存在相同记录并已提交"}
            )
            continue

        try:
            preview = upload_service.ingest_file(db, fname, content)
            if not preview.detected_platform:
                target = failed_dir / f"{timestamp}_{fname}"
                try:
                    shutil.move(str(file_path), str(target))
                except Exception:
                    pass
                failed.append({"filename": fname, "error": "无法自动识别文件所属平台"})
                continue

            if auto_commit:
                commit_res = upload_service.commit_upload(db, preview.upload_id)
                target = processed_dir / f"{timestamp}_{fname}"
                try:
                    shutil.move(str(file_path), str(target))
                except Exception:
                    file_path.unlink(missing_ok=True)
                committed.append(
                    {
                        "filename": fname,
                        "upload_id": preview.upload_id,
                        "platform": preview.detected_platform,
                        "valid_rows": commit_res.row_count_valid,
                        "unmapped_sku": commit_res.unmapped_count,
                    }
                )
            else:
                committed.append(
                    {
                        "filename": fname,
                        "upload_id": preview.upload_id,
                        "platform": preview.detected_platform,
                        "status": "parsed",
                    }
                )
        except Exception as e:
            # 异常若发生在 DB 写途中必须回滚，否则同批后续文件连锁失败
            db.rollback()
            target = failed_dir / f"{timestamp}_{fname}"
            try:
                shutil.move(str(file_path), str(target))
            except Exception:
                pass
            failed.append({"filename": fname, "error": str(e)})

    return {
        "scanned_files": len(candidates),
        "committed": committed,
        "skipped_duplicates": skipped_duplicates,
        "failed": failed,
    }
