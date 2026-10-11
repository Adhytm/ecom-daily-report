"""自动化 Release 打包工具。
生成两个干净自包含的压缩包：
1. ecom-daily-report-portable-v{version}.zip （小白一键免配置版）
2. ecom-daily-report-skill-v{version}.zip （AI Agent 标准技能包）
并在 dist/ 下输出 Release 说明文案 release_notes_v{version}.md。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

# 确保在 Windows 控制台下支持 UTF-8 打印输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT_DIR / "source"
PORTABLE_DIR = ROOT_DIR / "portable"
SKILL_DIR = ROOT_DIR / "skill"
DIST_DIR = ROOT_DIR / "dist"


def _ignore_patterns(path, names):
    """过滤无用的缓存和临时文件。"""
    ignored = set()
    for name in names:
        if name in [
            "__pycache__",
            ".pytest_cache",
            ".pytest_tmp",
            ".coverage",
            ".git",
            ".venv",
            "data",
            "node_modules",
            "tests",
        ]:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", ".pyd")):
            ignored.add(name)
    return ignored


def build_portable_zip(version: str) -> Path:
    """打包小白便携版，注入后端核心，做成自包含绿色包。"""
    out_zip = DIST_DIR / f"ecom-daily-report-portable-v{version}.zip"
    build_tmp = DIST_DIR / "_tmp_portable"
    if build_tmp.exists():
        shutil.rmtree(build_tmp)
    build_tmp.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] 正在组装小白便携版 (v{version})...")

    # 1. 复制顶层便携文件
    for item in [
        "一键生成今日报表.bat",
        "一键启动网页看板.bat",
        "一键生成测试模拟数据.bat",
        "使用说明_30秒上手.txt",
    ]:
        src = PORTABLE_DIR / item
        if src.exists():
            shutil.copy2(src, build_tmp / item)

    # 2. 建立空目录带 .gitkeep
    (build_tmp / "输入表格放这里").mkdir(exist_ok=True)
    (build_tmp / "输入表格放这里" / ".gitkeep").touch()
    (build_tmp / "生成的报表在里头").mkdir(exist_ok=True)
    (build_tmp / "生成的报表在里头" / ".gitkeep").touch()

    # 3. 复制 core
    core_dest = build_tmp / "core"
    core_dest.mkdir(exist_ok=True)
    shutil.copy2(PORTABLE_DIR / "core" / "runner.py", core_dest / "runner.py")

    # 4. 将 source/backend 打包进 core/backend（过滤测试和缓存）
    backend_src = SOURCE_DIR / "backend"
    backend_dest = core_dest / "backend"
    shutil.copytree(backend_src, backend_dest, ignore=_ignore_patterns)

    # 5. 复制 requirements.txt 到 core
    if (backend_src / "requirements.txt").exists():
        shutil.copy2(backend_src / "requirements.txt", core_dest / "requirements.txt")

    # 6. 压缩成 zip
    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_tmp):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(build_tmp)
                zf.write(file_path, arcname)

    shutil.rmtree(build_tmp)
    print(f"  [完成] 小白便携版打包完成: {out_zip.name} ({out_zip.stat().st_size / 1024:.1f} KB)")
    return out_zip


def build_skill_zip(version: str) -> Path:
    """打包 AI Agent 技能包。"""
    out_zip = DIST_DIR / f"ecom-daily-report-skill-v{version}.zip"
    build_tmp = DIST_DIR / "_tmp_skill"
    if build_tmp.exists():
        shutil.rmtree(build_tmp)
    build_tmp.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] 正在组装 Agent 技能包 (v{version})...")

    skill_root = build_tmp / "ecom-daily-report"
    skill_root.mkdir(parents=True, exist_ok=True)

    # 1. 复制 SKILL.md
    shutil.copy2(SKILL_DIR / "SKILL.md", skill_root / "SKILL.md")

    # 2. 复制 scripts
    shutil.copytree(SKILL_DIR / "scripts", skill_root / "scripts", ignore=_ignore_patterns)

    # 3. 复制 references
    shutil.copytree(SKILL_DIR / "references", skill_root / "references", ignore=_ignore_patterns)

    # 4. 注入后端核心到 skill 内部（支持独立唤醒）
    backend_src = SOURCE_DIR / "backend"
    backend_dest = skill_root / "backend"
    shutil.copytree(backend_src, backend_dest, ignore=_ignore_patterns)

    # 5. 压缩成 zip
    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_tmp):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(build_tmp)
                zf.write(file_path, arcname)

    shutil.rmtree(build_tmp)
    print(f"  [完成] Agent 技能包打包完成: {out_zip.name} ({out_zip.stat().st_size / 1024:.1f} KB)")
    return out_zip


def generate_release_notes(version: str) -> Path:
    """生成漂亮的 Release 发布说明文本。"""
    notes_file = DIST_DIR / f"release_notes_v{version}.md"
    content = f"""# Ecom Daily Report v{version} 发布说明

