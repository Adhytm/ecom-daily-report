<script setup>
// 异常预警列表：按 severity 排序，critical 红点、warning 黄点（规划 8.3 第 7 步）
import { computed } from 'vue'

const props = defineProps({
  anomalies: { type: Array, default: () => [] },
})

const sorted = computed(() => {
  return [...props.anomalies].sort(
    (a, b) => (a.severity === 'critical' ? 0 : 1) - (b.severity === 'critical' ? 0 : 1),
  )
})

const scopeLabel = { overall: '整体', platform: '平台', shop: '店铺', category: '类目', sku: 'SKU' }
</script>

<template>
  <div class="anomaly-list">
    <el-empty
      v-if="!sorted.length"
      description="本日无异常预警"
      :image-size="60"
    />
    <div v-for="(a, i) in sorted" :key="i" class="item" :class="a.severity">
      <span class="dot" :class="a.severity"></span>
      <div class="body">
        <div class="line1">
          <el-tag :type="a.severity === 'critical' ? 'danger' : 'warning'" size="small">
            {{ a.severity === 'critical' ? '严重' : '警告' }}
          </el-tag>
          <span class="subject">{{ a.subject }}</span>
          <span class="scope">{{ scopeLabel[a.scope] || a.scope }}</span>
        </div>
        <div class="message">{{ a.message }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.anomaly-list { max-height: 480px; overflow: auto; }
.item {
  display: flex; gap: 8px; padding: 8px 10px; border-radius: 6px;
  align-items: flex-start; margin-bottom: 6px;
  transition: background-color 0.2s;
}
.item.critical { background: var(--el-color-danger-light-9, #fdf0f0); }
.item.warning { background: var(--el-color-warning-light-9, #fdf6ec); }
:global(html.dark) .item.critical { background: rgba(245, 108, 108, 0.15); }
:global(html.dark) .item.warning { background: rgba(230, 162, 60, 0.15); }
.dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
.dot.critical { background: var(--el-color-danger, #f56c6c); }
.dot.warning { background: var(--el-color-warning, #e6a23c); }
.body { min-width: 0; }
.line1 { display: flex; gap: 6px; align-items: center; }
.subject { font-size: 13px; font-weight: 600; color: var(--el-text-color-primary, #303133); }
.scope { font-size: 12px; color: var(--el-text-color-secondary, #909399); }
.message { margin-top: 2px; font-size: 12px; color: var(--el-text-color-regular, #606266); word-break: break-all; }
</style>
