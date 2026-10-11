"""可回灌明细导出（2026-10-04 追加）。

用途：把某天的入库明细按**各平台适配器认识的原始列名**导出成 CSV，
使用户拿到的导出文件可以直接拖回上传区、被同一套适配器再次识别入库。

与日报 Excel 的区别：
- 日报 Excel 是给人看的**成品报表**（7 个 Sheet，聚合、占比、环比），
  **不可能**作为导入源（缺 stat_date / platform_product_code 这两个必需维度）；
- 本模块导出的是**原始明细粒度**（stat_date × platform_product_code），
  列名取 `AdapterSpec.column_map` / `column_aliases` 的规范源列名，
  因此不依赖任何额外配置即可原样回灌。

回灌保真度依赖两点，均由适配器契约保证：
1. 必需列可满足：`stat_date`/`platform_product_code`/`gmv` 在所有内置适配器
   里都有 column_map 源列；
2. 店铺维度不丢：`shop_code` 在 normalizer 里有"空则回填 shop_name"的兜底，
   所以只要导出 `shop_name` 对应的源列，回灌后 dedup 键
   （stat_date + platform_key + platform_product_code + shop_code）与原数据一致。
"""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.core import NoDataForDateError
from backend.app.core.adapter_registry import get_adapter, list_builtin_keys
from backend.app.models.entities import SalesFact
from backend.app.schemas.adapter import AdapterSpec, MAPPABLE_FIELDS

# 文件命名用：去掉 Windows 非法字符
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')

# 各平台导出的列顺序（未列出的字段按适配器 column_map 声明顺序追加）
_PREFERRED_ORDER: dict[str, tuple[str, ...]] = {
    "taobao": (
        "stat_date", "shop_name", "platform_product_code", "product_name",
        "category", "visitors", "buyers", "paid_qty", "gmv", "refund_amount",
        "ad_cost",
    ),
    "doudian": (
        "stat_date", "shop_name", "platform_product_code", "product_name",
        "category", "visitors", "buyers", "paid_qty", "gmv", "refund_amount",
        "ad_cost",
    ),
    "pinduoduo": (
        "stat_date", "shop_name", "platform_product_code", "product_name",
        "category", "visitors", "buyers", "paid_qty", "gmv", "refund_amount",
        "ad_cost",
    ),
    "jd": (
        "stat_date", "shop_name", "platform_product_code", "product_name",
        "category", "visitors", "buyers", "order_qty", "paid_qty", "gmv",
        "refund_amount", "ad_cost",
    ),
}


def _safe_filename(name: str) -> str:
    return _UNSAFE_FILENAME.sub("_", name).strip() or "platform"


def _fmt_value(v) -> str:
    """统一转字符串：日期 ISO、金额/数量去掉多余小数位，None → 空。"""
    if v is None:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        # 2 位小数的金额去掉无意义的尾零（30.00 → 30，30.50 → 30.5）
        return format(v.normalize(), "f")
    return str(v)


# 有 skip_footer 的平台：真实后台导出末尾会带「汇总说明」行，适配器据此丢弃。
# 回灌文件必须补齐这些行，否则适配器会把**真实数据行**当脚注丢掉。
# （踩过的坑：拼多多 skip_footer=2，25 行明细导回来只剩 23 行、GMV 少 24528.48）
# 注意：占位行必须**非空**——解析层先丢全空行、再按 skip_footer 砍尾部行数，
# 用空行占位会在丢空行阶段就被吃掉，脚注个数对不上。
_FOOTER_ROWS: dict[str, list[list[str]]] = {
    "pinduoduo": [
        ["说明：以上数据来源于拼多多商家后台，仅供参考。"],
        ["导出时间：{date}    共 {rows} 行"],
    ],
}


def _footer_rows(spec: AdapterSpec, *, stat_date: str, row_count: int) -> list[list[str]]:
    """按适配器的 skip_footer 生成占位脚注（无此配置则返回空）。"""
    n = spec.file_matching.skip_footer
    if n <= 0:
        return []
    stub = _FOOTER_ROWS.get(spec.platform_key, [])
    rows = [
        [c.format(date=stat_date, rows=row_count) for c in r] for r in stub[:n]
    ]
    while len(rows) < n:
        rows.append([f"以上数据仅供参考（导出说明行 {len(rows) + 1}）"])
    return rows[:n]


