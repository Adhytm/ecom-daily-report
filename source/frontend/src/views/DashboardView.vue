<script setup>
// 日报看板：现代化商业办公风格 + 渐进式业务信息流
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Download,
  DocumentCopy,
  Opportunity,
  Refresh,
  ArrowDown,
  ArrowUp,
  Warning,
  CircleCheck,
} from '@element-plus/icons-vue'
import MetricCard from '../components/MetricCard.vue'
import TrendChart from '../components/TrendChart.vue'
import RankTable from '../components/RankTable.vue'
import AnomalyList from '../components/AnomalyList.vue'
import { useReportStore } from '../stores/report'
import { useAdapterStore } from '../stores/adapter'
import { formatDelta, formatMoney, formatNum, formatRate } from '../utils/format'
import { api, downloadUrl, toastError } from '../api'

const router = useRouter()
const report = useReportStore()
const adapterStore = useAdapterStore()

const dimTab = ref('platform')
const adapterList = ref([])
const showMoreMetrics = ref(false)
const copied = ref(false)
const exporting = ref(false)
// 首屏加载完成前，日期 watch 不重复发请求
const ready = ref(false)

onMounted(async () => {
  await report.initDate()
  try {
    await Promise.all([report.fetchMeta(), adapterStore.fetchList()])
    adapterList.value = adapterStore.list
    await report.fetchReport()
  } catch (e) {
    toastError(e)
  } finally {
    ready.value = true
  }
})

watch(
  () => report.reportDate,
  (newVal, oldVal) => {
    // 不要求 oldVal 非空：latest-date 失败时 reportDate 停在 ''，
    // 用户首次手动选日期也必须能触发加载
    if (ready.value && newVal && newVal !== oldVal) {
      report.fetchReport().catch(toastError)
    }
  },
)

const data = computed(() => report.data)
const dc = computed(() => data.value?.data_completeness)
const ov = computed(() => data.value?.overview || {})

// 第一梯队：核心大盘指标（4项，经营总揽）
const primaryMetrics = computed(() => [
  { label: '全渠道流水 (GMV)', key: 'gmv', kind: 'money', tooltip: ov.value.note || '各平台支付口径流水' },
  { label: '实际成交额 (净额)', key: 'net_amount', kind: 'money', tooltip: '流水剔除退款后的真实到手营业额' },
  { label: '全盘退款率', key: 'refund_rate', kind: 'rate', tooltip: '退款额 / GMV，比率类环比为百分点差值(pp)' },
  { label: '综合到手毛利', key: 'gross_profit', kind: 'money', tooltip: '成交额 − 货品成本 − 平台扣点 − 履约成本' },
])

// 第二梯队：进阶运营细节指标（8项，按需展开，避免铺天盖地）
const secondaryMetrics = computed(() => [
  { label: '购买转化率', key: 'conversion_rate', kind: 'rate', tooltip: '成交买家数 / 访客数' },
  { label: '客单价 (AOV)', key: 'avg_order_value', kind: 'money', tooltip: '实付金额 / 支付买家数' },
  { label: '推广 ROI', key: 'roi', kind: 'num', tooltip: 'GMV / 推广总花费' },
  { label: '直通车/推广支出', key: 'ad_cost', kind: 'money', tooltip: '各平台商业广告与投流消耗总计' },
  { label: '总支付件数', key: 'paid_qty', kind: 'num', tooltip: '当日总出货包裹件数' },
  { label: '全渠道访客数', key: 'visitors', kind: 'num', tooltip: '当日总进店流量' },
  { label: '毛利率', key: 'gross_margin', kind: 'rate', tooltip: '综合毛利 / 实际成交额' },
  { label: '经营利润', key: 'operating_profit', kind: 'money', tooltip: '综合毛利 − 推广花费' },
])

const topGmvItems = computed(() => data.value?.top?.by_gmv || [])
const topGrowthItems = computed(() => data.value?.top?.by_growth || [])
const topDeclineItems = computed(() => data.value?.top?.by_decline || [])
const topRefundItems = computed(() => data.value?.top?.by_refund || [])

function platformName(key) {
  return adapterList.value.find((a) => a.platform_key === key)?.display_name || key
}