把每天从淘宝/天猫、抖店、拼多多、京东后台导出的销售文件，3 分钟内变成一份可直接群发的销售日报！

---

## 📦 本次发布资产下载说明 (Assets)

### 1. 电商运营/小白专用：`ecom-daily-report-portable-v{version}.zip`
- **使用方法**：
  1. 下载后解压到任意文件夹或桌面；
  2. 将导出的表格拖进 `输入表格放这里/` 文件夹；
  3. 双击 `一键生成今日报表.bat`，自动在记事本弹出可直接复制的群发摘要，并在 `生成的报表在里头/` 产出 7-Sheet 专业 Excel 报表！
- **功能特性**：零配置、全自动去重与平台识别、支持双击启动可视化 Web 看板。

### 2. AI 编程助手 / Agent 专用：`ecom-daily-report-skill-v{version}.zip`
- **使用方法**：
  1. 下载解压后，将 `ecom-daily-report` 文件夹丢入 Antigravity、Claude Code 或项目的 `skills/` 目录；
  2. 对 AI 说：“帮我跑一下今天的销售日报” 或 “分析今天为什么退款率飙升”，AI 即可全自动执行流水线并给出专业诊断归因。

---

## 🛠 本次更新内容
- **多平台适配**：内置淘宝/天猫（生意参谋）、抖音电商（抖店罗盘）、拼多多（商家后台）、京东（京东商智）四大平台适配规则。
- **架构升级**：采用源码、小白便携版、AI Skill 三合一分治架构，同源演进。
- **自动归因与预警**：支持客单价、转化率、ROI、退款率异动检测与红黄分级预警。
"""
    notes_file.write_text(content, encoding="utf-8")
    print(f"[3/3] 已生成 Release 说明文案: {notes_file.name}")
    return notes_file


def main():
    parser = argparse.ArgumentParser(description="一键生成 GitHub Release 发布包")
    parser.add_argument("--version", type=str, default="1.0.0", help="版本号（例如 1.0.0）")
    args = parser.parse_args()

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print(f"      开始打包 Ecom Daily Report v{args.version}      ")
    print("==================================================")

    p_zip = build_portable_zip(args.version)
    s_zip = build_skill_zip(args.version)
    notes = generate_release_notes(args.version)

    print("\n==================================================")
    print("                全部打包顺利完成！                  ")
    print("==================================================")
    print(f"产物目录: {DIST_DIR}")
    print(f"1. 小白包: {p_zip.name}")
    print(f"2. 技能包: {s_zip.name}")
    print(f"3. 发布说明: {notes.name}")
    print("\n【下一步发版指引】：")
    print("在 GitHub 仓库点击 [Releases] -> [Draft a new release]：")
    print("  - Tag version 填: v" + args.version)
    print("  - Release title 填: Ecom Daily Report v" + args.version)
    print("  - 正文粘贴: dist/" + notes.name + " 的内容")
    print("  - 将 dist/ 下的两个 .zip 文件拖入附件上传即可！")


if __name__ == "__main__":
    main()
