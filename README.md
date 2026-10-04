# 多平台电商销售日报生成器

把每天从淘宝/天猫、抖店、拼多多、京东后台手工导出的销售文件，压缩到 3 分钟内变成一份可直接群发的日报：
**拖入文件 → 自动识别平台 → 归一化 → 出 Excel 日报 + 群发文字摘要**。

## 功能总览

- **手工上传 CSV / XLSX**（多文件批量，单文件 ≤ 50MB），4 个内置平台适配器：
  淘宝/天猫（生意参谋）、抖音电商（抖店罗盘）、拼多多（商家后台）、京东（京东商智）
- **适配器 YAML 可视化管理**：查看 / 编辑 / 校验 / 恢复默认，平台差异全部由 YAML 表达，Python 代码零平台硬编码
- **归一化到统一 Schema** 并落库 SQLite（WAL 模式），行级错误明细可查（过滤 / 重复 / 解析失败）
- **SKU 跨平台映射**：精确码匹配 → 名称模糊建议（rapidfuzz，≥90 分高置信）→ 未映射归入【待映射】分组，绝不丢数据
- **多维聚合**：平台 / 店铺 / 类目 / SKU；环比（vs 昨日）、周同比（vs 上周同日）；比率类指标环比用百分点差值（pp）
- **异常预警规则引擎**：6 条默认规则（阈值可配），`gt / lt / abs_gt` 三种操作符 + `min_base` 小样本门槛
- **Excel 日报导出**：7 个 Sheet（日报总览 / 分平台 / 分店铺 / 分类目 / 分SKU / 异常预警 / 口径说明），含样式与条件格式
- **群发文字摘要**：≤ 800 字符，微信/钉钉友好，超长自动三级裁剪
- **Web 看板**（Vue 3 + Element Plus + ECharts）与**模拟数据生成器**（无真实数据时全链路验证）

## 快速开始

### 0. 环境要求

- Python 3.11+（开发环境为 3.14，见 `backend/requirements.txt` 中的版本说明）
- Node.js 18+（仅前端构建需要）

### 1. 后端

```powershell
cd ecom-daily-report
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt

# 初始化数据库（建表 + 默认预警规则）
.venv\Scripts\python -m backend.scripts.init_db

# 启动（生产模式，前端已构建时直接访问 http://localhost:8000）
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
```

### 2. 前端

```powershell
cd frontend
npm install
npm run dev        # 开发模式：http://localhost:5173，/api 代理到 8000
npm run build      # 生产模式：产物输出到 backend/app/static，由 FastAPI 挂载 /
```

### 3. 没有真实数据？一键生成模拟数据

```powershell
.venv\Scripts\python -m backend.scripts.gen_mock_data --days 35 --skus 40 --outdir samples/mock
```

生成 4 个平台各自真实格式（编码 / 表头 / 文件名）的导出文件，含脏数据注入
（货币符号、千分位、混用日期、空值、测试行、重复行、尾部说明文字），并输出
`manifest.json` 记录预期数字。

### 4. 全链路冒烟验证（最终交付的硬性门槛）

```powershell
.venv\Scripts\python -m pytest backend/tests -q
.venv\Scripts\python -m backend.scripts.smoke_test
```

冒烟流程：空库初始化 → 生成模拟数据 → 上传 4 平台文件 → 自动识别 → 提交入库 →
SKU 映射建议采纳 → 生成日报 → 下载 Excel → 获取摘要 → 与 manifest 数字断言（±0.5%）。

### 5. 数据自动接入（Drop Folder 监听与断档批量补录）

告别每天在网页上手工点击拖入，支持将导出的 CSV/XLSX 直接丢入 `data/incoming/` 目录：

```powershell
# 单次扫描并落库（适合断档批量回填、定时任务执行）：
.venv\Scripts\python -m backend.scripts.watch_incoming --once

# 后台守护监听模式（默认每 5 秒轮询，感知新报表毫秒级入库并自动归档到 processed/）：
.venv\Scripts\python -m backend.scripts.watch_incoming --interval 5
```

