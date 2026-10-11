"""AI 智能诊断 API（本地模型与云端 API）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.main import ok
from backend.app.services import ai_service

router = APIRouter(tags=["ai"])


class AiDiagnoseIn(BaseModel):
    date: str = Field(..., description="诊断日期 YYYY-MM-DD")
    provider: str = Field(default="local", description="模型提供方：local（本地 Ollama）或 cloud（商业云端）")
    base_url: str | None = Field(default=None, description="API 基础地址，如 http://localhost:11434/v1")
    api_key: str | None = Field(default=None, description="API Key（云端必填，本地可免）")
    model: str | None = Field(default=None, description="模型名称，如 qwen2.5:7b 或 deepseek-chat")
    custom_prompt: str | None = Field(default=None, description="自定义追问或补充要求")


@router.post("/ai/diagnose")
async def diagnose(body: AiDiagnoseIn, db: Session = Depends(get_db)):
    """对指定日期的销售数据进行 AI 智能商业诊断。"""
    res = await ai_service.run_ai_diagnosis(
        db=db,
        date_str=body.date,
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        custom_prompt=body.custom_prompt,
    )
    return ok(res)
