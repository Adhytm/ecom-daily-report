# 多平台电商销售日报生成器

把每天从淘宝/天猫、抖店、拼多多、京东后台手工导出的销售文件，压缩到 3 分钟内变成一份可直接群发的日报：
**拖入文件 → 自动识别平台 → 归一化 → 出 Excel 日报 / 周报 + 群发文字摘要 + AI 经营诊断**。

## 功能总览

- **手工上传 CSV / XLSX**（多文件批量，单文件 ≤ 50MB），4 个内置平台适配器：
  淘宝/天猫（生意参谋）、抖音电商（抖店罗盘）、拼多多（商家后台）、京东（京东商智）
- **适配器 YAML 可视化管理**：查看 / 编辑 / 校验 / 恢复默认，平台差异全部由 YAML 表达，Python 代码零平台硬编码
- **归一化到统一 Schema** 并落库 SQLite（WAL 模式），行级错误明细可查（过滤 / 重复 / 解析失败）
- **SKU 跨平台映射**：精确码匹配 → 名称模糊建议（rapidfuzz，≥90 分高置信）→ 未映射归入【待映射】分组，绝不丢数据；
  SKU 档案可维护 `cost_price`（成本价）供利润核算使用
- **多维聚合**：平台 / 店铺 / 类目 / SKU；环比（vs 昨日）、周同比（vs 上周同日）；比率类指标环比用百分点差值（pp）
- **利润核算**：毛利 = 实际成交额 − 货品成本（SKU 成本价 × 件数）− 平台扣点 − 单件履约成本，
  经营利润 = 毛利 − 推广费。扣点与履约成本在适配器 YAML 配置。
  **成本未知则毛利为空、绝不用比例兜底虚报**；成本覆盖率 < 100% 时在日报中显式提示（去补 SKU 成本价）
- **异常预警规则引擎**：6 条默认规则（阈值可配），`gt / lt / abs_gt` 三种操作符 + `min_base` 小样本门槛
- **Excel 日报导出**：7 个 Sheet（日报总览 / 分平台 / 分店铺 / 分类目 / 分SKU / 异常预警 / 口径说明），含样式与条件格式
- **群发文字摘要**：≤ 800 字符，微信/钉钉友好，超长自动三级裁剪
- **周度复盘**：自然周（周一~周日）聚合，周环比 WoW + 7 天走势 + 分平台周汇总 + 本周爆款榜 Top 10，
  一键导出 4 Sheet 周报 Excel（周报总览 / 7天趋势明细 / 分平台周汇总 / 本周爆款榜 Top 10）与周报群发摘要
- **AI 智能诊断**：规则引擎负责算准数字，AI 负责归因。**本地优先**（Ollama / LM Studio，数据不出本机），
  也可切云端 OpenAI 兼容接口（DeepSeek / Qwen 等，需显式填 Key）；输出 Markdown 三段式
  【大盘异动归因 / 爆款风险预警 / 明日 Action Plan】
- **口径公式安全求值**：适配器的 `net_amount_formula` 走基于 `ast` 的白名单求值器（只允许四则运算 + 数字常量 +
  白名单数值字段），**禁用 `eval`**，函数调用 / 属性访问 / 下标一律拒绝；京东含运费等口径差异会主动提示可比性风险
- **Web 看板 7 个页面**（Vue 3 + Vue Router + Pinia + Element Plus + ECharts）：数据上传 / 日报看板 / 周度复盘 /
  业务诊断 / SKU 映射 / 适配器管理 / 导出中心
- **模拟数据生成器**（无真实数据时全链路验证）与 **Drop Folder 自动接入**（监听目录 / 断档批量补录）

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

### 6. AI 智能诊断（可选，需自备模型）

不配置也能跑全流程：不调用 AI 时，日报 / 周报 / 预警全部由本地规则引擎产出。要用 AI 归因，二选一：

```powershell
# 方式 A（推荐）：本地大模型，数据不出本机
#   装好 Ollama 后拉一个中文模型并启动服务（默认端口 11434）
ollama pull qwen2.5:7b
ollama serve

# 方式 B：云端 OpenAI 兼容接口（DeepSeek / Qwen 等），需自备 API Key
#   base_url 默认 https://api.deepseek.com/v1，model 默认 deepseek-chat
```

前端进入「业务诊断」页选好日期、提供方（本地 / 云端）、模型与 Key 即可；也可直接调接口：

```powershell
$body = @{
  date     = '2026-10-04'
  provider = 'local'            # local = 本地 Ollama / cloud = 云端
  model    = 'qwen2.5:7b'       # 云端可填 deepseek-chat
  # api_key = 'sk-...'          # provider = 'cloud' 时必填
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/ai/diagnose `
  -ContentType 'application/json; charset=utf-8' -Body $body
