"""自动化 Release 打包工具（全能合一版）。
将【小白一键便携版】与【AI Agent 技能包】整合成一个免配置的完整便携包：
产物：dist/ecom-daily-report-v{version}.zip
小白双击即用，AI 助手也能直接调用其中的 skill 能力！
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


def build_all_in_one_zip(version: str) -> Path:
    """打包小白+Skill二合一全能独立自包含压缩包。"""
    out_zip = DIST_DIR / f"ecom-daily-report-v{version}.zip"
    build_tmp = DIST_DIR / "_tmp_bundle"
    if build_tmp.exists():
        shutil.rmtree(build_tmp)
    build_tmp.mkdir(parents=True, exist_ok=True)

    print(f"[1/2] 正在组装全能二合一便携包 (v{version})...")

    # 1. 复制顶层小白便携文件
    for item in [
        "一键生成今日报表.bat",
        "一键启动网页看板.bat",
        "一键生成测试模拟数据.bat",
        "使用说明_30秒上手.txt",
    ]:
        src = PORTABLE_DIR / item
        if src.exists():
            shutil.copy2(src, build_tmp / item)

    # 2. 建立空输入输出目录带 .gitkeep
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

    # 5. 复制完全精确锁定的依赖清单到 core，严守 Zero-Floating 准则，杜绝供应链投毒
    if (PORTABLE_DIR / "core" / "requirements.txt").exists():
        shutil.copy2(PORTABLE_DIR / "core" / "requirements.txt", core_dest / "requirements.txt")
    elif (backend_src / "requirements.lock").exists():
        shutil.copy2(backend_src / "requirements.lock", core_dest / "requirements.txt")
    if (backend_src / "requirements.lock").exists():
        shutil.copy2(backend_src / "requirements.lock", backend_dest / "requirements.lock")

    # 6. 集成 skill 文件夹到总包内
    skill_dest = build_tmp / "skill"
    skill_dest.mkdir(exist_ok=True)
    shutil.copy2(SKILL_DIR / "SKILL.md", skill_dest / "SKILL.md")
    shutil.copytree(SKILL_DIR / "scripts", skill_dest / "scripts", ignore=_ignore_patterns)
    shutil.copytree(SKILL_DIR / "references", skill_dest / "references", ignore=_ignore_patterns)

    # 7. 压缩成 zip
    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_tmp):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(build_tmp)
                zf.write(file_path, arcname)

    shutil.rmtree(build_tmp)
    print(f"  [完成] 全能二合一包打包完成: {out_zip.name} ({out_zip.stat().st_size / 1024:.1f} KB)")
    return out_zip


def generate_release_notes(version: str) -> Path:
    """生成清晰规范的 Release 说明。"""
    notes_file = DIST_DIR / f"release_notes_v{version}.md"
    content = f"""# Ecom Daily Report v{version} 正式发布

把每天从淘宝/天猫、抖店、拼多多、京东后台导出的销售文件，3 分钟内变成一份可直接群发的销售日报！

---

## 📦 下载安装指引 (Assets)

只需下载附件中的 **`ecom-daily-report-v{version}.zip`**，解压即可使用全部功能：

### 👤 普通运营 / 小白使用：
1. 解压后打开文件夹；
2. 将导出的表格（CSV/XLSX）丢进 `输入表格放这里/`；
3. 鼠标双击 `一键生成今日报表.bat`：
   - 电脑自动弹出记事本显示**【800字群发文字摘要】**（直接发微信群/钉钉群）；
   - `生成的报表在里头/` 文件夹中自动生成 7 个 Sheet 的精美 Excel 日报！
4. 想看大屏走势：双击 `一键启动网页看板.bat`。

### 🤖 AI Agent / 开发者使用：
- 包内自带 `skill/` 文件夹，符合标准 Agent 技能格式；
- 丢进 Antigravity、Claude Code 或指定 skills 目录，AI 即可全自动执行报表生成、异常归因与经营诊断！

---

## 🛠 本版核心特性
- **4 大电商平台支持**：内置淘宝/天猫（生意参谋）、抖音电商（抖店罗盘）、拼多多（商家后台）、京东（京东商智）适配器；
- **真实脏数据容错**：自动处理特殊货币符号、千分位、多行表头及尾部说明；
- **异常预警归因**：支持 GMV 剧烈波动、退款率飙升、推广 ROI 倒挂等 6 大核心规则检测；
- **严谨财务口径**：货品成本缺失绝不虚报，比率类指标环比严格采用百分点（pp）。
"""
    notes_file.write_text(content, encoding="utf-8")
    print(f"[2/2] 已生成 Release 说明文案: {notes_file.name}")
    return notes_file


def main():
    parser = argparse.ArgumentParser(description="一键生成 GitHub Release 发布包")
    parser.add_argument("--version", type=str, default="1.0.0", help="版本号（例如 1.0.0）")
    args = parser.parse_args()

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print(f"      开始打包 Ecom Daily Report v{args.version}      ")
    print("==================================================")

    bundle_zip = build_all_in_one_zip(args.version)
    notes = generate_release_notes(args.version)

    print("\n==================================================")
    print("                全部打包顺利完成！                  ")
    print("==================================================")
    print(f"产物目录: {DIST_DIR}")
    print(f"★ 全能二合一包: {bundle_zip.name}")
    print(f"★ 官方发布说明: {notes.name}")
    print("\n【下一步发版指引】：")
    print(f"1. 打开 GitHub 仓库页面，点击 Releases -> Draft a new release")
    print(f"2. Tag 填: v{args.version}")
    print(f"3. 标题填: Ecom Daily Report v{args.version}")
    print(f"4. 正文粘贴: dist/{notes.name} 的内容")
    print(f"5. 附件上传: dist/{bundle_zip.name}")


if __name__ == "__main__":
    main()
