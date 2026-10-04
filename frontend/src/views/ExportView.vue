<script setup>
// 导出中心（规划 8.6）：生成日报 → Excel 下载 + 文字摘要复制 + 历史日报
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useReportStore } from '../stores/report'
import { api, downloadUrl, toastError } from '../api'

const report = useReportStore()
const generating = ref(false)
const summaryText = ref('')
const charCount = ref(0)
const lastGenerated = ref(null)

onMounted(async () => {
  await report.initDate()
  try {
    await report.fetchHistory()
    if (report.history.items.length) {
      await loadSummary(report.history.items[0].report_date)
    }
  } catch (e) {
    /* 首次使用无历史日报，不提示错误 */
  }
})

async function generate() {
  generating.value = true
  try {
    const r = await report.generate()
    lastGenerated.value = r
    summaryText.value = r.summary_text
    charCount.value = r.char_count
    await report.fetchHistory()
    ElMessage.success('日报已生成')
  } catch (e) {
    toastError(e)
  } finally {
    generating.value = false
  }
}

async function loadSummary(date) {
  try {
    const s = await report.fetchSummary(date)
    summaryText.value = s.summary_text
    charCount.value = s.char_count
    lastGenerated.value = { report_date: date, excel_url: api.reports.exportUrl(date) }
  } catch {
    summaryText.value = ''
  }
}

async function copySummary() {
  if (!summaryText.value) return
  try {
    await navigator.clipboard.writeText(summaryText.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    // navigator.clipboard 兜底：document.execCommand
    const ta = document.createElement('textarea')
    ta.value = summaryText.value
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      ElMessage.success('已复制到剪贴板')
    } catch {
      ElMessage.error('复制失败，请手动全选复制')
    }
    document.body.removeChild(ta)
  }
}

async function download(row) {
  try {
    await downloadUrl(api.reports.exportUrl(row.report_date))
  } catch (e) {
    toastError(e)
  }
}

function viewSummary(row) {
  loadSummary(row.report_date)
}

// ---- 可回灌明细导出 ----
// 日报 Excel 是给人看的成品报表（聚合/占比/环比），缺「统计日期 + 平台商品ID」
// 两个必需维度，导入端必然拒绝；这里导出的是原始明细粒度，列名用各平台后台的
// 真实列名，所以能直接拖回上传页重新入库。
const detailDialog = ref(false)
const detailLoading = ref(false)
const detailItems = ref([])
const detailNote = ref('')
const detailDate = ref('')

async function openDetailExports(date) {
  const d = date || report.reportDate || report.data?.report_date
  if (!d) {
    ElMessage.warning('请先选择日期')
    return
  }
  detailDate.value = d
  detailDialog.value = true
  detailLoading.value = true
  detailItems.value = []
  try {
    const r = await api.reports.detailExports(d)
    detailItems.value = r.items || []
    detailNote.value = r.note || ''
  } catch (e) {
    toastError(e)
  } finally {
    detailLoading.value = false
  }
}

async function downloadDetail(item) {
  try {
    await downloadUrl(api.reports.detailExportUrl(detailDate.value, item.platform_key))
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="export-view page-container">
    <el-card shadow="never">
      <template #header>生成日报</template>
      <div class="gen-row">
        <el-date-picker
          v-model="report.reportDate"
          type="date"
          value-format="YYYY-MM-DD"
          :clearable="false"
          style="width: 170px"
        />
        <el-button type="primary" :loading="generating" @click="generate">生成日报</el-button>
        <el-button @click="openDetailExports()">导出可回灌明细</el-button>
      </div>
      <div class="hint">
        日报 Excel 是给人看的成品报表（聚合 / 占比 / 环比），不能当数据源导入；
        要「导出后再导回来」请用<b>可回灌明细</b>：它按各平台后台的真实列名导出，
        含统计日期与平台商品 ID，可直接拖回上传页重新入库。
      </div>
    </el-card>

    <el-dialog v-model="detailDialog" title="可回灌明细导出" width="720px">
      <div v-loading="detailLoading">
        <el-alert v-if="detailNote" :title="detailNote" type="info" :closable="false" class="note" />
        <el-table v-if="detailItems.length" :data="detailItems" size="small" border stripe>
          <el-table-column prop="display_name" label="平台" width="180" />
          <el-table-column prop="row_count" label="行数" width="80" />
          <el-table-column label="列名（平台原始）" show-overflow-tooltip>
            <template #default="{ row }">{{ row.columns.join('、') }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="downloadDetail(row)">下载</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else-if="!detailLoading" :description="`${detailDate} 没有可导出的明细`" />
      </div>
      <template #footer>
        <span class="foot-hint">下载后可多选一起拖回「上传」页验证回灌</span>
        <el-button @click="detailDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-card v-if="lastGenerated" shadow="never" class="summary-card">
      <template #header>
        <div class="head-row">
          <span>
            文字摘要
            <span :class="charCount > 800 ? 'over' : 'ok'">（{{ charCount }} 字符）</span>
          </span>
          <div class="tools">
            <el-button size="small" @click="copySummary">复制</el-button>
            <el-button size="small" type="primary" @click="download(lastGenerated)">下载 Excel</el-button>
          </div>
        </div>
      </template>
      <el-input
        :model-value="summaryText"
        type="textarea"
        :rows="16"
        readonly
        class="summary-text"
      />
    </el-card>

    <el-card shadow="never" class="history-card">
      <template #header>历史日报</template>
      <el-empty v-if="!report.history.items.length" description="还没有生成过日报。选择日期后点击「生成日报」。">
        <el-button type="primary" @click="generate" :loading="generating">生成本日日报</el-button>
      </el-empty>
      <el-table v-else :data="report.history.items" size="small" border stripe>
        <el-table-column prop="report_date" label="报告日期" width="130" />
        <el-table-column prop="generated_at" label="生成时间" width="180" />
        <el-table-column label="摘要" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.has_summary" size="small" type="success">已生成</el-tag>
            <el-tag v-else size="small" type="info">无</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="download(row)">下载 Excel</el-button>
            <el-button link size="small" @click="viewSummary(row)">查看摘要</el-button>
            <el-button link size="small" @click="openDetailExports(row.report_date)">回灌明细</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.export-view {
  width: 100%;
}
.gen-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.warn-tip { color: var(--color-warn); font-size: var(--text-sm); }
.hint { margin-top: 14px; font-size: var(--text-xs); line-height: 1.7; color: var(--el-text-color-secondary); }
.hint b { color: var(--el-color-primary); }
.note { margin-bottom: 12px; }
.foot-hint { float: left; font-size: var(--text-xs); color: var(--el-text-color-secondary); line-height: 32px; }
.head-row { display: flex; justify-content: space-between; align-items: center; }
.tools { display: flex; gap: 8px; }
.summary-text :deep(textarea) { font-family: Consolas, "Microsoft YaHei", monospace; font-size: var(--text-sm); line-height: 1.6; }
.ok { color: var(--el-color-success); font-size: var(--text-sm); }
.over { color: var(--color-warn); font-size: var(--text-sm); }
</style>
