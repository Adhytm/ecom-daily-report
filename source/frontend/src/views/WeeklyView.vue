<script setup>
// 周度经营复盘工作台 —— 现代化商业办公渐进式架构
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Download,
  DocumentCopy,
  Opportunity,
  Calendar,
  DataLine,
  PieChart,
  Trophy,
  ChatDotRound,
  ArrowDown,
  ArrowUp,
} from '@element-plus/icons-vue'
import { api, downloadUrl, toastError } from '../api'
import { formatDelta, formatMoney, formatNum, formatRate } from '../utils/format'
import { useReportStore } from '../stores/report'

const router = useRouter()
const reportStore = useReportStore()

const targetDate = ref('')
const loading = ref(false)
const weeklyData = ref(null)
const activeTab = ref('trend')
const summaryExpanded = ref(true)
const copied = ref(false)
const exporting = ref(false)
// 请求序号：快速切换日期时慢的旧响应不得覆盖新响应
let fetchSeq = 0

onMounted(async () => {
  await reportStore.initDate()
  targetDate.value = reportStore.reportDate || new Date().toISOString().slice(0, 10)
  await loadWeekly()
})

// 与全局日期选择器联动：header 上改日期（昨日/今日快捷项）本页必须响应
watch(
  () => reportStore.reportDate,
  (d) => {
    if (d && d !== targetDate.value) {
      targetDate.value = d
      loadWeekly()
    }
  },
)

async function loadWeekly() {
  if (!targetDate.value) return
  const seq = ++fetchSeq
  loading.value = true
  try {
    const res = await api.weekly.get(targetDate.value)
    if (seq === fetchSeq) weeklyData.value = res
  } catch (e) {
    if (seq === fetchSeq) {
      weeklyData.value = null
      // 整周无数据是正常空态而非错误
      if (e?.code !== 'NO_DATA_FOR_WEEK') toastError(e)
    }
  } finally {
    if (seq === fetchSeq) loading.value = false
  }
}

function onDateChange() {
  loadWeekly()
}

async function exportExcel() {
  if (!targetDate.value || exporting.value) return
  exporting.value = true
  try {
    await downloadUrl(api.weekly.exportUrl(targetDate.value))
    ElMessage.success('周度复盘 Excel 已下载')
  } catch (e) {
    toastError(e)
  } finally {
    exporting.value = false
  }
}

async function copySummary() {
  if (!weeklyData.value?.summary_text) return
  try {
    await navigator.clipboard.writeText(weeklyData.value.summary_text)
    copied.value = true
    ElMessage.success('周报文字摘要已成功复制到剪贴板！')
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    ElMessage.error('复制失败，请手动选取文本复制')
  }
}
</script>

