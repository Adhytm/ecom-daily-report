"""模拟数据生成 API（规划 7：POST /api/mock-data/generate）。"""

from __future__ import annotations

import hashlib
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.config import PROJECT_ROOT
from backend.app.main import ok
from backend.app.models.entities import Upload
from backend.app.services import upload_service
from backend.scripts.gen_mock_data import generate as generate_mock_files

router = APIRouter(tags=["mock"])

# 模块级共享锁：生成器向 samples/mock 写文件，必须串行化并发请求。
# （在请求内 with threading.Lock() 会每次新建一把无人竞争的锁，等于没锁）
_GEN_LOCK = threading.Lock()


class MockGenerateIn(BaseModel):
    days: int = Field(default=35, ge=1, le=365)
    skus: int = Field(default=40, ge=4, le=500)
    # True 时生成后直接入库（识别 + commit），前端「一键体验仿真数据」用，
    # 用户不用再自己找文件拖回来——否则引导流程在「文件生成在服务器目录」断掉
    ingest: bool = Field(default=False)


@router.post("/mock-data/generate")
def generate_mock_data(body: MockGenerateIn, db: Session = Depends(get_db)):
    """调用生成器产出 4 平台模拟原始文件 + manifest.json；可选直接入库。"""
    outdir = PROJECT_ROOT / "samples" / "mock"
    # 生成器为整目录写入，加锁避免并发请求互写同一目录
    with _GEN_LOCK:
        manifest = generate_mock_files(days=body.days, skus=body.skus, outdir=str(outdir))

    ingest_result = None
    if body.ingest:
        ingest_result = {"ingested": [], "skipped_duplicates": [], "failed": []}
        for platform_key, fname in manifest["files"].items():
            raw = (outdir / fname).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            exists = (
                db.query(Upload)
                .filter(Upload.sha256 == sha, Upload.status == "committed")
                .first()
            )
            if exists is not None:
                ingest_result["skipped_duplicates"].append(
                    {"filename": fname, "reason": "相同内容已入库，跳过"}
                )
                continue
            try:
                preview = upload_service.ingest_file(
                    db, fname, raw, platform_key=platform_key
                )
                commit_res = upload_service.commit_upload(db, preview.upload_id)
                ingest_result["ingested"].append(
                    {
                        "filename": fname,
                        "upload_id": preview.upload_id,
                        "platform": platform_key,
                        "valid_rows": commit_res.row_count_valid,
                    }
                )
            except Exception as e:  # 单文件失败不影响其余文件
                # 异常若发生在 DB 写途中必须回滚，否则同批后续文件连锁失败
                db.rollback()
                ingest_result["failed"].append({"filename": fname, "error": str(e)})

    return ok(
        {
            "outdir": str(outdir),
            "files": list(manifest["files"].values()),
            "manifest_path": str(outdir / "manifest.json"),
            "report_date": manifest["report_date"],
            "ingest": ingest_result,
        }
    )
