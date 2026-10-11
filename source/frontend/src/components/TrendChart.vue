<script setup>
// 近 30 天趋势图：双 Y 轴折线（左轴 GMV/实际成交，右轴退款率），
// 大促日（GMV >= 中位数 × 2）用 markLine 标注（规划 8.3 第 4 步）
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { formatMoney, formatRate } from '../utils/format'
import { isDark } from '../utils/theme'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer])

const props = defineProps({
  dates: { type: Array, default: () => [] },
  series: { type: Object, default: () => ({}) },
})

const el = ref(null)
let chart = null

function promoDays(dates, gmv) {
  // 疑似大促日：GMV >= 有效中位数 × 2
  const vals = (gmv || []).filter((v) => v !== null && v !== undefined)
  if (vals.length < 4) return []
  const sorted = [...vals].sort((a, b) => a - b)
  const mid = sorted[Math.floor(sorted.length / 2)]
  if (!mid) return []
  const days = []
  dates.forEach((d, i) => {
    if (gmv[i] !== null && gmv[i] >= mid * 2) days.push(d)
  })
  return days
}

function render() {
  if (!el.value) return
  const { dates, series } = props
  const promos = promoDays(dates, series.gmv)
  chart = chart || echarts.init(el.value)

  const dark = isDark.value
  const textColor = dark ? '#E5EAF3' : '#606266'
  const subTextColor = dark ? '#A3A6AD' : '#909399'
  const lineColor = dark ? 'rgba(255, 255, 255, 0.12)' : '#E4E7ED'
  const gmvColor = dark ? '#5b9bd5' : '#1f4e79'
  const netColor = dark ? '#4ecb94' : '#00875a'
  const refundColor = dark ? '#ff7a59' : '#d4380d'
  const promoColor = dark ? '#e8b339' : '#d48806'

  chart.setOption(
    {
      tooltip: {
        trigger: 'axis',
        backgroundColor: dark ? 'rgba(20, 23, 28, 0.95)' : 'rgba(255, 255, 255, 0.95)',
        borderColor: dark ? 'rgba(255, 255, 255, 0.12)' : '#E4E7ED',
        textStyle: { color: textColor },
        formatter: (params) => {
          let res = `<b>${params[0]?.axisValueLabel || ''}</b><br/>`
          for (const p of params) {
            let val = '—'
            if (p.value !== null && p.value !== undefined) {
              if (p.seriesName === '退款率') {
                val = formatRate(p.value)
              } else {
                val = formatMoney(p.value, { wan: false })
              }
            }
            res += `${p.marker} ${p.seriesName}: <b>${val}</b><br/>`
          }
          return res
        },
      },
      legend: {
        data: ['GMV', '实际成交', '退款率'],
        textStyle: { color: textColor },
      },
      grid: { left: 70, right: 60, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: lineColor } },
        axisLabel: { color: subTextColor },
      },
      yAxis: [
        {
          type: 'value',
          name: '金额',
          nameTextStyle: { color: subTextColor },
          axisLabel: { color: subTextColor, formatter: (v) => formatMoney(v, { wan: true }) },
          splitLine: { lineStyle: { color: lineColor } },
        },
        {
          type: 'value',
          name: '退款率',
          nameTextStyle: { color: subTextColor },
          axisLabel: { color: subTextColor, formatter: (v) => `${(v * 100).toFixed(0)}%` },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: 'GMV', type: 'line', data: series.gmv, showSymbol: false, smooth: true,
          lineStyle: { width: 2.5, color: gmvColor }, itemStyle: { color: gmvColor },
          markLine: promos.length
            ? {
                symbol: 'none',
                silent: true,
                lineStyle: { type: 'dashed', color: promoColor },
                label: { formatter: '疑似大促', color: promoColor },
                data: promos.map((d) => ({ xAxis: d })),
              }
            : { data: [] },
        },
        {
          name: '实际成交', type: 'line', data: series.net_amount,
          showSymbol: false, smooth: true,
          lineStyle: { width: 2, color: netColor }, itemStyle: { color: netColor },
        },
        {
          name: '退款率', type: 'line', yAxisIndex: 1, data: series.refund_rate,
          showSymbol: false, smooth: true,
          lineStyle: { width: 1.5, type: 'dashed', color: refundColor },
          itemStyle: { color: refundColor },
        },
      ],
    },
    { notMerge: true },
  )
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
watch(() => [props.dates, props.series], render, { deep: true })
watch(isDark, render)
</script>

<template>
  <div ref="el" class="trend-chart"></div>
</template>

<style scoped>
.trend-chart { width: 100%; height: 320px; }
</style>
