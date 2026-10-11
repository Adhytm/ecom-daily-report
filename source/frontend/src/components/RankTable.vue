<script setup>
// 通用排名表：平台 / 店铺 / 类目 / SKU 维度复用（规划 8.3 第 5、6 步）
import { computed } from 'vue'
import { formatMoney, formatRate, formatNum, formatDelta, fmtOrDash } from '../utils/format'

const props = defineProps({
  type: { type: String, required: true }, // platform | shop | category | sku-mapped | sku-unmapped
  rows: { type: Array, default: () => [] },
})

const columns = computed(() => {
  if (props.type === 'sku-unmapped') {
    return [
      { key: 'platform', label: '平台' },
      { key: 'code', label: '平台商品编码' },
      { key: 'name', label: '商品名称', wide: true },
      { key: 'gmv', label: 'GMV', kind: 'money' },
      { key: 'net', label: '实际成交', kind: 'money' },
      { key: 'qty', label: '件数', kind: 'num' },
      { key: 'dod', label: 'GMV环比', kind: 'delta' },
    ]
  }
  if (props.type === 'sku-mapped') {
    return [
      { key: 'code', label: 'SKU' },
      { key: 'name', label: '商品名', wide: true },
      { key: 'category', label: '类目' },
      { key: 'platforms', label: '平台' },
      { key: 'gmv', label: 'GMV', kind: 'money' },
      { key: 'net', label: '实际成交', kind: 'money' },
      { key: 'qty', label: '件数', kind: 'num' },
      { key: 'refund_rate', label: '退款率', kind: 'rate' },
      { key: 'gross', label: '毛利', kind: 'money' },
      { key: 'dod', label: 'GMV环比', kind: 'delta' },
      { key: 'wow', label: 'GMV周同比', kind: 'delta' },
    ]
  }
  if (props.type === 'category') {
    return [
      { key: 'name', label: '类目' },
      { key: 'gmv', label: 'GMV', kind: 'money' },
      { key: 'share', label: '占比', kind: 'rate' },
      { key: 'net', label: '实际成交', kind: 'money' },
      { key: 'qty', label: '件数', kind: 'num' },
      { key: 'dod', label: 'GMV环比', kind: 'delta' },
      { key: 'wow', label: 'GMV周同比', kind: 'delta' },
    ]
  }
  // platform | shop
  return [
    { key: 'name', label: props.type === 'platform' ? '平台' : '平台 · 店铺', wide: true },
    { key: 'gmv', label: 'GMV', kind: 'money' },
    { key: 'share', label: '占比', kind: 'rate' },
    { key: 'net', label: '实际成交', kind: 'money' },
    { key: 'refund', label: '退款额', kind: 'money' },
    { key: 'refund_rate', label: '退款率', kind: 'rate' },
    { key: 'qty', label: '件数', kind: 'num' },
    { key: 'buyers', label: '买家数', kind: 'num' },
    { key: 'visitors', label: '访客', kind: 'num' },
    { key: 'cvr', label: '转化率', kind: 'rate' },
    { key: 'aov', label: '客单价', kind: 'money' },
    { key: 'ad_cost', label: '推广费', kind: 'money' },
    { key: 'roi', label: 'ROI', kind: 'num' },
    { key: 'gross', label: '毛利', kind: 'money' },
    { key: 'dod', label: 'GMV环比', kind: 'delta' },
    { key: 'wow', label: 'GMV周同比', kind: 'delta' },
  ]
})

const tableRows = computed(() => {
  return props.rows.map((r) => {
    const m = r.metrics || {}
    const row = {
      name: fmtOrDash(r.name || r.product_name),
      code: fmtOrDash(r.sku_code || r.platform_product_code),
      category: fmtOrDash(r.category),
      platforms: fmtOrDash(r.platforms),
      platform: fmtOrDash(r.platform_key || ''),
      gmv: m.gmv?.value,
      net: m.net_amount?.value,
      refund: m.refund_amount?.value,
      refund_rate: m.refund_rate?.value,
      qty: m.paid_qty?.value,
      buyers: m.buyers?.value,
      visitors: m.visitors?.value,
      cvr: m.conversion_rate?.value,
      aov: m.avg_order_value?.value,
      ad_cost: m.ad_cost?.value,
      roi: m.roi?.value,
      gross: m.gross_profit?.value,
      share: r.share,
      dod: m.gmv?.dod,
      wow: m.gmv?.wow,
    }
    return row
  })
})

function cellText(row, col) {
  const v = row[col.key]
  if (col.kind === 'money') return formatMoney(v)
  if (col.kind === 'rate') return formatRate(v)
  if (col.kind === 'num') return formatNum(v)
  if (col.kind === 'delta') return formatDelta(v).text
  return v ?? '—'
}

function cellClass(row, col) {
  if (col.kind === 'delta') return formatDelta(row[col.key]).cls
  return ''
}
</script>

<template>
  <el-table :data="tableRows" size="small" border stripe max-height="480">
    <el-table-column
      v-for="c in columns"
      :key="c.key"
      :prop="c.key"
      :label="c.label"
      :min-width="c.wide ? 220 : 110"
      show-overflow-tooltip
    >
      <template #default="{ row }">
        <span :class="cellClass(row, c)">{{ cellText(row, c) }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
/* 涨跌色由全局设计系统提供（.delta-up/.delta-down/.delta-none） */
</style>