async function refresh() {
  try {
    await report.fetchReport()
    ElMessage.success('报表数据已刷新')
  } catch (e) {
    toastError(e)
  }
}

async function regenerate() {
  try {
    await ElMessageBox.confirm(
      `将按当前库内数据重新计算 ${report.reportDate} 的全量日报，已生成的 Excel 与群发文案会被覆盖。`,
      '重新计算日报',
      { confirmButtonText: '重新计算', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const r = await report.generate()
    ElMessage.success(`日报已重新生成（${r.char_count} 字符摘要）`)
    await report.fetchReport()
  } catch (e) {
    toastError(e)
  }
}

async function oneClickExport() {
  exporting.value = true
  try {
    const d = report.reportDate
    await downloadUrl(api.reports.exportUrl(d))
    ElMessage.success(`${d} 日报 Excel 已下载`)
  } catch (e) {
    toastError(e)
  } finally {
    exporting.value = false
  }
}

async function copyDailySummary() {
  try {
    const s = await report.fetchSummary(report.reportDate)
    if (s?.summary_text) {
      await navigator.clipboard.writeText(s.summary_text)
      copied.value = true
      setTimeout(() => {
        copied.value = false
      }, 2500)
      ElMessage.success('今日群发摘要已复制，可直接前往钉钉/微信群粘贴！')
    } else {
      ElMessage.warning('暂无文字摘要')
    }
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="dashboard page-container">
    <!-- 业务快捷操作栏 -->
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar-inner">
        <div class="filter-group">
          <el-select
            v-model="report.platforms"
            multiple
            collapse-tags
            placeholder="筛选平台"
            clearable
            style="width: 170px"
          >
            <el-option
              v-for="p in report.metaOptions.platforms"
              :key="p"
              :value="p"
              :label="platformName(p)"
            />
          </el-select>
          <el-select
            v-model="report.shops"
            multiple
            collapse-tags
            placeholder="筛选店铺"
            clearable
            style="width: 170px"
          >
            <el-option v-for="s in report.metaOptions.shops" :key="s" :value="s" :label="s" />
          </el-select>
          <el-button @click="refresh">查询</el-button>
        </div>

        <div class="action-group">
          <el-button
            :type="copied ? 'success' : 'primary'"
            :plain="!copied"
            class="action-btn"
            @click="copyDailySummary"
          >
            <el-icon><DocumentCopy /></el-icon>
            <span>{{ copied ? '已复制群发文案 ✓' : '复制微信群发文案' }}</span>
          </el-button>
          <el-button
            :loading="exporting"
            class="action-btn"
            @click="oneClickExport"
          >
            <el-icon><Download /></el-icon>
            <span>导出 Excel</span>
          </el-button>
          <el-button
            plain
            class="action-btn"
            @click="router.push('/diagnosis')"
          >
            <el-icon><Opportunity /></el-icon>
            <span>业务诊断</span>
          </el-button>
          <el-button
            link
            size="small"
            :loading="report.generating"
            class="regen-btn"
            @click="regenerate"
          >
            <el-icon><Refresh /></el-icon>
            <span>重新计算</span>
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 无数据友好引导 -->
    <el-empty
      v-if="!data && !report.loading"
      description="本日暂无销售入库数据，通常在清晨导入各平台前一天的账单。"
      :image-size="120"
      class="empty-block"
    >
      <el-button type="primary" size="large" round @click="router.push('/upload')">
        去导入平台导出账单 →
      </el-button>
    </el-empty>

    <div v-else-if="report.loading" class="loading-block">
      <el-icon class="is-loading" :size="24"><Refresh /></el-icon>
      <div style="margin-top: 8px">正在加载最新销售看板…</div>
    </div>

    <template v-else>
      <!-- 渐进披露 1：经营归因简报（大白话核心结论） -->
      <div v-if="data?.diagnosis" class="briefing-banner">
        <div class="briefing-left">
          <div class="briefing-badge">
            <el-icon><Opportunity /></el-icon>
            <span>今日归因速览</span>
          </div>
          <div class="briefing-text">
            <el-tag
              :type="data.diagnosis.gmv_dod > 0 ? 'danger' : (data.diagnosis.gmv_dod < 0 ? 'success' : 'info')"
              size="small"
              class="driver-tag"
            >
              {{ data.diagnosis.driver_label }}
            </el-tag>
            <span class="explanation">{{ data.diagnosis.explanation }}</span>
          </div>
        </div>
        <div v-if="data.diagnosis.contributions" class="factors-wrap">
          <div class="factor-pill">
            <span class="f-label">访客流量</span>
            <span :class="data.diagnosis.contributions.visitors >= 0 ? 'f-up' : 'f-down'">
              {{ data.diagnosis.contributions.visitors > 0 ? '+' : '' }}{{ (data.diagnosis.contributions.visitors * 100).toFixed(1) }}pp
            </span>
          </div>
          <div class="factor-pill">
            <span class="f-label">转化承接</span>
            <span :class="data.diagnosis.contributions.conversion_rate >= 0 ? 'f-up' : 'f-down'">
              {{ data.diagnosis.contributions.conversion_rate > 0 ? '+' : '' }}{{ (data.diagnosis.contributions.conversion_rate * 100).toFixed(1) }}pp
            </span>
          </div>
          <div class="factor-pill">
            <span class="f-label">客单连带</span>
            <span :class="data.diagnosis.contributions.avg_order_value >= 0 ? 'f-up' : 'f-down'">
              {{ data.diagnosis.contributions.avg_order_value > 0 ? '+' : '' }}{{ (data.diagnosis.contributions.avg_order_value * 100).toFixed(1) }}pp
            </span>
          </div>
        </div>
      </div>

      <!-- 数据完整性预警条（如果有平台缺漏） -->
      <el-alert
        v-if="dc?.warning"
        type="warning"
        :closable="false"
        class="completeness-alert"
        :title="dc.warning"
        :description="`已导入平台：${dc.present_platforms.map(platformName).join('、') || '暂无'}`"
        show-icon
      />

      <!-- 渐进披露 2：核心大盘指标（首屏4张高权重卡片） -->
      <div class="primary-metrics-grid">
        <MetricCard
          v-for="m in primaryMetrics"
          :key="m.key"
          :label="m.label"
          :block="ov[m.key] || {}"
          :kind="m.kind"
          :tooltip="m.tooltip"
        />
      </div>

      <!-- 渐进披露 2.1：更多经营指标折叠区（按需展开，不把所有东西一次性摆出来） -->
      <div class="metrics-expander">
        <el-button
          link
          type="primary"
          class="toggle-btn"
          @click="showMoreMetrics = !showMoreMetrics"
        >
          <span>{{ showMoreMetrics ? '收起次要经营指标' : '查看完整经营指标 (转化率、客单价、ROI等8项)' }}</span>
          <el-icon><ArrowUp v-if="showMoreMetrics" /><ArrowDown v-else /></el-icon>
        </el-button>
      </div>

      <el-collapse-transition>
        <div v-show="showMoreMetrics" class="secondary-metrics-grid">
          <MetricCard
            v-for="m in secondaryMetrics"
            :key="m.key"
            :label="m.label"
            :block="ov[m.key] || {}"
            :kind="m.kind"
            :tooltip="m.tooltip"
          />
        </div>
      </el-collapse-transition>

      <!-- 中层：近30天走势 + 异常预警 -->
      <div class="analytics-panels">
        <el-card shadow="never" class="trend-card">
          <template #header>
            <div class="panel-header">
              <span class="p-title">近 30 天全渠道流水走势与大促标记</span>
              <span class="p-hint">自动标记 GMV 超过中位数 2 倍的大促异常峰值</span>
            </div>
          </template>
          <TrendChart :dates="data.trend?.dates || []" :series="data.trend?.series || {}" />
        </el-card>

        <el-card shadow="never" class="anomaly-card">
          <template #header>
            <div class="panel-header">
              <span class="p-title">今日经营异常预警 ({{ data.anomalies?.length || 0 }})</span>
            </div>
          </template>
          <AnomalyList :anomalies="data.anomalies || []" />
          <div class="card-footer-action">
            <el-button link type="primary" size="small" @click="router.push('/sku')">
              前往「SKU 映射」维护关联商品 →
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- 渐进披露 3：多维穿透分析（按 Tab 聚焦） -->
      <el-card shadow="never" class="dimensions-card">
        <el-tabs v-model="dimTab" class="modern-tabs">
          <el-tab-pane label="渠道平台" name="platform">
            <RankTable v-if="dimTab === 'platform'" type="platform" :rows="data.by_platform" />
          </el-tab-pane>
          <el-tab-pane label="店铺明细" name="shop">
            <RankTable v-if="dimTab === 'shop'" type="shop" :rows="data.by_shop" />
          </el-tab-pane>
          <el-tab-pane label="类目构成" name="category">
            <RankTable v-if="dimTab === 'category'" type="category" :rows="data.by_category" />
          </el-tab-pane>
          <el-tab-pane name="sku">
            <template #label>
              <span>商品表现</span>
              <el-badge
                v-if="data.by_sku?.unmapped?.length"
                :value="data.by_sku.unmapped.length"
                class="badge-dot"
                type="warning"
              />
            </template>
            <div v-if="data.by_sku?.unmapped?.length" class="unmapped-alert">
              <el-alert
                type="warning"
                :closable="false"
                :title="`发现 ${data.by_sku.unmapped.length} 个平台商品尚未映射内部 SKU，已归入下方【待映射商品】`"
              >
                <el-button
                  type="primary"
                  size="small"
                  @click="router.push({ path: '/sku', query: { tab: 'unmapped' } })"
                >
                  去完成映射
                </el-button>
              </el-alert>
            </div>
            <h4 class="section-title">已映射内部商品</h4>
            <RankTable type="sku-mapped" :rows="data.by_sku?.mapped || []" />

            <h4 class="section-title unmapped-head">【待映射商品】(平台新商品或未关联)</h4>
            <RankTable type="sku-unmapped" :rows="data.by_sku?.unmapped || []" />
          </el-tab-pane>

          <!-- 爆款榜单作为专属分析 Tab，不再平铺塞满页面底端 -->
          <el-tab-pane label="爆款榜单" name="tops">
            <div class="tops-grid">
              <el-card shadow="never" class="top-card">
                <template #header>
                  <span class="top-head">今日热卖榜 (GMV TOP 10)</span>
                </template>
                <el-empty v-if="!topGmvItems.length" description="暂无热卖数据" :image-size="40" />
                <div v-for="(it, i) in topGmvItems" :key="i" class="top-row">
                  <span class="rank-badge" :class="`rank-${i+1}`">{{ i + 1 }}</span>
                  <span class="item-name" :title="it.name">{{ it.name }}</span>
                  <span class="item-val">{{ formatMoney(it.gmv) }}</span>
                </div>
              </el-card>

              <el-card shadow="never" class="top-card">
                <template #header>
                  <span class="top-head up">涨幅先锋榜 (基数>1000)</span>
                </template>
                <el-empty v-if="!topGrowthItems.length" description="暂无数据" :image-size="40" />
                <div v-for="(it, i) in topGrowthItems" :key="i" class="top-row">
                  <span class="rank-badge" :class="`rank-${i+1}`">{{ i + 1 }}</span>
                  <span class="item-name" :title="it.name">{{ it.name }}</span>
                  <span class="item-val factor-up">{{ formatDelta(it.gmv_dod).text }}</span>
                </div>
              </el-card>

              <el-card shadow="never" class="top-card">
                <template #header>
                  <span class="top-head down">跌幅预警榜 (基数>1000)</span>
                </template>
                <el-empty v-if="!topDeclineItems.length" description="暂无数据" :image-size="40" />
                <div v-for="(it, i) in topDeclineItems" :key="i" class="top-row">
                  <span class="rank-badge" :class="`rank-${i+1}`">{{ i + 1 }}</span>
                  <span class="item-name" :title="it.name">{{ it.name }}</span>
                  <span class="item-val factor-down">{{ formatDelta(it.gmv_dod).text }}</span>
                </div>
              </el-card>

              <el-card shadow="never" class="top-card">
                <template #header>
                  <span class="top-head warn">退款关注榜 (GMV>500)</span>
                </template>
                <el-empty v-if="!topRefundItems.length" description="暂无数据" :image-size="40" />
                <div v-for="(it, i) in topRefundItems" :key="i" class="top-row">
                  <span class="rank-badge" :class="`rank-${i+1}`">{{ i + 1 }}</span>
                  <span class="item-name" :title="it.name">{{ it.name }}</span>
                  <span class="item-val factor-warn">{{ formatRate(it.refund_rate) }}</span>
                </div>
              </el-card>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.dashboard {
  width: 100%;
}

/* 顶部工具栏 */
.toolbar-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.filter-group {
  display: flex;
  gap: 10px;
  align-items: center;
}
.action-group {
  display: flex;
  gap: 8px;
  align-items: center;
}
.action-btn {
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.regen-btn {
  color: var(--el-text-color-secondary);
}

/* 经营归因简报横幅 */
.briefing-banner {
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: var(--radius-lg);
  padding: 14px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.briefing-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.briefing-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--el-color-primary);
}
.briefing-text {
  display: flex;
  align-items: center;
  gap: 8px;
}
.driver-tag {
  font-weight: 600;
}
.explanation {
  font-size: var(--text-md);
  color: var(--el-text-color-primary);
  font-weight: 500;
}
.factors-wrap {
  display: flex;
  gap: 8px;
}
.factor-pill {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: var(--text-xs);
  display: inline-flex;
  gap: 6px;
}
.f-label {
  color: var(--el-text-color-secondary);
}

.completeness-alert {
  border-radius: var(--radius-md);
}

/* 核心大盘指标（首屏4张） */
.primary-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 1024px) {
  .primary-metrics-grid { grid-template-columns: repeat(2, 1fr); }
}

/* 次要指标展开区 */
.metrics-expander {
  text-align: center;
  margin-top: -4px;
}
.toggle-btn {
  font-size: var(--text-sm);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.secondary-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding-top: 4px;
}
@media (max-width: 1024px) {
  .secondary-metrics-grid { grid-template-columns: repeat(2, 1fr); }
}

/* 中层图表面板 */
.analytics-panels {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 14px;
}
@media (max-width: 960px) {
  .analytics-panels { grid-template-columns: 1fr; }
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.p-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.p-hint {
  font-size: var(--text-xs);
  color: var(--el-text-color-placeholder);
}
.card-footer-action {
  margin-top: 10px;
  text-align: right;
}

/* 多维穿透 Tab 卡片 */
.modern-tabs :deep(.el-tabs__item) {
  font-size: var(--text-md);
  font-weight: 500;
}
.badge-dot {
  margin-left: 6px;
}
.unmapped-alert {
  margin-bottom: 12px;
}
.section-title {
  margin: 16px 0 10px;
  font-size: var(--text-md);
  color: var(--el-text-color-primary);
  font-weight: 600;
}
.unmapped-head {
  color: var(--el-color-warning);
}

/* 爆款排行榜单网格（放进专属Tab，收纳整洁） */
.tops-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 1100px) {
  .tops-grid { grid-template-columns: repeat(2, 1fr); }
}
.top-head {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.top-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  font-size: var(--text-sm);
  border-bottom: 1px dashed var(--el-border-color-extra-light);
}
.top-row:last-child {
  border-bottom: none;
}
.rank-badge {
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
/* 前三甲：克制的品牌色层级，不用扎眼金红 */
.rank-1 {
  background: var(--el-color-primary);
  color: #fff;
}
.rank-2 {
  background: var(--el-color-primary-light-3);
  color: #fff;
}
.rank-3 {
  background: var(--el-color-primary-light-5);
  color: #fff;
}
html.dark .rank-2, html.dark .rank-3 { color: var(--el-text-color-primary); }
.item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--el-text-color-primary);
}
.item-val {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.empty-block {
  padding: 60px 0;
  background: var(--el-bg-color);
  border-radius: var(--radius-lg);
  border: 1px solid var(--el-border-color-lighter);
}
.loading-block {
  text-align: center;
  padding: 60px 0;
  color: var(--el-text-color-secondary);
}
</style>
