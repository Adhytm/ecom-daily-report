<script setup>
// 上传与解析工作台 —— 现代化商业办公流水线交互
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import {
  UploadFilled,
  CircleCheckFilled,
  WarningFilled,
  Files,
  DocumentChecked,
  Download,
  Delete,
  View,
} from '@element-plus/icons-vue'
import FileDropZone from '../components/FileDropZone.vue'
import MappingPreviewTable from '../components/MappingPreviewTable.vue'
import { useUploadStore } from '../stores/upload'
import { useAdapterStore } from '../stores/adapter'
import { useReportStore } from '../stores/report'
import { api, downloadUrl, toastError } from '../api'

const router = useRouter()
const upload = useUploadStore()
const adapterStore = useAdapterStore()
const reportStore = useReportStore()
const quickExporting = ref(false)
const demoLoading = ref(false)

const drawerVisible = ref(false)
const drawerItem = ref(null)
const drawerAdapterSpec = ref(null)
const drawerLoading = ref(false)

const errorDrawer = ref(false)
const errorItem = ref(null)
const errorRows = ref([])
const platformList = ref([])
const platformListFailed = ref(false)

// 行级异常类型的大白话标签（内部 error_type 不直接丢给用户）
const ERROR_TYPE_LABELS = {
  filtered_out: '被过滤',
  duplicate_row: '重复行',
  number_parse_failed: '数字解析失败',
  date_parse_failed: '日期解析失败',
  missing_required_value: '缺必需值',
}
function errorTypeLabel(t) {
  return ERROR_TYPE_LABELS[t] || t
}

// 标准统一定义的字段中英对照表（大白话业务标签）
const STANDARD_FIELD_LABELS = {
  stat_date: '统计日期 (stat_date)',
  shop_name: '店铺名称 (shop_name)',
  platform_product_code: '平台商品ID (platform_product_code)',
  product_name: '商品名称 (product_name)',
  category: '商品类目 (category)',
  gmv: '成交金额 / GMV (gmv)',
  paid_qty: '支付件数 (paid_qty)',
  refund_amount: '退款金额 (refund_amount)',
  visitors: '访客数 UV (visitors)',
  buyers: '支付人数 (buyers)',
  ad_cost: '广告消耗 (ad_cost)',
  order_qty: '支付订单量 (order_qty)',
}

// 平台商业品牌色彩与样式配置
const PLATFORM_BADGES = {
  taobao: { label: '天猫 / 淘宝', bg: '#fff1f0', border: '#ffa39e', text: '#cf1322' },
  doudian: { label: '抖音电商', bg: '#f9f0ff', border: '#d3adf7', text: '#531dab' },
  pinduoduo: { label: '拼多多', bg: '#fff7e6', border: '#ffd591', text: '#d46b08' },
  jd: { label: '京东', bg: '#fff2f0', border: '#ffccc7', text: '#f5222d' },
}

adapterStore.fetchList().then(() => {
  platformList.value = adapterStore.list.map((a) => ({
    key: a.platform_key,
    name: a.display_name,
  }))
}).catch((e) => {
  // 平台清单加载失败时，识别失败卡片的改选下拉会为空、文件无法复活，
  // 必须显式提示并给出重试入口，不能静默
  platformListFailed.value = true
  toastError(e)
})

function platformDisplayName(key) {
  return platformList.value.find((p) => p.key === key)?.name || key
}

async function retryLoadPlatforms() {
  try {
    await adapterStore.fetchList()
    platformList.value = adapterStore.list.map((a) => ({
      key: a.platform_key,
      name: a.display_name,
    }))
    platformListFailed.value = false
  } catch (e) {
    toastError(e)
  }
}

// 挂载时恢复上次未完成的待处理清单（parsed + failed），刷新页面不丢进度
onMounted(() => {
  upload.loadPending().catch(toastError)
})

// 当前工作流步骤判定
const currentStep = computed(() => {
  if (upload.items.length === 0 && upload.failedItems.length === 0) return 0
  const allValid = upload.items.some((i) => i.stats?.valid > 0)
  return allValid ? 1 : 0
})

