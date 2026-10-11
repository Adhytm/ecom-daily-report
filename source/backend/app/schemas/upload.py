"""上传与解析相关的 API Schema（规划 7）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParseMeta(BaseModel):
    """文件物理解析元信息（规划 6.1 第 8 步）。"""

    encoding: str | None = None
    header_row_index: int | None = None
    detected_columns: list[str] = Field(default_factory=list)
    dropped_empty_rows: int = 0
    warnings: list[str] = Field(default_factory=list)
    sheet_name: str | None = None  # 实际读取的工作表名
    sheet_names: list[str] = Field(default_factory=list)  # 文件内全部工作表名


class DetectCandidate(BaseModel):
    """平台自动识别候选（打分排序）。"""

    platform_key: str
    display_name: str
    score: float


class NormalizeStats(BaseModel):
    """归一化统计（规划 6.2）。"""

    total: int = 0
    valid: int = 0
    filtered: int = 0
    duplicated: int = 0
    failed: int = 0


class UploadPreviewItem(BaseModel):
    """POST /api/uploads 返回的单文件解析结果。"""

    upload_id: int
    filename: str
    detected_platform: str | None = None
    detect_confidence: float = 0.0
    candidates: list[DetectCandidate] = Field(default_factory=list)
    parse_meta: ParseMeta
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)
    stats: NormalizeStats
    semantics_note: str | None = None
    duplicate_of: int | None = None  # sha256 命中历史上传时提示


class UploadListItem(BaseModel):
    """GET /api/uploads 列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    sha256: str
    platform_key: str | None
    adapter_version: int | None
    status: str
    row_count_total: int
    row_count_valid: int
    row_count_error: int
    error_message: str | None
    created_at: datetime


class UploadErrorItem(BaseModel):
    """行级错误明细。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    upload_id: int
    row_number: int
    raw_content: str | None
    error_type: str
    error_message: str | None


class CommitResult(BaseModel):
    """POST /api/uploads/{id}/commit 返回。"""

    upload_id: int
    row_count_valid: int
    unmapped_count: int
    suggestions_count: int
    # 本批入库数据覆盖的最大统计日期：前端「入库后出报」应以它为目标日期，
    # 而不是全库最新日期（库里可能有更晚日期的其他数据）
    max_stat_date: str | None = None