def _column_plan(spec: AdapterSpec) -> list[tuple[str, str]]:
    """返回 [(统一字段, 该平台规范源列名)]，顺序稳定。"""
    canonical = {dst: src for src, dst in spec.column_map.items()}
    for dst, aliases in spec.column_aliases.items():
        canonical.setdefault(dst, aliases[0])  # 无 column_map 源列时取首个别名

    available = set(canonical)
    # 只有能参与归一化的字段才导出（platform_key/sku_id/raw_row_json 由系统自己产生）
    exportable = [f for f in available if f in MAPPABLE_FIELDS]

    preferred = _PREFERRED_ORDER.get(spec.platform_key, ())
    ordered: list[str] = [f for f in preferred if f in exportable]
    ordered += [f for f in exportable if f not in ordered]
    return [(field, canonical[field]) for field in ordered]


def _platform_rows(db: Session, platform_key: str, d: date) -> list[SalesFact]:
    return (
        db.query(SalesFact)
        .filter(SalesFact.platform_key == platform_key, SalesFact.stat_date == d)
        .order_by(SalesFact.platform_product_code, SalesFact.id)
        .all()
    )


def build_detail_exports(
    db: Session, date_str: str, out_dir: Path | None = None
) -> list[dict]:
    """把某天各平台的明细导出为「可直接回灌」的 CSV。

    out_dir 省略时用 settings.export_dir/detail（运行时读取，便于测试隔离）。
    返回 [{platform_key, display_name, filename, path, row_count}]，按平台 key 排序。
    该日期无任何明细时抛 NoDataForDateError。
    """
    try:
        d = date.fromisoformat(date_str)
    except (TypeError, ValueError) as e:
        from backend.app.core import ValidationError

        raise ValidationError(f"日期格式非法：{date_str}（应为 YYYY-MM-DD）") from e

    if out_dir is None:
        from backend.app.config import settings

        out_dir = settings.export_dir / "detail"

    has_any = (
        db.query(SalesFact.id).filter(SalesFact.stat_date == d).first() is not None
    )
    if not has_any:
        raise NoDataForDateError(f"{date_str} 没有任何销售明细，无法导出回灌文件")

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for key in list_builtin_keys():
        rows = _platform_rows(db, key, d)
        if not rows:
            continue
        bundle = get_adapter(db, key)
        plan = _column_plan(bundle.spec)

        filename = f"明细回灌_{_safe_filename(bundle.display_name)}_{date_str}.csv"
        path = out_dir / filename

        # utf-8-sig：带 BOM，Excel 直接双击不乱码，上传端候选编码首位即 utf-8-sig
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([src for _, src in plan])
            for fact in rows:
                writer.writerow(
                    [_fmt_value(getattr(fact, field, None)) for field, _ in plan]
                )
            for footer in _footer_rows(
                bundle.spec, stat_date=date_str, row_count=len(rows)
            ):
                writer.writerow(footer)

        results.append(
            {
                "platform_key": key,
                "display_name": bundle.display_name,
                "filename": filename,
                "path": str(path),
                # 有效明细行数（不含 skip_footer 占位脚注）
                "row_count": len(rows),
                "footer_rows": len(
                    _footer_rows(bundle.spec, stat_date=date_str, row_count=len(rows))
                ),
                "columns": [src for _, src in plan],
            }
        )

    if not results:
        # 有数据但没有任何内置适配器能认领（自定义平台）→ 明确告知
        raise NoDataForDateError(
            f"{date_str} 的明细不属于任何内置平台（可能来自自定义适配器），"
            "无法生成可回灌文件"
        )
    return results


def detail_export_manifest(db: Session, date_str: str, out_dir: Path | None = None) -> dict:
    """导出 + 返回前端展示用的清单（含每个平台的行数、列名、回灌提示）。"""
    items = build_detail_exports(db, date_str, out_dir)
    return {
        "date": date_str,
        "dir": str(out_dir),
        "items": [
            {
                "platform_key": it["platform_key"],
                "display_name": it["display_name"],
                "filename": it["filename"],
                "row_count": it["row_count"],
                "columns": it["columns"],
                "download_url": (
                    f"/api/reports/daily/{date_str}/detail-exports/"
                    f"{it['platform_key']}"
                ),
            }
            for it in items
        ],
        "note": (
            "这些 CSV 用的是各平台后台导出的原始列名（含 统计日期/商品ID/金额），"
            "可以直接拖回「上传」页重新入库，用于校验或迁移数据。"
        ),
    }
