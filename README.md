# 多平台电商销售日报与经营分析系统 (Ecom Daily Report)

> **把每天从淘宝/天猫、抖店、拼多多、京东后台导出的销售报表，压缩到 3 分钟内变成一份可直接群发的日报**：  
> **拖入文件 → 自动识别平台 → 归一化 → 出 Excel 报表 + 微信/钉钉群发摘要 + AI 经营诊断**。

---

## 快速导航：选择适合您的版本

本项目已按“源码工程”、“小白便携版”、“AI Agent 技能包”进行分治解耦，结构清晰，各取所需：

```text
ecom-daily-report/
├── portable/          # 【完全小白版】（免配置、0门槛、解压双击即用）
├── skill/             # 【AI Agent 技能包】（给 AI 编程助手/Agent 调用的标准化工具箱）
└── source/            # 【完整源码工程】（含 FastAPI 后端、Vue 3 前端、单元测试与架构规范）
```

---

### 1. 运营/商家快速使用：👉 [小白便携版 (portable/)](portable/)

适合不熟悉编程、不想安装复杂环境的电商运营与小店主：

- **极简 3 步**：
  1. 把各平台导出的表格文件丢进 `portable/输入表格放这里/`
  2. 双击运行 `portable/一键生成今日报表.bat`
  3. 电脑自动弹出记事本显示**【800字群发文字摘要】**，专业 7-Sheet Excel 日报已保存在 `portable/生成的报表在里头/`
- **进阶看板**：双击 `portable/一键启动网页看板.bat`，自动在浏览器中打开可视化分析看板。
- **快速体验**：双击 `portable/一键生成测试模拟数据.bat`，可一键生成 4 大平台 7 天跨度的演示数据。

---

### 2. AI 助手/自动化使用：👉 [AI Agent 技能包 (skill/)](skill/)

适合集成进 Antigravity、Claude Code、Cursor 等智能 Agent 中：

- **技能定义**：详见 `skill/SKILL.md`，规范了唤醒场景、参数定义与指标算法规范。
- **开箱即用脚本**：
  - `python skill/scripts/run_report.py --json`：全自动无头跑批入库与报表生成。
  - `python skill/scripts/query_metrics.py --date YYYY-MM-DD`：查询多维聚合指标、异常预警与 Top SKU。
- **业务字典**：包含平台字段对照表 `references/adapters.md` 与指标预警算法手册 `references/metrics.md`。

---

### 3. 二次开发与全量部署：👉 [完整工程源码 (source/)](source/)

适合前端/后端工程师进行二次开发、扩展新平台适配器或部署私有服务：

- **技术栈**：Python 3.11+ (FastAPI + SQLAlchemy + SQLite WAL) + Vue 3 (Element Plus + Pinia + ECharts)
- **开发与启动指引**：详见 [source/README.md](source/README.md)。
- **核心架构原则**：
  - 平台差异全部由 YAML 表达（`source/backend/app/adapters/*.yaml`），代码零平台硬编码。
  - 公式 AST 白名单求值（严禁 `eval`）。
  - 成本未知则毛利为空，严禁比例兜底虚报。
- **自动化测试**：271 个 pytest 模块全绿覆盖（`pytest source/backend/tests`）。

---

## 开源与使用说明

- 核心数据均在本地处理，默认使用 SQLite WAL 模式，保障数据隐私与安全。
- 无论代码如何被复刻，真正的壁垒在于对真实电商平台繁杂表格细节的持续适配与清洗。