```

`provider=cloud` 时必须传 `api_key`（否则返回 `AI_KEY_REQUIRED`）；连不上本地服务返回
`AI_LOCAL_NOT_FOUND`，超时（>90s）返回 `AI_TIMEOUT`。**项目本身不内置、不保存任何 Key**，
Key 只随单次请求传入。

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
                          #   semantics.py：net_amount_formula 的 ast 白名单求值器（禁 eval）
    exporters/            # Excel 日报 / 周报 / 明细导出 + 文字摘要
    api/                  # /api 路由（统一 {ok, data} / {ok, error} 包裹）
                          #   reports.py 日报 · weekly.py 周报 · ai.py 诊断 · adapters/uploads/skus/mock
    services/             # 上传编排 / 日报编排 / 周报编排 / AI 诊断 / 自动接入
  scripts/                # init_db / gen_mock_data / smoke_test / watch_incoming / verify_samples
  tests/                  # 17 个 pytest 模块（含 5 万行性能断言）
frontend/                 # Vue 3 + Vite + Vue Router + Pinia + Element Plus + ECharts
  src/views/              # 7 个页面：上传 / 日报看板 / 周度复盘 / 业务诊断 / SKU 映射 / 适配器管理 / 导出中心
data/                     # 运行时生成（app.db、uploads、exports、incoming）
samples/mock/             # 模拟原始文件 + manifest.json
docs/口径说明.md          # 指标口径与平台差异
```

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ECOM_DB_PATH` | `<项目根>/data/app.db` | SQLite 文件路径 |
| `EXPORT_DIR` | `<项目根>/data/exports` | Excel 日报输出目录 |
| `INCOMING_DIR` | `<项目根>/data/incoming` | Drop Folder 监听目录（见第 5 节） |
| `COMPANY_NAME` | `XX` | 日报标题 / 摘要中的公司名 |
| `SUMMARY_USE_WAN` | `true` | 摘要中金额 ≥ 1 万时用万元简写 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 允许的跨域来源（JSON 数组） |

单文件上传上限 50MB 为代码内固定值（`config.py` 的 `max_upload_mb`），暂不支持环境变量覆盖。
AI 诊断的模型 / Key 不走环境变量，随单次请求传入。

## 主要 API

统一响应包裹：成功 `{"ok": true, "data": {...}}`；失败 `{"ok": false, "error": {"code", "message", "detail"}}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本 / DB 连通性 / 内置适配器数 |
| GET·PUT·POST | `/api/adapters*` | 适配器列表 / 详情 / 编辑（先校验后落库）/ 校验 / 重置 |
| GET | `/api/adapters-meta/builtin-keys` | 内置平台 key 清单 |
| POST | `/api/uploads` | multipart 批量上传，自动识别平台 + 归一化预览 |
| GET | `/api/uploads`、`/api/uploads/{id}/preview`、`/api/uploads/{id}/errors` | 上传批次列表 / 预览 / 行级错误明细 |
| POST | `/api/uploads/{id}/commit` | 正式落库并触发 SKU 映射 |
| POST | `/api/uploads/{id}/reparse` | 改选平台后重新解析 |
| DELETE | `/api/uploads/{id}` | 删除上传批次（连带其明细） |
| POST | `/api/uploads/scan-incoming` | 扫描 Drop Folder 并落库 |
| GET·POST·PUT·DELETE | `/api/skus*`、`/api/sku-mappings*` | SKU CRUD（含成本价）、映射列表 / 采纳 / 批量 / 建议 / 引用计数 |
| GET | `/api/reports/daily?date=` | 日报看板聚合（6.4 契约结构） |
| POST | `/api/reports/daily/generate` | 生成日报（幂等，同日期覆盖） |
| GET | `/api/reports/daily/{date}/export` | 下载 Excel 日报（中文文件名 RFC 5987） |
| GET | `/api/reports/daily/{date}/summary` | 群发文字摘要 |
| GET | `/api/reports/daily/{date}/detail-exports[/{platform_key}]` | 各平台可回灌明细 CSV 清单 / 下载 |
| GET | `/api/reports/weekly?date=` | 周报聚合（大盘 / 7 天走势 / 分平台 / Top 爆款，含周环比） |
| GET | `/api/reports/weekly/export` | 下载 4 Sheet 周报 Excel |
| GET | `/api/reports/weekly/summary` | 周报群发文字摘要 |
| GET | `/api/reports`、`/api/reports/latest-date` | 历史日报分页列表 / 最新有数据的日期 |
| POST | `/api/ai/diagnose` | AI 智能诊断（`provider=local` 走本地模型，`cloud` 走云端） |
| GET·PUT | `/api/anomaly-rules*` | 预警规则查询与启停 / 阈值调整 |
| GET | `/api/meta/categories`、`/api/meta/platforms`、`/api/meta/shops` | 类目 / 平台 / 店铺字典（筛选下拉用） |
| POST | `/api/mock-data/generate` | 生成模拟原始文件 |

完整契约见 `docs/需求规格说明.md` 第 7 节。

## 已知限制与偏差

- **numpy 版本**：规划锁定 `numpy ^1.26`，但当前环境为 Python 3.14，numpy 1.26 无对应发行包，
  实际安装 numpy 2.x（pandas 2.3 兼容），已在 `backend/requirements.txt` 注明。
- **python-multipart**：FastAPI 处理 multipart 文件上传的必需配套组件，规划依赖清单未列出但实际必需，已加入。
- **利润指标的边界**：毛利 / 经营利润只在成本已知（SKU 填了 `cost_price`）的行上统计，成本未知记为空而非 0；
  日报会给出成本覆盖率，覆盖率不足时利润指标只代表部分商品。
- **AI 诊断依赖外部模型**：项目不内置模型与 Key。本地模式需自备 Ollama / LM Studio；云端模式需自备 Key，
  且会把当日经营数据发往第三方（前端有醒目安全提示），请自行评估数据合规。
- **MVP 边界**：不含平台 API 对接 / 账号体系 / 实时刷新 / Docker 化 / 数据库迁移（表结构变更时删除
  `data/app.db` 重建）。
- 四个内置适配器的源表头基于平台后台常见导出字段名整理，若与实际导出文件有出入，可直接在
  「适配器管理」页编辑 YAML 的 `column_map` / `column_aliases`（见 `docs/口径说明.md`）。