亦可通过 API 触发：`POST /api/uploads/scan-incoming`。

## 目录结构

```
backend/
  app/
    main.py               # FastAPI 入口：生命周期 / CORS / 统一错误包裹 / SPA 静态挂载
    config.py             # pydantic-settings 全局配置
    db.py                 # SQLite WAL engine / SessionLocal / init_db
    models/entities.py    # 8 个 ORM 实体（金额一律 Numeric，禁止 float）
    schemas/              # 适配器 YAML 的 pydantic 校验 + API Schema
    adapters/*.yaml       # 4 个内置平台适配器（核心架构约束：平台差异只在这里）
    core/                 # 解析 → 归一化 → 映射 → 聚合 → 预警 引擎
    exporters/            # Excel 日报 + 文字摘要
    api/                  # /api 路由（统一 {ok, data} / {ok, error} 包裹）
    services/             # 上传编排 / 日报编排
  scripts/                # init_db / gen_mock_data / smoke_test
  tests/                  # pytest（含 5 万行性能断言）
frontend/                 # Vue 3 + Vite + Element Plus + ECharts
data/                     # 运行时生成（app.db、uploads、exports）
samples/mock/             # 模拟原始文件 + manifest.json
docs/口径说明.md          # 指标口径与平台差异
```

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ECOM_DB_PATH` | `<项目根>/data/app.db` | SQLite 文件路径 |
| `EXPORT_DIR` | `<项目根>/data/exports` | Excel 日报输出目录 |
| `COMPANY_NAME` | `XX` | 日报标题 / 摘要中的公司名 |
| `SUMMARY_USE_WAN` | `true` | 摘要中金额 ≥ 1 万时用万元简写 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 允许的跨域来源（JSON 数组） |

## 主要 API

统一响应包裹：成功 `{"ok": true, "data": {...}}`；失败 `{"ok": false, "error": {"code", "message", "detail"}}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本 / DB 连通性 / 内置适配器数 |
| GET·PUT·POST | `/api/adapters*` | 适配器列表 / 详情 / 编辑（先校验后落库）/ 校验 / 重置 |
| POST | `/api/uploads` | multipart 批量上传，自动识别平台 + 归一化预览 |
| POST | `/api/uploads/{id}/commit` | 正式落库并触发 SKU 映射 |
| POST | `/api/uploads/{id}/reparse` | 改选平台后重新解析 |
| GET·POST·PUT·DELETE | `/api/skus*`、`/api/sku-mappings*` | SKU CRUD、映射列表 / 采纳 / 批量 / 建议 |
| GET | `/api/reports/daily?date=` | 看板聚合（6.4 契约结构） |
| POST | `/api/reports/daily/generate` | 生成日报（幂等，同日期覆盖） |
| GET | `/api/reports/daily/{date}/export` | 下载 Excel（中文文件名 RFC 5987） |
| GET·PUT | `/api/anomaly-rules*` | 预警规则查询与启停 / 阈值调整 |
| POST | `/api/mock-data/generate` | 生成模拟原始文件 |

完整契约见 `docs/需求规格说明.md` 第 7 节。

## 已知限制与偏差

- **numpy 版本**：规划锁定 `numpy ^1.26`，但当前环境为 Python 3.14，numpy 1.26 无对应发行包，
  实际安装 numpy 2.x（pandas 2.3 兼容），已在 `backend/requirements.txt` 注明。
- **python-multipart**：FastAPI 处理 multipart 文件上传的必需配套组件，规划依赖清单未列出但实际必需，已加入。
- **MVP 边界**：不含平台 API 对接 / 账号体系 / 实时刷新 / 利润核算 / Docker 化 / 数据库迁移（表结构变更时删除
  `data/app.db` 重建）。
- 四个内置适配器的源表头基于平台后台常见导出字段名整理，若与实际导出文件有出入，可直接在
  「适配器管理」页编辑 YAML 的 `column_map` / `column_aliases`（见 `docs/口径说明.md`）。