async function onFiles(fileList) {
  try {
    await upload.uploadFiles(fileList)
    ElMessage.success('账单解析完成，请核对平台识别与有效行数')
  } catch (e) {
    toastError(e)
  }
}

// 一键体验：生成仿真账单并直接入库，省去「自己找文件再拖回来」的断链步骤
async function runDemo() {
  demoLoading.value = true
  try {
    const res = await api.mock.generate({ days: 35, skus: 40, ingest: true })
    const ing = res.ingest
    if (ing?.failed?.length) {
      ElMessage.warning(`部分仿真账单入库失败：${ing.failed.map((f) => f.filename).join('、')}`)
      return
    }
    const skipped = ing?.skipped_duplicates?.length || 0
    ElMessage.success(
      skipped
        ? `仿真数据已存在（跳过 ${skipped} 份重复），正在打开看板…`
        : `仿真账单已生成并入库，正在打开 ${res.report_date} 的经营看板…`,
    )
    reportStore.reportDate = res.report_date
    router.push('/dashboard')
  } catch (e) {
    toastError(e)
  } finally {
    demoLoading.value = false
  }
}

async function changePlatform(item, key) {
  if (!key || key === item.detected_platform) return
  try {
    await upload.reparse(item, key)
    ElMessage.success(`已按「${platformDisplayName(key)}」重新解析`)
  } catch (e) {
    toastError(e)
  }
}