<template>
  <div class="weekly-workbench page-container">
    <!-- 头部工具栏 -->
    <div class="header-card">
      <div class="left-controls">
        <span class="label">复盘周期：</span>
        <el-date-picker
          v-model="targetDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择周期内任意一天"
          :clearable="false"
          style="width: 170px"
          @change="onDateChange"
        />
        <el-button type="primary" :loading="loading" @click="loadWeekly">查询本周</el-button>
        <span v-if="weeklyData" class="week-range-text">
          周期范围：<b>{{ weeklyData.week_range_str }}</b>
        </span>
      </div>

      <div v-if="weeklyData" class="right-actions">
        <el-button
          type="success"
          :icon="Download"
          @click="exportExcel"
        >
          ⚡ 一键导出周报 Excel
        </el-button>
        <el-button
          type="primary"
          plain
          :icon="DocumentCopy"
          @click="copySummary"
        >
          {{ copied ? '✓ 已复制' : '复制周报摘要' }}
        </el-button>
        <el-button
          type="warning"
          plain
          :icon="Opportunity"
          @click="router.push('/diagnosis')"
        >
          AI 智能诊断
        </el-button>
      </div>
    </div>

    <el-empty
      v-if="!weeklyData && !loading"
      description="该周暂无销售数据，请选择其他周或先在「数据上传」导入账单。"
    />

    <template v-else-if="weeklyData">
      <!-- 核心指标卡片矩阵 -->
      <div class="metrics-grid">
        <div class="m-card">
          <div class="m-header">
            <span class="m-label">周总流水 (GMV)</span>
          </div>
          <div class="m-val">{{ formatMoney(weeklyData.total_gmv) }}</div>
          <div class="m-sub">
            周环比：
            <span :class="formatDelta(weeklyData.wow_gmv).cls">
              {{ formatDelta(weeklyData.wow_gmv).text }}
            </span>
          </div>
        </div>

        <div class="m-card">
          <div class="m-header">
            <span class="m-label">周实际成交 (剔退款)</span>
          </div>
          <div class="m-val">{{ formatMoney(weeklyData.total_net_amount) }}</div>
          <div class="m-sub">
            周环比：
            <span :class="formatDelta(weeklyData.wow_net_amount).cls">
              {{ formatDelta(weeklyData.wow_net_amount).text }}
            </span>
          </div>
        </div>

        <div class="m-card">
          <div class="m-header">
            <span class="m-label">周到手毛利</span>
          </div>
          <div class="m-val">{{ formatMoney(weeklyData.total_gross_profit) }}</div>
          <div class="m-sub">
            综合毛利率：<b>{{ formatRate(weeklyData.total_gross_margin) }}</b>
          </div>
        </div>

        <div class="m-card">
          <div class="m-header">
            <span class="m-label">全周平均退款率</span>
          </div>
          <div class="m-val" :style="{ color: (weeklyData.total_refund_rate || 0) >= 0.15 ? 'var(--color-warn)' : 'inherit' }">
            {{ formatRate(weeklyData.total_refund_rate) }}
          </div>
          <div class="m-sub">
            退款总额：{{ formatMoney(weeklyData.total_refund_amount) }}
          </div>
        </div>

        <div class="m-card">
          <div class="m-header">
            <span class="m-label">周总销量 / 客单价</span>
          </div>
          <div class="m-val">{{ formatNum(weeklyData.total_paid_qty) }} <span class="unit">件</span></div>
          <div class="m-sub">
            平均客单价：{{ formatMoney(weeklyData.avg_order_value) }}
          </div>
        </div>

        <div class="m-card">
          <div class="m-header">
            <span class="m-label">全周推广 ROI</span>
          </div>
          <div class="m-val">{{ weeklyData.total_roi ? weeklyData.total_roi.toFixed(2) : '—' }}</div>
          <div class="m-sub">
            渠道投流健康度监控
          </div>
        </div>
      </div>

      <!-- 渐进式多维明细：收纳进 Tab，避免页面长滚杂乱 -->
      <el-card shadow="never" class="details-card">
        <el-tabs v-model="activeTab" class="view-tabs">
          <!-- Tab 1: 7 天日度明细 -->
          <el-tab-pane name="trend">
            <template #label>
              <div class="tab-title">
                <el-icon><Calendar /></el-icon>
                <span>7 天每日走势明细</span>
              </div>
            </template>
            <el-table :data="weeklyData.daily_breakdown" size="small" border stripe>
              <el-table-column prop="date" label="日期" width="110" align="center" />
              <el-table-column prop="weekday" label="星期" width="70" align="center" />
              <el-table-column label="GMV (元)" align="right">
                <template #default="{ row }">{{ formatMoney(row.gmv) }}</template>
              </el-table-column>
              <el-table-column label="实际成交 (元)" align="right">
                <template #default="{ row }">{{ formatMoney(row.net_amount) }}</template>
              </el-table-column>
              <el-table-column label="退款率" width="100" align="right">
                <template #default="{ row }">
                  <span :style="{ color: row.refund_rate >= 0.15 ? 'var(--color-warn)' : 'inherit', fontWeight: row.refund_rate >= 0.15 ? '600' : 'normal' }">
                    {{ formatRate(row.refund_rate) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="综合毛利 (元)" align="right">
                <template #default="{ row }">{{ formatMoney(row.gross_profit) }}</template>
              </el-table-column>
              <el-table-column label="毛利率" width="90" align="right">
                <template #default="{ row }">{{ formatRate(row.gross_margin) }}</template>
              </el-table-column>
              <el-table-column label="销量件数" width="90" align="right">
                <template #default="{ row }">{{ formatNum(row.paid_qty) }}</template>
              </el-table-column>
              <el-table-column label="客单价 (元)" width="100" align="right">
                <template #default="{ row }">{{ formatMoney(row.avg_order_value) }}</template>
              </el-table-column>
              <el-table-column label="推广 ROI" width="90" align="right">
                <template #default="{ row }">{{ row.roi ? row.roi.toFixed(2) : '—' }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- Tab 2: 渠道平台分析 -->
          <el-tab-pane name="platforms">
            <template #label>
              <div class="tab-title">
                <el-icon><PieChart /></el-icon>
                <span>全渠道平台占比与环比</span>
              </div>
            </template>
            <el-table :data="weeklyData.platforms_summary" size="small" border stripe>
              <el-table-column prop="platform_name" label="平台渠道" width="130" />
              <el-table-column label="周 GMV (元)" align="right">
                <template #default="{ row }">{{ formatMoney(row.gmv) }}</template>
              </el-table-column>
              <el-table-column label="销售占比" width="100" align="right">
                <template #default="{ row }">
                  <b>{{ formatRate(row.share) }}</b>
                </template>
              </el-table-column>
              <el-table-column label="渠道退款率" width="100" align="right">
                <template #default="{ row }">
                  <span :style="{ color: row.refund_rate >= 0.15 ? '#F56C6C' : 'inherit' }">
                    {{ formatRate(row.refund_rate) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="贡献毛利 (元)" align="right">
                <template #default="{ row }">{{ formatMoney(row.gross_profit) }}</template>
              </el-table-column>
              <el-table-column label="周环比增长" width="110" align="right">
                <template #default="{ row }">
                  <span :class="formatDelta(row.wow_gmv).cls">{{ formatDelta(row.wow_gmv).text }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- Tab 3: 本周核心爆款 Top 10 -->
          <el-tab-pane name="skus">
            <template #label>
              <div class="tab-title">
                <el-icon><Trophy /></el-icon>
                <span>本周核心爆款 Top 10</span>
              </div>
            </template>
            <el-table :data="weeklyData.top_skus" size="small" border stripe>
              <el-table-column type="index" label="排名" width="55" align="center" />
              <el-table-column prop="name" label="商品名称" min-width="260" show-overflow-tooltip />
              <el-table-column prop="platform" label="主力渠道" width="110" align="center" />
              <el-table-column label="周总流水 GMV" width="130" align="right">
                <template #default="{ row }">
                  <span class="gmv-val">{{ formatMoney(row.gmv) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="销售件数" width="90" align="right">
                <template #default="{ row }">{{ formatNum(row.paid_qty) }}</template>
              </el-table-column>
              <el-table-column label="退款率" width="90" align="right">
                <template #default="{ row }">
                  <span :style="{ color: row.refund_rate >= 0.15 ? '#F56C6C' : 'inherit' }">
                    {{ formatRate(row.refund_rate) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <!-- 微信 / 钉钉群发周度复盘文字版（可折叠） -->
      <div class="summary-section">
        <div class="summary-toggle-bar" @click="summaryExpanded = !summaryExpanded">
          <div class="st-left">
            <el-icon class="st-icon"><ChatDotRound /></el-icon>
            <span class="st-title">微信 / 钉钉群发周度复盘速报</span>
            <span class="st-sub">直接复制后可发到运营复盘群或管理汇报群</span>
          </div>
          <div class="st-right">
            <el-button
              type="primary"
              size="small"
              :icon="DocumentCopy"
              @click.stop="copySummary"
            >
              {{ copied ? '✓ 已复制' : '一键复制周报' }}
            </el-button>
            <el-icon class="st-arrow">
              <ArrowUp v-if="summaryExpanded" />
              <ArrowDown v-else />
            </el-icon>
          </div>
        </div>

        <el-collapse-transition>
          <div v-show="summaryExpanded" class="summary-content">
            <el-input
              :model-value="weeklyData.summary_text"
              type="textarea"
              :rows="8"
              readonly
              class="summary-textarea"
            />
          </div>
        </el-collapse-transition>
      </div>
    </template>
  </div>
</template>

<style scoped>
.weekly-workbench {
  width: 100%;
}

.header-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 14px 20px;
}

.left-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.week-range-text {
  margin-left: 8px;
  font-size: var(--text-sm);
  color: var(--el-color-primary);
}

.right-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 指标矩阵 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 14px;
}

@media (max-width: 1200px) {
  .metrics-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 768px) {
  .metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.m-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px;
  transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-med) var(--ease-out);
}

.m-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
  border-color: var(--el-border-color);
}

.m-header {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}

.m-label {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.m-val {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
  margin-top: 4px;
}

.unit {
  font-size: var(--text-sm);
  font-weight: normal;
  color: var(--el-text-color-secondary);
}

.m-sub {
  margin-top: 8px;
  font-size: var(--text-xs);
  color: var(--el-text-color-regular);
}

/* 详情卡片 */
.details-card {
  border-radius: var(--radius-lg);
  border-color: var(--el-border-color-lighter);
  box-shadow: var(--shadow-sm);
}

.tab-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  font-weight: 600;
}

.gmv-val {
  font-weight: 600;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}

/* 群发摘要折叠卡 */
.summary-section {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.summary-toggle-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  cursor: pointer;
  background: var(--el-fill-color-light);
  user-select: none;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.summary-toggle-bar:hover {
  background: var(--el-fill-color);
}

.st-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.st-icon {
  font-size: 18px;
  color: var(--el-color-primary);
}

.st-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.st-sub {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  margin-left: 8px;
}

.st-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.st-arrow {
  color: var(--el-text-color-secondary);
  font-size: var(--text-sm);
}

.summary-content {
  padding: 16px 20px;
}

.summary-textarea {
  font-family: Consolas, monospace;
  font-size: var(--text-sm);
  line-height: 1.6;
}
</style>
