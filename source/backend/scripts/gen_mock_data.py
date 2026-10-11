"""模拟原始导出文件生成器（规划 6.8）。

这是无真实平台数据时全链路验证的关键交付物：按 4 个平台各自的
真实表头、编码、文件命名生成 35 天 × 多 SKU 的导出文件，并注入
脏数据（货币符号、千分位、混用日期格式、空值、测试行、重复行、
尾部说明文字），同时产出 manifest.json 记录预期聚合结果的关键
数字，供 smoke_test 做端到端断言（±0.5% 误差）。

用法：
    python -m backend.scripts.gen_mock_data --days 35 --skus 40 --outdir samples/mock
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import zlib
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook

# 固定随机种子：保证同参数重跑的 manifest 可复现
DEFAULT_SEED = 20260906

REPORT_DATE = date(2026, 9, 6)  # 规划 6.8 示例文件名所用的统计日期

# ---------------------------------------------------------------------------
# SKU 目录
# ---------------------------------------------------------------------------

_CATEGORIES = ["美妆护肤", "个护清洁", "家居日用", "食品饮料", "数码配件"]

# 共享 SKU 的内部标准名（模糊匹配的基准名，刻意包含规格词与品牌词）
_SHARED_BASE_NAMES = [
    ("水润保湿精华液30ml", "美妆护肤", "本草堂"),
    ("烟酰胺美白身体乳250ml", "美妆护肤", "雪漾"),
    ("氨基酸洁面乳120g", "美妆护肤", "本草堂"),
    ("玻尿酸补水面膜25片", "美妆护肤", "雪漾"),
    ("防晒霜SPF50 50g", "美妆护肤", "晒不黑"),
    ("深海鱼油软胶囊100粒", "食品饮料", "康倍"),
    ("每日坚果混合装750g", "食品饮料", "果语"),
    ("手冲挂耳咖啡40杯", "食品饮料", "山姆园"),
    ("零卡气泡水500ml×12", "食品饮料", "轻汽"),
    ("益生菌固体饮料20条", "食品饮料", "康倍"),
    ("抑菌洗手液500ml", "个护清洁", "净安"),
    ("除螨洗衣凝珠60颗", "个护清洁", "净安"),
    ("竹炭牙膏双支装220g", "个护清洁", "皓白"),
    ("儿童牙刷软毛4支", "个护清洁", "小白牙"),
    ("厨房去油污湿巾80片", "家居日用", "洁管家"),
    ("加厚珊瑚绒浴巾", "家居日用", "云柔"),
    (" aromatherapy无烟香薰蜡烛 ", "家居日用", "栖居"),
    ("304不锈钢保鲜盒三件套", "家居日用", "厨悦"),
    ("氮化镓65W充电器", "数码配件", "电友"),
    ("type-c编织快充线2m", "数码配件", "电友"),
]

# 各平台独占 SKU（仅出现在单一平台，无内部 SKU → 未映射【待映射】）
_EXCLUSIVE_NAMES = [
    "蓝牙耳机收纳包", "车载磁吸手机支架", "迷你便携挂脖风扇",
    "宠物梳毛手套", "户外折叠野餐垫", "硅胶折叠水壶",
    "磁吸式防晒遮阳帘", "不锈钢水果叉8支", "旅行分装瓶套装",
    "磁力片积木补充包", "儿童防走失背包", "可折叠购物车",
    "免打孔浴室置物架", "婴儿辅食研磨碗", "四季通用遮车罩",
    "桌面理线器收纳盒", "伸缩手机懒人支架", "防滑衣架30支",
    "USB小夜灯插电款", "大容量药盒分装格",
]

_SHOP_NAMES = {
    "taobao": ["本草堂官方旗舰店", "本草堂企业店"],
    "doudian": ["本草堂抖音官方店"],
    "pinduoduo": ["本草堂拼多多自营店"],
    "jd": ["本草堂京东自营店"],
}


def _clean_name(name: str) -> str:
    return " ".join(name.split()).strip()


def build_catalog(skus: int) -> tuple[list[dict], dict[str, list[dict]]]:
    """构建内部 SKU 目录与各平台商品清单。

    返回 (internal_skus, platform_products)：
    - internal_skus：共享 SKU 的内部目录（前一半数量）
    - platform_products：每个平台的商品清单（共享 + 独占），
      独占 SKU 的 internal_sku_code 为 None（未映射来源）
    """
    shared_count = max(4, skus // 2)
    exclusive_total = max(0, skus - shared_count)

    internal_skus = []
    for i in range(shared_count):
        base_name, category, brand = _SHARED_BASE_NAMES[i % len(_SHARED_BASE_NAMES)]
        suffix = f"_{i // len(_SHARED_BASE_NAMES) + 1}" if i >= len(_SHARED_BASE_NAMES) else ""
        internal_skus.append(
            {
                "sku_code": f"SKU{i + 1:04d}",
                "name": _clean_name(base_name) + suffix,
                "category": category,
                "brand": brand,
            }
        )

    platform_keys = ["taobao", "doudian", "pinduoduo", "jd"]
    platform_products = {k: [] for k in platform_keys}

    # 共享 SKU：四个平台都有，但商品名写法不同（验证模糊匹配）
    for sku in internal_skus:
        for key in platform_keys:
            platform_products[key].append(
                {
                    "platform_product_code": _platform_code(key, sku["sku_code"]),
                    "name": _platform_variant_name(key, sku["name"]),
                    "internal_sku_code": sku["sku_code"],
                    "category": sku["category"],
                }
            )

    # 独占 SKU：轮转分配给平台，无内部 SKU
    for i in range(exclusive_total):
        key = platform_keys[i % len(platform_keys)]
        base_name = _EXCLUSIVE_NAMES[(i // len(platform_keys)) % len(_EXCLUSIVE_NAMES)]
        cat = _CATEGORIES[(i * 3) % len(_CATEGORIES)]
        platform_products[key].append(
            {
                "platform_product_code": _platform_code(key, f"EX{i + 1:04d}"),
                "name": _platform_variant_name(key, base_name),
                "internal_sku_code": None,
                "category": cat,
            }
        )
    return internal_skus, platform_products


def _stable_code(seq: str) -> int:
    """进程无关的稳定散列（str.hash 有随机化，不能用）。"""
    return zlib.crc32(seq.encode("utf-8"))


def _platform_code(platform_key: str, seq: str) -> str:
    """平台侧商品编码：各平台风格不同，且与内部 sku_code 不相等
    （保证精确码匹配不命中，走名称模糊匹配）。"""
    prefix = {"taobao": "69", "doudian": "88", "pinduoduo": "77", "jd": "66"}[platform_key]
    return f"{prefix}{_stable_code(seq) % 10000000000:010d}"


def _platform_variant_name(platform_key: str, base_name: str) -> str:
    """同一商品在各平台的写法差异（规划 6.8：验证模糊匹配预处理）。"""
    if platform_key == "taobao":
        return f"本草堂旗舰店正品 {base_name} 新款"
    if platform_key == "doudian":
        return f"【爆款】{base_name}"
    if platform_key == "pinduoduo":
        return f"{base_name} 包邮"
    return f"京东自营 {base_name}"


# ---------------------------------------------------------------------------
# 销量模拟
# ---------------------------------------------------------------------------


def _simulate_sales(
    rng: random.Random,
    platform_key: str,
    product: dict,
    day_index: int,
    stat_date: date,
    days: int,
    promo_offsets: set[int],
    crash_offsets: set[int],
    high_refund: bool,
    price: Decimal,
    burn_day: bool,
) -> dict:
    """模拟单行干净数据，返回"解析后的最终值"（即解析器应产出的值）。"""
    base_qty = rng.uniform(8, 60)
    factor = 1.0
    weekend = stat_date.weekday() >= 5
    is_sunday = stat_date.weekday() == 6
    # 周末效应：销量 1.4~1.8 倍
    if weekend:
        factor *= rng.uniform(1.4, 1.8)
    # 7 天小波动周期
    factor *= 1 + 0.15 * math.sin(2 * math.pi * day_index / 7)
    # 大促日：3~5 倍
    if day_index in promo_offsets:
        factor *= rng.uniform(3.0, 5.0)
    qty = max(1, round(base_qty * factor))
    # 最后 3 天销量逐日骤降（每天约 -55%，报告日环比仍触发骤降预警）
    if day_index in crash_offsets:
        level = crash_offsets[day_index]
        qty = max(1, round(qty * (0.45 ** level)))

    gmv = (price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # 退款率：普通 2%~12%，指定 SKU 18%~25%（>15% 触发退款率超限预警）
    rate = rng.uniform(0.18, 0.25) if high_refund else rng.uniform(0.02, 0.12)
    refund = (gmv * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 访客/买家：周末"逛的人多、买的人少" → 转化率明显低于工作日；
    # 周日进一步下探（触发转化率异常下滑预警：周六≈4%，周日≈2%）
    if is_sunday:
        visitors = int(qty * rng.uniform(18, 24)) + 1
        buyers = max(1, int(qty * rng.uniform(0.30, 0.45)))
    elif weekend:
        visitors = int(qty * rng.uniform(15, 20)) + 1
        buyers = max(1, int(qty * rng.uniform(0.65, 0.80)))
    else:
        visitors = int(qty * rng.uniform(8, 12)) + 1
        buyers = max(1, int(qty * rng.uniform(0.7, 0.95)))

    # 抖店"烧钱日"：推广消耗超过净成交额（触发推广 ROI 过低预警）
    if burn_day:
        ad_cost = (gmv * Decimal(str(rng.uniform(1.2, 1.5)))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        ad_cost = (gmv * Decimal(str(rng.uniform(0, 0.15)))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    row = {
        "stat_date": stat_date,
        "shop_name": _pick_shop(rng, platform_key, product),
        "product_code": product["platform_product_code"],
        "product_name": product["name"],
        "category": product["category"],
        "visitors": visitors,
        "buyers": buyers,
        "paid_qty": qty,
        "gmv": gmv,
        "refund_amount": refund,
        "ad_cost": ad_cost,
        "order_qty": None,
    }
    if platform_key == "jd":
        # 京东为下单口径：下单件数 ≥ 付款件数，下单金额略高于付款金额
        row["order_qty"] = qty + int(qty * rng.uniform(0, 0.2))
        row["gmv"] = (price * Decimal(row["order_qty"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return row


def _pick_shop(rng: random.Random, platform_key: str, product: dict) -> str:
    """按商品码稳定分配店铺（保证同一商品固定在一个店铺，便于断言）。"""
    shops = _SHOP_NAMES[platform_key]
    idx = _stable_code(product["platform_product_code"]) % len(shops)
    return shops[idx]


# ---------------------------------------------------------------------------
# 脏数据渲染
# ---------------------------------------------------------------------------


def _fmt_money(value: Decimal, style: str) -> str:
    """按平台风格渲染金额（含脏格式：货币符号 / 千分位）。"""
    if style == "taobao":
        return f"￥{value:,.2f}"
    if style == "doudian":
        return f"{value:,.2f}"
    if style == "pinduoduo":
        return f"¥{value:,.2f}"
    return f"{value:,.2f}"  # jd


def _fmt_date(d: date, dirty: bool) -> str:
    """日期混用 2026-09-06 与 2026/09/06 两种写法。"""
    return d.strftime("%Y/%m/%d") if dirty else d.strftime("%Y-%m-%d")


def _render_row(rng: random.Random, platform_key: str, row: dict,
                field_order: list[str]) -> tuple[dict, dict]:
    """渲染一行平台原始数据。

    返回 (raw_row, final_values)：raw_row 是写入文件的字符串字典；
    final_values 是"解析器应还原出的值"，用于累计 manifest 预期数。
    每行有约 12% 概率注入 1 处脏数据（规划 6.8：每平台每天 1~2 行）。
    """
    final = {
        "gmv": row["gmv"],
        "refund_amount": row["refund_amount"],
        "paid_qty": row["paid_qty"],
        "visitors": row["visitors"],
        "buyers": row["buyers"],
        "ad_cost": row["ad_cost"],
        "order_qty": row["order_qty"],
    }

    stat_date_text = _fmt_date(row["stat_date"], dirty=rng.random() < 0.1)
    gmv_text = _fmt_money(row["gmv"], platform_key)
    refund_text = _fmt_money(row["refund_amount"], platform_key)
    visitors_text = str(row["visitors"])
    buyers_text = str(row["buyers"])
    paid_text = str(row["paid_qty"])
    order_text = str(row["order_qty"]) if row["order_qty"] is not None else ""
    ad_text = _fmt_money(row["ad_cost"], platform_key)

    roll = rng.random()
    if roll < 0.03:
        final["visitors"] = None  # 空值 → NULL（default: null）
        visitors_text = "-"
    elif roll < 0.05:
        final["buyers"] = None
        buyers_text = "--"
    elif roll < 0.07:
        final["ad_cost"] = Decimal("0.00")  # "-" → default "0"
        ad_text = "-"

    raw = {
        "stat_date": stat_date_text,
        "shop_name": row["shop_name"],
        "product_code": row["product_code"],
        "product_name": row["product_name"],
        "category": row["category"],
        "visitors": visitors_text,
        "buyers": buyers_text,
        "paid_qty": paid_text,
        "gmv": gmv_text,
        "refund_amount": refund_text,
        "ad_cost": ad_text,
        "order_qty": order_text,
    }
    return raw, final


# ---------------------------------------------------------------------------
# 平台文件写出
# ---------------------------------------------------------------------------

_PLATFORM_FILE_SPEC = {
    "taobao": {
        "filename": "生意参谋_商品效果_{date_compact}.csv",
        "encoding": "gb18030",
        "columns": ["统计日期", "店铺名称", "商品ID", "商品名称", "一级类目",
                    "商品访客数", "支付买家数", "支付件数", "支付金额", "退款金额", "推广花费"],
        "field_order": ["stat_date", "shop_name", "product_code", "product_name", "category",
                        "visitors", "buyers", "paid_qty", "gmv", "refund_amount", "ad_cost"],
    },
    "doudian": {
        "filename": "抖店罗盘_商品分析_{date_dash}.csv",
        "encoding": "utf-8-sig",
        "columns": ["统计时间", "店铺名称", "商品ID", "商品名称", "一级类目",
                    "商品访客数", "支付人数", "支付商品件数", "支付金额", "退款金额", "推广消耗"],
        "field_order": ["stat_date", "shop_name", "product_code", "product_name", "category",
                        "visitors", "buyers", "paid_qty", "gmv", "refund_amount", "ad_cost"],
    },
    "pinduoduo": {
        "filename": "拼多多_商品数据_{date_compact}.xlsx",
        "encoding": "xlsx",
        "columns": ["统计日期", "店铺名称", "商品ID", "商品名称", "商品一级类目",
                    "商品访客数", "成团买家数", "成团件数", "成团金额(GMV)", "退款金额", "推广花费"],
        "field_order": ["stat_date", "shop_name", "product_code", "product_name", "category",
                        "visitors", "buyers", "paid_qty", "gmv", "refund_amount", "ad_cost"],
    },
    "jd": {
        "filename": "京东商智_商品分析_{date_compact}.csv",
        "encoding": "utf-8",
        "columns": ["日期", "店铺名称", "商品编号", "商品名称", "一级分类",
                    "访客数", "付款人数", "下单件数", "付款件数", "下单金额", "退款金额", "快车花费"],
        "field_order": ["stat_date", "shop_name", "product_code", "product_name", "category",
                        "visitors", "buyers", "order_qty", "paid_qty", "gmv", "refund_amount",
                        "ad_cost"],
    },
}


def _write_csv(path: Path, encoding: str, all_rows: list[list[str]]) -> None:
    """按给定顺序写出全部行（含标题行 / 空行 / 表头行 / 数据行）。"""
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.writer(f)
        writer.writerows(all_rows)


def _write_xlsx(path: Path, header: list[str], rows: list[list[str]],
                footer: list[list[str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "商品数据"
    ws.append(header)
    for row in rows:
        ws.append(row)
    for row in footer:
        ws.append(row)
    wb.save(path)


# ---------------------------------------------------------------------------
# 主生成流程
# ---------------------------------------------------------------------------

# 用于 manifest 预期数累计的字段
_SUM_FIELDS = ["gmv", "refund_amount", "paid_qty", "visitors", "buyers", "ad_cost", "order_qty"]


def generate(days: int = 35, skus: int = 40, outdir: str | Path = "samples/mock",
             seed: int = DEFAULT_SEED) -> dict:
    """生成 4 个平台的多天导出文件 + manifest.json，返回 manifest 字典。"""
    rng = random.Random(seed)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    internal_skus, platform_products = build_catalog(skus)

    date_range = [REPORT_DATE - timedelta(days=days - 1 - i) for i in range(days)]
    report_date = date_range[-1]

    # 35 天内 2 个大促日（销量 3~5 倍）
    promo_offsets = {int(days * 0.35), int(days * 0.75)}
    # 2 个共享 SKU 在最后 3 天销量逐日骤降（报告日环比仍触发预警）
    crash_skus = [s["sku_code"] for s in internal_skus[:2]]
    crash_offsets = {days - 3: 1, days - 2: 2, days - 1: 3}

    # 每个共享 SKU 固定价格，保证 crash SKU 的日 GMV 足够触发 min_base
    prices: dict[str, Decimal] = {}
    for i, sku in enumerate(internal_skus):
        prices[sku["sku_code"]] = Decimal(str(rng.uniform(30, 300))).quantize(Decimal("0.01"))
    # crash SKU 高价确保骤降后单日 GMV 仍 > 500
    for code in crash_skus:
        prices[code] = Decimal("260.00")

    # 3 个共享 SKU 退款率 > 15%（触发退款率超限预警）；高价确保每日 GMV 过门槛
    high_refund_skus = internal_skus[2:5]
    high_refund_codes = {s["sku_code"] for s in high_refund_skus}
    # 必须按列表顺序遍历，不能遍历 high_refund_codes：字符串集合的迭代
    # 顺序由 PYTHONHASHSEED 决定（逐进程随机），会让 rng.uniform 的调用
    # 次序漂移，同一 seed 重跑得到不同价格，直接破坏「固定种子 →
    # manifest 可复现」这条约定（实测同 seed 连跑 8 次出现 4 种 GMV）。
    for sku in high_refund_skus:
        prices[sku["sku_code"]] = Decimal(
            str(rng.uniform(120, 280))
        ).quantize(Decimal("0.01"))

    # 各平台"特别脏行"的位置（每平台固定注入，保证可断言）
    test_row_offset = 0     # 第 1 天：商品名含"测试商品"（row_filters）
    dup_row_offset = 5      # 第 6 天：完整重复行（dedup sum）
    na_row_offset = 10      # 第 11 天：金额无法解析（number_parse_failed）

    # 抖店"烧钱日"：推广消耗 1.2~1.5 倍 GMV（触发推广 ROI 过低预警）；
    # 最后一天必烧，保证报告日可触发
    burn_days = {int(days * 0.2), int(days * 0.6), days - 1}

    files: dict[str, dict] = {}
    expected_by_platform = {k: {f: Decimal("0") for f in _SUM_FIELDS}
                            for k in _PLATFORM_FILE_SPEC}
    expected_by_day = {d.isoformat(): Decimal("0") for d in date_range}
    expected_report_day = {k: {f: Decimal("0") for f in _SUM_FIELDS}
                           for k in _PLATFORM_FILE_SPEC}
    unmapped_codes: dict[str, list[str]] = {}

    for platform_key, spec in _PLATFORM_FILE_SPEC.items():
        columns = spec["columns"]
        field_order = spec["field_order"]
        physical_rows: list[list[str]] = []
        row_count = 0

        for day_index, stat_date in enumerate(date_range):
            if platform_key == "jd" and stat_date == report_date:
                continue  # 京东最后 1 天故意缺失（验证 data_completeness）

            day_rows: list[tuple[dict, dict]] = []
            products = platform_products[platform_key]
            for product in products:
                sku_code = product["internal_sku_code"]
                is_crash = sku_code in crash_skus and day_index in crash_offsets
                row = _simulate_sales(
                    rng, platform_key, product, day_index, stat_date, days,
                    promo_offsets, crash_offsets if is_crash else {},
                    high_refund=sku_code in high_refund_codes if sku_code else False,
                    price=prices.get(sku_code, Decimal("59.90")),
                    burn_day=(platform_key == "doudian" and day_index in burn_days),
                )
                day_rows.append((row, product))
                if product["internal_sku_code"] is None:
                    unmapped_codes.setdefault(platform_key, []).append(
                        product["platform_product_code"]
                    )

            # —— 脏数据 / 特殊行注入 ——
            is_test_row_day = day_index == test_row_offset
            if is_test_row_day and day_rows:
                base_row, product = day_rows[0]
                base_row["product_name"] = f"测试商品{product['name']}"
            if day_index == na_row_offset and day_rows:
                # 该行金额写入不可解析文本 → number_parse_failed（整行剔除）
                day_rows[0][0]["gmv"] = None  # 标记：渲染为 N/A 且不进预期数
            if day_index == dup_row_offset and day_rows:
                day_rows.append(day_rows[0])  # 完整重复行 → dedup sum 合并

            for row, product in day_rows:
                is_na_row = row["gmv"] is None and row["refund_amount"] is not None
                is_test_row = is_test_row_day and row is day_rows[0][0]
                if is_na_row:
                    raw = {k: "" for k in field_order}
                    raw["stat_date"] = row["stat_date"].strftime("%Y-%m-%d")
                    raw["shop_name"] = row["shop_name"]
                    raw["product_code"] = row["product_code"]
                    raw["product_name"] = row["product_name"]
                    raw["category"] = row["category"]
                    raw["gmv"] = "N/A"
                    raw["visitors"] = str(row["visitors"])
                    raw["buyers"] = str(row["buyers"])
                    raw["paid_qty"] = str(row["paid_qty"])
                    raw["refund_amount"] = _fmt_money(row["refund_amount"], platform_key)
                    raw["ad_cost"] = _fmt_money(row["ad_cost"], platform_key)
                    raw["order_qty"] = str(row["order_qty"] or "")
                    physical_rows.append([raw.get(f, "") for f in field_order])
                    row_count += 1
                    continue

                raw, final = _render_row(rng, platform_key, row, field_order)
                # 抖店统计时间带时分秒（验证 datetime 多格式与截断）
                if platform_key == "doudian":
                    raw["stat_date"] = f"{row['stat_date'].strftime('%Y-%m-%d')} 00:00:00"

                physical_rows.append([raw.get(f, "") for f in field_order])
                row_count += 1

                # 被行过滤规则剔除的"测试商品"行不计入预期数
                if is_test_row:
                    continue

                # 累计预期数（以"解析器应还原的最终值"为准）
                bucket = expected_by_platform[platform_key]
                for f in _SUM_FIELDS:
                    v = final.get(f)
                    if v is not None:
                        bucket[f] += Decimal(str(v))
                expected_by_day[stat_date.isoformat()] += Decimal(str(final["gmv"]))
                if stat_date == report_date:
                    for f in _SUM_FIELDS:
                        v = final.get(f)
                        if v is not None:
                            expected_report_day[platform_key][f] += Decimal(str(v))

        # —— 写文件 ——
        filename = spec["filename"].format(
            date_compact=report_date.strftime("%Y%m%d"),
            date_dash=report_date.strftime("%Y-%m-%d"),
        )
        path = outdir / filename
        if spec["encoding"] == "xlsx":
            footer = [
                ["说明：以上数据来源于拼多多商家后台，仅供参考。"],
                [f"导出时间：{report_date.strftime('%Y-%m-%d')} 08:00    共 {row_count} 行"],
            ]
            _write_xlsx(path, columns, physical_rows, footer)
        else:
            # 京东商智文件首部带报表标题与空行（验证 auto 表头定位）
            leading: list[list[str]] = []
            if platform_key == "jd":
                leading = [
                    [f"京东商智-商品分析报表（{report_date.strftime('%Y-%m-%d')} 导出）"],
                    [],
                ]
            _write_csv(path, spec["encoding"], leading + [columns] + physical_rows)

        files[platform_key] = {"filename": filename, "rows": row_count}

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "days": days,
        "skus": skus,
        "report_date": report_date.isoformat(),
        "date_range": [date_range[0].isoformat(), date_range[-1].isoformat()],
        "files": {k: v["filename"] for k, v in files.items()},
        "file_row_counts": {k: v["rows"] for k, v in files.items()},
        "internal_skus": internal_skus,
        "shops": _SHOP_NAMES,
        "promo_days": [date_range[o].isoformat() for o in sorted(promo_offsets)],
        "crash_skus": crash_skus,
        "high_refund_skus": sorted(high_refund_codes),
        "unmapped_platform_codes": unmapped_codes,
        "expected": {
            "by_platform": {
                k: {f: str(v) for f, v in bucket.items()}
                for k, bucket in expected_by_platform.items()
            },
            "overall": {
                f: str(sum((bucket[f] for bucket in expected_by_platform.values()),
                           Decimal("0")))
                for f in _SUM_FIELDS
            },
            "by_day_gmv": {d: str(v) for d, v in expected_by_day.items()},
            "report_day_by_platform": {
                k: {f: str(v) for f, v in bucket.items()}
                for k, bucket in expected_report_day.items()
            },
            "quality": {
                "filtered_min": 4,       # 每平台 1 行"测试商品"
                "duplicated_min": 4,     # 每平台 1 组重复行
                "failed_min": 4,         # 每平台 1 行 N/A
            },
            "missing_platforms_on_report_day": ["jd"],
        },
    }

    manifest_path = outdir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 4 平台模拟原始导出文件")
    parser.add_argument("--days", type=int, default=35, help="生成天数（默认 35）")
    parser.add_argument("--skus", type=int, default=40, help="SKU 总数（默认 40）")
    parser.add_argument("--outdir", type=str, default="samples/mock", help="输出目录")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    args = parser.parse_args()

    manifest = generate(days=args.days, skus=args.skus, outdir=args.outdir, seed=args.seed)
    print(f"已生成 {len(manifest['files'])} 个平台文件 → {args.outdir}")
    for k, f in manifest["files"].items():
        print(f"  [{k}] {f}（{manifest['file_row_counts'][k]} 行）")
    print(f"manifest.json → {Path(args.outdir) / 'manifest.json'}")


if __name__ == "__main__":
    main()