// 移除上传卡片：级联删除原始文件与已入库明细，不可恢复，必须确认
async function removeItem(item) {
  try {
    await ElMessageBox.confirm(
      `移除「${item.filename}」将同时删除其已入库的销售明细与服务器上的原始文件，且已生成的相关日报会失效。此操作不可恢复。`,
      '移除账单文件',
      { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await upload.removeItem(item)
  } catch (e) {
    toastError(e)
  }
}

async function openPreview(item) {
  drawerItem.value = item
  drawerVisible.value = true
  drawerLoading.value = true
  try {
    const detail = await adapterStore.select(item.detected_platform)
    drawerAdapterSpec.value = detail?.structured || null
  } catch {
    drawerAdapterSpec.value = null
  } finally {
    drawerLoading.value = false
  }
}

const columnMappingsList = computed(() => {
  if (!drawerItem.value?.parse_meta?.detected_columns) return []
  const colMap = drawerAdapterSpec.value?.column_map || {}
  return drawerItem.value.parse_meta.detected_columns.map((srcCol) => {
    const targetKey = colMap[srcCol]
    const targetLabel = targetKey ? (STANDARD_FIELD_LABELS[targetKey] || targetKey) : null
    return {
      source: srcCol,
      targetKey,
      targetLabel,
      isMapped: !!targetKey,
    }
  })
})

async function openErrors(item) {
  errorItem.value = item
  errorDrawer.value = true
  try {
    const data = await upload.loadErrors(item.upload_id, { page: 1, size: 200 })
    errorRows.value = data.items
  } catch (e) {
    toastError(e)
    errorRows.value = []
  }
}

// 可提交的文件：有效行 > 0；重复文件需用户勾选确认（_forceCommit）才纳入
function committableItems() {
  return upload.items.filter((i) => i.stats?.valid > 0 && (!i.duplicate_of || i._forceCommit))
}

function noCommittableWarn() {
  const ready = upload.items.filter((i) => i.stats?.valid > 0)
  if (!ready.length) {
    ElMessage.warning('没有可提交的文件')
  } else {
    ElMessage.warning('待提交的都是与历史完全相同的重复文件，如确需重复入库，请先在卡片上勾选确认')
  }
}

async function commitAll() {
  if (upload.committing || quickExporting.value) return
  const valid = committableItems()
  if (!valid.length) {
    noCommittableWarn()
    return
  }
  try {
    await ElMessageBox.confirm(
      `将 ${valid.length} 个文件的归一化数据正式入库？`,
      '全部提交入库',
      { type: 'info', confirmButtonText: '确定入库', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const { results, failures, skippedDuplicates } = await upload.commitAll()
    const failTip = failures.length
      ? `；${failures.length} 个失败（${failures.map((f) => f.filename).join('、')}），已保留在清单中`
      : ''
    const skipTip = skippedDuplicates ? `；已跳过 ${skippedDuplicates} 份重复文件` : ''
    if (!results.length) {
      ElMessage.error(`没有文件入库成功${failTip}${skipTip}`)
      return
    }
    const unmapped = results.reduce((s, r) => s + (r.unmapped_count || 0), 0)
    const msg = `已入库 ${results.reduce((s, r) => s + r.row_count_valid, 0)} 行；未映射商品 ${unmapped} 个，可前往「SKU 映射」处理${failTip}${skipTip}`
    if (failures.length) ElMessage.warning(msg)
    else ElMessage.success(msg)
    // 看板日期对齐到本批入库数据的最新统计日期：
    // 用户补传更早日期账单时，跳过去不能还停在别的日期让人以为数据丢了
    const targetDate = results.map((r) => r.max_stat_date).filter(Boolean).sort().pop()
    if (targetDate) reportStore.reportDate = targetDate
    if (!failures.length) router.push('/dashboard')
  } catch (e) {
    toastError(e)
  }
}

async function commitAndExport() {
  const valid = committableItems()
  if (!valid.length) {
    noCommittableWarn()
    return
  }
  if (quickExporting.value || upload.committing) return
  try {
    await ElMessageBox.confirm(
      `将 ${valid.length} 个文件正式入库，并生成最新日期日报（Excel 自动下载、群发文案自动复制）。`,
      '一键入库并导出',
      { type: 'info', confirmButtonText: '开始执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  quickExporting.value = true
  try {
    const { results, failures } = await upload.commitAll()
    if (!results.length) {
      ElMessage.error('没有文件入库成功，无法生成日报（失败文件已保留在清单中）')
      return
    }
    const totalValid = results.reduce((s, r) => s + r.row_count_valid, 0)

    // 报表日期取「本批入库数据覆盖的最新日期」，而不是全库最新日期——
    // 库里可能有更晚日期的其他数据，直接取 latest 会导出到别的日期
    const targetDate =
      results.map((r) => r.max_stat_date).filter(Boolean).sort().pop()
      || (await api.latestDate())?.date
      || new Date().toISOString().slice(0, 10)
    reportStore.reportDate = targetDate

    const genRes = await reportStore.generate(targetDate)
    await downloadUrl(api.reports.exportUrl(targetDate))

    let copied = false
    if (genRes?.summary_text) {
      try {
        await navigator.clipboard.writeText(genRes.summary_text)
        copied = true
      } catch {
        copied = false
      }
    }

    const failTip = failures.length
      ? `；${failures.length} 个文件入库失败（已保留在清单中）`
      : ''
    ElNotification({
      title: '一键交付完成',
      message:
        `已入库 ${totalValid} 行数据${failTip}；${targetDate} 日报 Excel 已下载；` +
        (copied ? '群发摘要已复制到剪贴板，可直接粘贴！' : '摘要复制失败，请到「导出中心」手动复制。'),
      type: failures.length || !copied ? 'warning' : 'success',
      duration: 6000,
    })

    if (!failures.length) router.push('/dashboard')
  } catch (e) {
    toastError(e)
  } finally {
    quickExporting.value = false
  }
}

function statusTag(stats) {
  if (!stats) return { type: 'info', text: '待解析' }
  if (stats.valid === 0) return { type: 'danger', text: '无有效行' }
  if (stats.failed + stats.filtered > 0) return { type: 'warning', text: '含过滤/异常' }
  return { type: 'success', text: '全量有效' }
}
</script>

<template>
  <div class="upload-view page-container">
    <!-- 业务工作流三步指引（商业办公向导） -->
    <div class="pipeline-stepper">
      <div class="step-node" :class="{ active: currentStep === 0, done: currentStep > 0 }">
        <div class="step-num">1</div>
        <div class="step-text">
          <div class="step-title">导入平台账单</div>
          <div class="step-desc">拖入天猫/抖店/拼多多等原始数据</div>
        </div>
      </div>
      <div class="step-arrow">→</div>
      <div class="step-node" :class="{ active: currentStep === 1, done: currentStep > 1 }">
        <div class="step-num">2</div>
        <div class="step-text">
          <div class="step-title">自动识别核对</div>
          <div class="step-desc">核查平台匹配度与有效数据行数</div>
        </div>
      </div>
      <div class="step-arrow">→</div>
      <div class="step-node">
        <div class="step-num">3</div>
        <div class="step-text">
          <div class="step-title">一键入库出报</div>
          <div class="step-desc">自动入库、导出 Excel 与群发摘要</div>
        </div>
      </div>
    </div>

    <!-- 上传拖拽投放区（解析中锁定，防止并发上传互踩） -->
    <FileDropZone :disabled="upload.uploading" @files="onFiles" />

    <div v-if="upload.uploading" class="loading-bar">
      <el-icon class="is-loading"><UploadFilled /></el-icon>
      <span>正在智能解析文件并对齐表头…</span>
    </div>

    <!-- 空状态指引 -->
    <div v-if="!upload.items.length && !upload.failedItems.length && !upload.uploading" class="empty-guide">
      <div class="guide-content">
        <div class="guide-title">日常运营 3 分钟高效交付模式</div>
        <p class="guide-tip">
          每天早晨从各电商后台下载昨日销售账单，直接打包或多选拖入上方区域。
          系统自动完成编码识别、字段匹配与去重，一键生成全渠道统一经营看板。
        </p>
        <el-button type="primary" plain :loading="demoLoading" @click="runDemo">
          一键生成仿真账单并查看效果
        </el-button>
      </div>
    </div>

    <!-- 平台清单加载失败：识别失败卡片无法改选平台复活，给显式提示与重试 -->
    <el-alert
      v-if="platformListFailed"
      type="error"
      show-icon
      :closable="false"
      title="平台清单加载失败，「识别失败」的文件暂时无法改选平台重新解析"
    >
      <el-button size="small" @click="retryLoadPlatforms">重试加载</el-button>
    </el-alert>

    <!-- 识别失败文件：改选平台即可重新解析，无需重新上传 -->
    <div v-if="upload.failedItems.length" class="cards-section">
      <div class="section-head">
        <span class="title">识别失败的文件（{{ upload.failedItems.length }} 份）</span>
        <span class="sub">文件已保存在系统里，选择正确的平台后自动重新解析，无需重新上传</span>
      </div>
      <div class="cards-grid">
        <el-card
          v-for="item in upload.failedItems"
          :key="'failed-' + item.upload_id"
          shadow="hover"
          class="bill-card failed-card"
        >
          <template #header>
            <div class="card-head">
              <div class="fname-box">
                <el-icon class="file-icon"><Files /></el-icon>
                <span class="fname" :title="item.filename">{{ item.filename }}</span>
              </div>
              <div class="head-tags">
                <el-tag type="danger" size="small" effect="plain">识别失败</el-tag>
                <el-button
                  link
                  type="danger"
                  size="small"
                  :icon="Delete"
                  @click="removeItem(item)"
                >
                  移除
                </el-button>
              </div>
            </div>
          </template>
          <el-alert
            type="error"
            :closable="false"
            show-icon
            :title="item.error_message || '解析失败'"
            class="failed-alert"
          />
          <div v-if="!item.unrecoverable" class="platform-select-row failed-reparse-row">
            <span class="label">手动指定平台：</span>
            <el-select
              size="default"
              class="platform-select"
              placeholder="选择平台后自动重新解析"
              @change="(v) => changePlatform(item, v)"
            >
              <el-option
                v-for="p in platformList"
                :key="p.key"
                :value="p.key"
                :label="p.name"
              />
            </el-select>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 文件识别与核对卡片 -->
    <div v-if="upload.items.length" class="cards-section">
      <div class="section-head">
        <span class="title">待入库文件清单（{{ upload.items.length }} 份）</span>
        <span class="sub">请核对各文件识别出的电商平台及有效行数</span>
      </div>

      <div class="cards-grid">
        <el-card
          v-for="item in upload.items"
          :key="item.upload_id"
          shadow="hover"
          class="bill-card"
        >
          <template #header>
            <div class="card-head">
              <div class="fname-box">
                <el-icon class="file-icon"><Files /></el-icon>
                <span class="fname" :title="item.filename">{{ item.filename }}</span>
              </div>
              <div class="head-tags">
                <el-tag :type="statusTag(item.stats).type" size="small" effect="plain">
                  {{ statusTag(item.stats).text }}
                </el-tag>
                <el-button
                  link
                  type="danger"
                  size="small"
                  :icon="Delete"
                  @click="removeItem(item)"
                >
                  移除
                </el-button>
              </div>
            </div>
          </template>

          <!-- 平台识别与置信度 -->
          <div class="platform-match-box">
            <div class="platform-select-row">
              <span class="label">归属平台：</span>
              <el-select
                :model-value="item.detected_platform"
                size="default"
                class="platform-select"
                @change="(v) => changePlatform(item, v)"
              >
                <el-option
                  v-for="p in platformList"
                  :key="p.key"
                  :value="p.key"
                  :label="p.name"
                />
              </el-select>
              <div
                v-if="PLATFORM_BADGES[item.detected_platform]"
                class="brand-badge"
                :style="{
                  background: PLATFORM_BADGES[item.detected_platform].bg,
                  borderColor: PLATFORM_BADGES[item.detected_platform].border,
                  color: PLATFORM_BADGES[item.detected_platform].text,
                }"
              >
                {{ PLATFORM_BADGES[item.detected_platform].label }}
              </div>
            </div>

            <div class="confidence-row">
              <span class="conf-label">表头匹配度</span>
              <el-progress
                :percentage="Math.round((item.detect_confidence || 0) * 100)"
                :stroke-width="6"
                :color="item.detect_confidence >= 0.7 ? 'var(--el-color-success)' : 'var(--el-color-warning)'"
                class="conf-progress"
              />
              <span class="conf-pct">
                {{ Math.round((item.detect_confidence || 0) * 100) }}%
              </span>
            </div>
          </div>

          <!-- 重复文件预警：默认跳过提交，显式勾选后才入库 -->
          <el-alert
            v-if="item.duplicate_of"
            type="warning"
            :closable="false"
            show-icon
            class="dup-alert"
            :title="`该账单与历史文件 #${item.duplicate_of} 内容完全相同。为避免统计翻倍，提交时将默认跳过此文件`"
          />
          <el-checkbox
            v-if="item.duplicate_of"
            v-model="item._forceCommit"
            size="small"
            class="dup-force"
          >
            我已确认这是两笔不同的业务数据，仍要重复入库
          </el-checkbox>
          <!-- 单文件提交失败原因（部分入库失败后保留卡片并标注） -->
          <el-alert
            v-if="item._commitError"
            type="error"
            :closable="false"
            show-icon
            class="dup-alert"
            :title="`本次入库失败：${item._commitError}`"
          />

          <!-- 行数核对指标胶囊 -->
          <div class="stats-pills">
            <div class="pill total">
              <span class="k">总计</span>
              <span class="v">{{ item.stats?.total ?? 0 }} 行</span>
            </div>
            <div class="pill valid">
              <span class="k">有效</span>
              <span class="v">{{ item.stats?.valid ?? 0 }} 行</span>
            </div>
            <div class="pill filtered">
              <span class="k">过滤</span>
              <span class="v">{{ item.stats?.filtered ?? 0 }}</span>
            </div>
            <div class="pill duplicated">
              <span class="k">重复</span>
              <span class="v">{{ item.stats?.duplicated ?? 0 }}</span>
            </div>
            <div class="pill failed">
              <span class="k">异常</span>
              <span class="v">{{ item.stats?.failed ?? 0 }}</span>
            </div>
          </div>

          <!-- 操作行 -->
          <div class="card-footer-actions">
            <el-button
              size="small"
              :icon="View"
              @click="openPreview(item)"
            >
              查看字段映射与前 20 行预览
            </el-button>
            <el-button
              v-if="(item.stats?.failed || 0) + (item.stats?.filtered || 0) > 0"
              size="small"
              type="warning"
              plain
              @click="openErrors(item)"
            >
              行级异常明细
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- 底部常驻提交流水线操作条 -->
      <div class="workflow-commit-bar">
        <div class="commit-info">
          <span>共就绪 <b>{{ upload.items.length }}</b> 个账单文件，</span>
          <span>有效数据合计 <b>{{ upload.items.reduce((s, i) => s + (i.stats?.valid || 0), 0) }}</b> 行</span>
        </div>
        <div class="commit-btns">
          <el-button
            type="success"
            size="large"
            :icon="Download"
            :loading="quickExporting || upload.committing"
            class="btn-hero"
            @click="commitAndExport"
          >
            ⚡ 一键入库并导出今日日报
          </el-button>
          <el-button
            type="primary"
            size="large"
            plain
            :icon="DocumentChecked"
            :loading="upload.committing || quickExporting"
            @click="commitAll"
          >
            仅提交入库
          </el-button>
        </div>
      </div>
    </div>

    <!-- 字段映射与预览抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="`字段映射核对 · ${drawerItem?.filename || ''}`"
      size="65%"
    >
      <div v-loading="drawerLoading" class="drawer-inner">
        <div class="drawer-sec-title">
          <span>列映射对照表（源表格字段 → 统一标准指标）</span>
          <span class="drawer-sec-sub">系统已依据【{{ platformDisplayName(drawerItem?.detected_platform) }}】模板完成自动解析</span>
        </div>

        <el-table :data="columnMappingsList" size="small" border stripe max-height="320">
          <el-table-column prop="source" label="上传账单原始表头" min-width="180">
            <template #default="{ row }">
              <span class="source-col-name">{{ row.source }}</span>
            </template>
          </el-table-column>
          <el-table-column label="→" width="50" align="center">
            <template #default>→</template>
          </el-table-column>
          <el-table-column label="对应系统统一字段" min-width="220">
            <template #default="{ row }">
              <span v-if="row.isMapped" class="mapped-text">
                {{ row.targetLabel }}
              </span>
              <span v-else class="unmapped-muted">
                — 未映射（自动忽略）
              </span>
            </template>
          </el-table-column>
          <el-table-column label="映射状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.isMapped ? 'success' : 'info'"
                effect="plain"
              >
                {{ row.isMapped ? '已匹配' : '忽略' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>

        <el-alert
          v-if="drawerItem?.semantics_note"
          type="info"
          :closable="false"
          class="sem-alert"
          :title="`平台口径注解：${drawerItem.semantics_note}`"
        />

        <div class="drawer-sec-title mt-16">
          <span>归一化数据前 20 行示例（核对入库格式）</span>
        </div>
        <MappingPreviewTable :rows="drawerItem?.preview_rows || []" />
      </div>
    </el-drawer>

    <!-- 行级错误明细抽屉 -->
    <el-drawer
      v-model="errorDrawer"
      :title="`行级异常明细 · ${errorItem?.filename || ''}`"
      size="55%"
    >
      <el-table :data="errorRows" size="small" border max-height="600">
        <el-table-column prop="row_number" label="行号" width="80" align="center" />
        <el-table-column prop="error_type" label="异常类型" width="160">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.error_type === 'filtered_out' || row.error_type === 'duplicate_row' ? 'warning' : 'danger'"
            >
              {{ errorTypeLabel(row.error_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error_message" label="具体说明" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<style scoped>
.upload-view {
  width: 100%;
}

/* 业务流程三步指引 */
.pipeline-stepper {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 16px 24px;
}

.step-node {
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.55;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.step-node.active, .step-node.done {
  opacity: 1;
}

.step-num {
  width: 28px;
  height: 28px;
  border-radius: var(--el-border-radius-round);
  background: var(--el-fill-color-darker);
  color: var(--el-text-color-regular);
  font-size: var(--text-sm);
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-node.active .step-num {
  background: var(--el-color-primary);
  color: #fff;
}

.step-node.done .step-num {
  background: var(--el-color-success);
  color: #fff;
}

.step-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.step-desc {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}

.step-arrow {
  color: var(--el-text-color-placeholder);
  font-size: var(--text-md);
  font-weight: bold;
}

/* 加载动画 */
.loading-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  border-radius: var(--radius-md);
  font-size: var(--text-md);
}

/* 空状态卡片 */
.empty-guide {
  background: var(--el-bg-color);
  border: 1px dashed var(--el-border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 28px;
  text-align: center;
}

.guide-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 6px;
}

.guide-tip {
  max-width: 600px;
  margin: 0 auto 16px auto;
  font-size: var(--text-sm);
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

/* 卡片部分 */
.cards-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.section-head .title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.section-head .sub {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(460px, 1fr));
  gap: 14px;
}

@media (max-width: 768px) {
  .cards-grid {
    grid-template-columns: 1fr;
  }
}

.bill-card {
  border-radius: var(--radius-lg);
  border-color: var(--el-border-color-lighter);
  box-shadow: var(--shadow-sm);
  transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-med) var(--ease-out);
}

.bill-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
  border-color: var(--el-border-color);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.fname-box {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.file-icon {
  font-size: 16px;
  color: var(--el-color-primary);
}

.fname {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.head-tags {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 平台识别 */
.platform-match-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--el-fill-color-lighter);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}

.platform-select-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.platform-select-row .label {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.platform-select {
  width: 190px;
}

.brand-badge {
  font-size: var(--text-xs);
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid;
}

:global(html.dark) .brand-badge {
  background: var(--el-fill-color) !important;
  border-color: var(--el-border-color) !important;
}

.confidence-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.conf-label {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  width: 65px;
}

.conf-progress {
  flex: 1;
}

.conf-pct {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--el-text-color-regular);
  width: 35px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.dup-alert {
  margin-top: 10px;
}

.dup-force {
  margin-top: 8px;
  margin-left: 4px;
}

.failed-card {
  border-radius: var(--radius-lg);
  border-color: var(--el-color-danger-light-5);
  box-shadow: var(--shadow-sm);
}

.failed-alert {
  margin-bottom: 10px;
}

.failed-reparse-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.failed-reparse-row .label {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

/* 统计胶囊 */
.stats-pills {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.pill {
  flex: 1;
  min-width: 70px;
  background: var(--el-fill-color-light);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.pill .k {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}

.pill .v {
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--el-text-color-primary);
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}

.pill.valid .v {
  color: var(--el-color-success);
}

.pill.filtered .v, .pill.duplicated .v {
  color: var(--el-color-warning);
}

.pill.failed .v {
  color: var(--el-color-danger);
}

.card-footer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

/* 底部常驻提交流水线 */
.workflow-commit-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: var(--radius-lg);
  padding: 16px 24px;
  box-shadow: var(--shadow-md);
}

.commit-info {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.commit-info b {
  color: var(--el-color-primary);
  font-size: var(--text-lg);
  font-variant-numeric: tabular-nums;
}

.commit-btns {
  display: flex;
  align-items: center;
  gap: 12px;
}

.btn-hero {
  font-weight: 600;
  padding-left: 20px;
  padding-right: 20px;
}

/* 抽屉内容 */
.drawer-inner {
  display: flex;
  flex-direction: column;
}

.drawer-sec-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 10px;
}

.drawer-sec-sub {
  font-size: var(--text-xs);
  font-weight: normal;
  color: var(--el-text-color-secondary);
}

.source-col-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.mapped-text {
  font-weight: 600;
  color: var(--el-color-primary);
}

.unmapped-muted {
  color: var(--el-text-color-placeholder);
}

.sem-alert {
  margin-top: 12px;
}

.mt-16 {
  margin-top: 16px;
}
</style>
