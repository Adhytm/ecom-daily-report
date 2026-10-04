<script setup>
// 指标卡：大号当前值 + 环比 + 周同比（带颜色箭头）（规划 8.3 第 3 步）
import { computed } from 'vue'
import { formatMoney, formatRate, formatNum, formatDelta } from '../utils/format'

const props = defineProps({
  label: { type: String, required: true },
  block: { type: Object, default: () => ({}) },
  kind: { type: String, default: 'money' }, // money | rate | num
  tooltip: { type: String, default: '' },
})

const valueText = computed(() => {
  const v = props.block?.value
  if (props.kind === 'money') return formatMoney(v)
  if (props.kind === 'rate') return formatRate(v)
  return formatNum(v)
})

const isPP = computed(() => props.block?.dod_pp !== undefined && props.block?.dod_pp !== null)
const dod = computed(() => formatDelta(props.block?.dod ?? props.block?.dod_pp, isPP.value))
const wow = computed(() => formatDelta(props.block?.wow ?? props.block?.wow_pp, isPP.value))
</script>

<template>
  <el-card shadow="hover" class="metric-card">
    <div class="head">
      <span class="label">{{ label }}</span>
      <el-tooltip v-if="tooltip" :content="tooltip" placement="top">
        <span class="tip">ⓘ</span>
      </el-tooltip>
    </div>
    <div class="value">{{ valueText }}</div>
    <div class="deltas">
      <span class="delta-item">
        环比
        <span :class="dod.cls">{{ dod.cls === 'delta-up' ? '↑ ' : dod.cls === 'delta-down' ? '↓ ' : '' }}{{ dod.text.replace(/^[+-]/, '') }}</span>
      </span>
      <el-divider direction="vertical" />
      <span class="delta-item">
        周同比
        <span :class="wow.cls">{{ wow.cls === 'delta-up' ? '↑ ' : wow.cls === 'delta-down' ? '↓ ' : '' }}{{ wow.text.replace(/^[+-]/, '') }}</span>
      </span>
    </div>
  </el-card>
</template>

<style scoped>
.metric-card {
  transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-med) var(--ease-out);
}
.metric-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}
.metric-card :deep(.el-card__body) { padding: 16px 18px; }
.head { display: flex; align-items: center; gap: 6px; }
.label { color: var(--el-text-color-secondary); font-size: var(--text-sm); font-weight: 500; }
.tip { color: var(--el-text-color-placeholder); cursor: help; font-size: var(--text-xs); }
.value {
  margin-top: 8px;
  font-size: var(--text-2xl);
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}
.deltas {
  margin-top: 8px;
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
}
.delta-item { display: inline-flex; gap: 4px; align-items: center; }
</style>
