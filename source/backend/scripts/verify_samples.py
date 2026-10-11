"""真实样例解析自检:验证 4 个平台真实导出文件均可正常解析。

用途:导入能力改造(见 docs/导入能力改造计划.md)的回归基线。
改动 file_parser / adapter 后必须运行本脚本,行数不得低于基线。

用法(PowerShell,项目根目录下):
    .venv/Scripts/python.exe -m backend.scripts.verify_samples

基线(改动前实测):
    抖店罗盘_商品分析_2026-09-06.csv     doudian      行=876  列=11
    京东商智_商品分析_20260906.csv       jd           行=851  列=12
    拼多多_商品数据_20260906.xlsx        pinduoduo    行=876  列=11
    生意参谋_商品效果_20260906.csv       taobao       行=876  列=11
退出码:0=全部通过;1=有文件解析失败或行数低于基线。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from backend.app.core.adapter_registry import load_builtin
from backend.app.core.file_parser import parse_file

# 自检脚本不 import file_parser.SUPPORTED_EXTENSIONS:该常量由
# docs/导入能力改造计划.md P0-3 才引入,此处忽略大小写做后缀过滤以保持自检可独立运行。
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".csv", ".xlsx", ".xlsm", ".xls", ".tsv", ".txt")

# 文件名关键词 → 平台 key
PLATFORM_HINTS: dict[str, str] = {
    "抖店": "doudian",
    "京东": "jd",
    "拼多多": "pinduoduo",
    "生意参谋": "taobao",
}

# 文件名 → 基线行数(低于基线即判定回归)
BASELINE_ROWS: dict[str, int] = {
    "抖店罗盘_商品分析_2026-09-06.csv": 876,
    "京东商智_商品分析_20260906.csv": 851,
    "拼多多_商品数据_20260906.xlsx": 876,
    "生意参谋_商品效果_20260906.csv": 876,
}

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples" / "mock"


def _platform_of(filename: str) -> str | None:
    for keyword, key in PLATFORM_HINTS.items():
        if keyword in filename:
            return key
    return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not SAMPLES_DIR.is_dir():
        print(f"[FAIL] 样例目录不存在:{SAMPLES_DIR}")
        return 1

    print(f"样例目录:{SAMPLES_DIR}")
    print(f"支持扩展名:{SUPPORTED_EXTENSIONS}\n")

    files = sorted(
        p
        for p in SAMPLES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        print("[FAIL] 样例目录下没有可解析的文件")
        return 1

    failures: list[str] = []
    for path in files:
        platform_key = _platform_of(path.name)
        if platform_key is None:
            print(f"[SKIP] {path.name}:无法从文件名推断平台")
            continue

        matching = load_builtin(platform_key).spec.file_matching
        started = time.perf_counter()
        try:
            df, meta = parse_file(path.read_bytes(), path.name, matching)
        except Exception as e:  # noqa: BLE001 — 自检脚本需报告任何异常
            failures.append(path.name)
            print(f"[FAIL] {path.name:38s} {platform_key:10s} {type(e).__name__}: {e}")
            continue
        elapsed = time.perf_counter() - started

        baseline = BASELINE_ROWS.get(path.name)
        flag = ""
        if baseline is not None and len(df) < baseline:
            failures.append(path.name)
            flag = f"  ← 低于基线 {baseline}"
        elif baseline is not None and len(df) > baseline:
            flag = f"  ← 高于基线 {baseline}(请确认是否为预期变化)"

        print(
            f"[ OK ] {path.name:38s} {platform_key:10s} "
            f"行={len(df):5d} 列={len(df.columns):3d} "
            f"表头行={meta.header_row_index} {elapsed:.2f}s{flag}"
        )
        for w in meta.warnings:
            print(f"        警告:{w}")

    print()
    if failures:
        print(f"[FAIL] {len(failures)} 个文件未通过:{', '.join(failures)}")
        return 1
    print(f"[PASS] {len(files)} 个真实样例全部解析通过,行数均不低于基线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
