"""适配器注册中心（规划 T05）。

职责：
- 加载并校验 4 个内置 YAML（带缓存）；
- 支持用户在 ``AdapterConfig`` 表中覆盖（内置 YAML 作为 baseline）；
- ``detect_platform()`` 基于 detect_keywords 命中数 × detect_weight 打分，
  返回排序候选；
- ``validate_yaml_text()`` 供编辑器先校验后保存；
- ``reset_to_builtin()`` 恢复内置 baseline。

所有平台差异都由 YAML 表达，本模块不包含任何平台特定解析逻辑。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core import AdapterConfigError, AdapterNotFoundError
from backend.app.schemas.adapter import (
    AdapterSpec,
    AdapterValidateResult,
    build_semantics_note,
)

logger = logging.getLogger("ecom.adapter")


@dataclass
class AdapterBundle:
    """一个可用适配器的完整信息（内置 baseline 或用户覆盖版）。"""

    spec: AdapterSpec
    yaml_text: str
    is_builtin: bool
    version: int
    enabled: bool = True
    semantics_note: str = field(default="")
    source: str = "builtin"  # builtin | override

    @property
    def platform_key(self) -> str:
        return self.spec.platform_key

    @property
    def display_name(self) -> str:
        return self.spec.display_name


# ---------------------------------------------------------------------------
# 内置适配器加载（带缓存）
# ---------------------------------------------------------------------------

_builtin_cache: dict[str, AdapterBundle] = {}


def _builtin_path(key: str) -> Path:
    return settings.adapters_dir / f"{key}.yaml"


def _parse_yaml_text(yaml_text: str) -> AdapterSpec:
    """YAML 文本 → AdapterSpec，失败抛 AdapterConfigError。"""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise AdapterConfigError(f"YAML 语法错误：{e}") from e
    if not isinstance(data, dict):
        raise AdapterConfigError("YAML 内容必须是键值映射结构")
    try:
        return AdapterSpec(**data)
    except ValidationError as e:
        errors = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise AdapterConfigError(
            "适配器配置校验失败：" + "；".join(errors),
            detail={"errors": errors},
        ) from e


def load_builtin(platform_key: str) -> AdapterBundle:
    """加载内置适配器（带缓存）。文件不存在抛 AdapterNotFoundError。"""
    if platform_key in _builtin_cache:
        return _builtin_cache[platform_key]

    path = _builtin_path(platform_key)
    if not path.is_file():
        raise AdapterNotFoundError(f"内置适配器不存在：{platform_key}")

    yaml_text = path.read_text(encoding="utf-8")
    spec = _parse_yaml_text(yaml_text)
    if spec.platform_key != platform_key:
        raise AdapterConfigError(
            f"适配器文件 {path.name} 的 platform_key={spec.platform_key!r} 与文件名不符"
        )

    bundle = AdapterBundle(
        spec=spec,
        yaml_text=yaml_text,
        is_builtin=True,
        version=spec.adapter_version,
        semantics_note=build_semantics_note(spec),
    )
    _builtin_cache[platform_key] = bundle
    return bundle


def list_builtin_keys() -> list[str]:
    """列出全部内置适配器 key（按文件名排序）。"""
    d = settings.adapters_dir
    return sorted(p.stem for p in d.glob("*.yaml"))


def count_builtin_adapters() -> int:
    """内置适配器数量（/api/health 用）。"""
    return len(list_builtin_keys())


def reload_builtin_cache() -> None:
    """清空内置缓存（适配器文件被修改后调用）。"""
    _builtin_cache.clear()


# ---------------------------------------------------------------------------
# 用户覆盖（AdapterConfig 表）
# ---------------------------------------------------------------------------


def get_adapter(db: Session, platform_key: str) -> AdapterBundle:
    """取可用适配器：用户覆盖版优先（enabled 才生效），否则内置 baseline。"""
    from backend.app.models.entities import AdapterConfig

    row = (
        db.query(AdapterConfig)
        .filter(AdapterConfig.platform_key == platform_key)
        .one_or_none()
    )
    if row is not None and row.enabled:
        spec = _parse_yaml_text(row.yaml_text)
        return AdapterBundle(
            spec=spec,
            yaml_text=row.yaml_text,
            is_builtin=row.is_builtin,
            version=row.version,
            enabled=row.enabled,
            semantics_note=build_semantics_note(spec),
            source="override",
        )
    return load_builtin(platform_key)


def get_all_adapters(db: Session) -> list[AdapterBundle]:
    """全部内置平台 + 用户已覆盖的自定义平台。"""
    from backend.app.models.entities import AdapterConfig

    bundles: dict[str, AdapterBundle] = {}
    for key in list_builtin_keys():
        try:
            bundles[key] = get_adapter(db, key)
        except (AdapterNotFoundError, AdapterConfigError) as e:
            logger.warning("内置适配器 %s 加载失败: %s", key, e)

    for row in db.query(AdapterConfig).all():
        if row.platform_key not in bundles and row.enabled:
            try:
                bundles[row.platform_key] = get_adapter(db, row.platform_key)
            except AdapterConfigError as e:
                logger.warning("自定义适配器 %s 加载失败: %s", row.platform_key, e)
    return list(bundles.values())


def save_override(db: Session, platform_key: str, yaml_text: str) -> AdapterBundle:
    """校验通过后保存用户覆盖版，version 自增（PUT /api/adapters/{key}）。"""
    from backend.app.models.entities import AdapterConfig

    spec = _parse_yaml_text(yaml_text)  # 校验失败直接抛 AdapterConfigError
    if spec.platform_key != platform_key:
        raise AdapterConfigError(
            f"YAML 中的 platform_key={spec.platform_key!r} 与路径 {platform_key!r} 不一致"
        )

    row = (
        db.query(AdapterConfig)
        .filter(AdapterConfig.platform_key == platform_key)
        .one_or_none()
    )
    try:
        baseline_version = load_builtin(platform_key).version
        is_builtin = True
    except AdapterNotFoundError:
        baseline_version = 0
        is_builtin = False

    if row is None:
        row = AdapterConfig(
            platform_key=platform_key,
            version=baseline_version + 1,
            yaml_text=yaml_text,
            is_builtin=is_builtin,
            enabled=True,
        )
        db.add(row)
    else:
        row.version += 1
        row.yaml_text = yaml_text
        row.is_builtin = is_builtin
        row.enabled = True
    db.commit()
    db.refresh(row)

    return AdapterBundle(
        spec=spec,
        yaml_text=yaml_text,
        is_builtin=is_builtin,
        version=row.version,
        semantics_note=build_semantics_note(spec),
        source="override",
    )


def reset_to_builtin(db: Session, platform_key: str) -> AdapterBundle:
    """删除用户覆盖版，恢复内置 baseline（POST /api/adapters/{key}/reset）。"""
    from backend.app.models.entities import AdapterConfig

    if _builtin_path(platform_key).exists():
        load_builtin(platform_key)  # 确认存在，不存在抛 AdapterNotFoundError
        db.query(AdapterConfig).filter(
            AdapterConfig.platform_key == platform_key
        ).delete()
        db.commit()
        return load_builtin(platform_key)

    # 自定义平台（无内置 baseline）：reset 即删除覆盖配置
    db.query(AdapterConfig).filter(
        AdapterConfig.platform_key == platform_key
    ).delete()
    db.commit()
    raise AdapterNotFoundError(f"平台 {platform_key} 没有内置 baseline，覆盖配置已删除")


# ---------------------------------------------------------------------------
# YAML 文本校验（编辑器用，不落库）
# ---------------------------------------------------------------------------


def validate_yaml_text(yaml_text: str) -> AdapterValidateResult:
    """POST /api/adapters/{key}/validate：返回 valid / errors / warnings。"""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return AdapterValidateResult(valid=False, errors=[f"YAML 语法错误：{e}"])

    if not isinstance(data, dict):
        return AdapterValidateResult(valid=False, errors=["YAML 内容必须是键值映射结构"])

    try:
        spec = AdapterSpec(**data)
    except ValidationError as e:
        errors = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return AdapterValidateResult(valid=False, errors=errors)

    # 非阻断性提示
    if not spec.file_matching.detect_keywords:
        warnings.append("detect_keywords 为空，将无法参与平台自动识别")
    if "gmv" not in spec.transforms:
        warnings.append("gmv 未配置 money 转换，非数值内容将按原样保留")
    if spec.metric_semantics.gmv_includes_shipping:
        warnings.append("该平台 GMV 含运费，与其他平台不可直接相加比较（导出与看板会标注）")
    if spec.metric_semantics.refund_handling == "none":
        warnings.append("该平台无退款数据，实际成交额未扣除退款（口径说明中会标注）")
    if spec.metric_semantics.platform_fee_rate == 0:
        warnings.append("platform_fee_rate 为 0：利润指标将不含平台扣点，请按经营类目实际扣点配置")

    return AdapterValidateResult(valid=True, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# 平台自动识别（规划 T05：detect_keywords 命中数 × detect_weight）
# ---------------------------------------------------------------------------


def detect_platform(columns: list[str], filename: str | None = None) -> list[dict]:
    """对全部内置适配器打分，返回按得分降序的候选列表。

    打分：表头列名与文件名中每命中一个 detect_keyword 计 1，
    再乘以该适配器的 detect_weight。
    """
    col_text = " ".join(columns)
    filename = filename or ""

    scored: list[dict] = []
    for key in list_builtin_keys():
        try:
            bundle = load_builtin(key)
        except (AdapterNotFoundError, AdapterConfigError):
            continue
        fm = bundle.spec.file_matching
        hits = sum(1 for kw in fm.detect_keywords if kw and kw in col_text)
        hits += sum(1 for kw in fm.detect_keywords if kw and kw in filename)
        scored.append(
            {
                "platform_key": key,
                "display_name": bundle.display_name,
                "score": hits * fm.detect_weight,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    total = sum(x["score"] for x in scored)
    top = scored[0]["score"] if scored else 0
    confidence = (top / total) if total > 0 else 0.0
    for i, item in enumerate(scored):
        item["confidence"] = round(confidence, 4) if i == 0 else 0.0
    return scored
