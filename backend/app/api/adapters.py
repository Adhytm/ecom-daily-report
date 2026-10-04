"""适配器管理 API（规划 7）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db
from backend.app.core import AdapterNotFoundError
from backend.app.core.adapter_registry import (
    get_adapter,
    get_all_adapters,
    list_builtin_keys,
    load_builtin,
    reset_to_builtin,
    save_override,
    validate_yaml_text,
)
from backend.app.main import ok
from backend.app.schemas.adapter import build_semantics_note

router = APIRouter(tags=["adapters"])


@router.get("/adapters")
def list_adapters(db: Session = Depends(get_db)):
    """全部适配器列表（含映射列数）。"""
    items = []
    for bundle in get_all_adapters(db):
        items.append(
            {
                "platform_key": bundle.platform_key,
                "display_name": bundle.display_name,
                "version": bundle.version,
                "is_builtin": bundle.is_builtin,
                "enabled": bundle.enabled,
                "mapped_column_count": len(bundle.spec.column_map),
                "source": bundle.source,
            }
        )
    return ok({"items": items})


@router.get("/adapters/{platform_key}")
def get_adapter_detail(platform_key: str, db: Session = Depends(get_db)):
    """返回 yaml_text + 解析后的结构化对象 + semantics_note。"""
    bundle = get_adapter(db, platform_key)
    spec = bundle.spec
    return ok(
        {
            "platform_key": bundle.platform_key,
            "display_name": bundle.display_name,
            "version": bundle.version,
            "is_builtin": bundle.is_builtin,
            "enabled": bundle.enabled,
            "source": bundle.source,
            "yaml_text": bundle.yaml_text,
            "semantics_note": bundle.semantics_note,
            "structured": {
                "file_matching": spec.file_matching.model_dump(),
                "column_map": spec.column_map,
                "column_aliases": spec.column_aliases,
                "required_columns": spec.required_columns,
                "optional_columns": spec.optional_columns,
                "transforms": {k: v.model_dump() for k, v in spec.transforms.items()},
                "metric_semantics": spec.metric_semantics.model_dump(),
                "row_filters": [f.model_dump() for f in spec.row_filters],
                "dedup": spec.dedup.model_dump(),
            },
        }
    )


@router.put("/adapters/{platform_key}")
def update_adapter(platform_key: str, body: dict, db: Session = Depends(get_db)):
    """校验通过才落库，version 自增。"""
    yaml_text = body.get("yaml_text")
    if not yaml_text:
        from backend.app.core import ValidationError

        raise ValidationError("yaml_text 不能为空")
    bundle = save_override(db, platform_key, yaml_text)
    return ok(
        {
            "platform_key": bundle.platform_key,
            "version": bundle.version,
            "semantics_note": bundle.semantics_note,
        }
    )


@router.post("/adapters/{platform_key}/validate")
def validate_adapter(platform_key: str, body: dict):
    """校验 YAML 文本，不落库。"""
    result = validate_yaml_text(body.get("yaml_text") or "")
    return ok(result.model_dump())


@router.post("/adapters/{platform_key}/reset")
def reset_adapter(platform_key: str, db: Session = Depends(get_db)):
    """恢复内置 baseline（删除用户覆盖配置）。"""
    bundle = reset_to_builtin(db, platform_key)
    return ok(
        {
            "platform_key": bundle.platform_key,
            "version": bundle.version,
            "yaml_text": bundle.yaml_text,
            "semantics_note": bundle.semantics_note,
        }
    )


@router.get("/adapters-meta/builtin-keys")
def builtin_keys():
    """内置平台 key 清单（前端下拉用）。"""
    return ok({"keys": list_builtin_keys()})
