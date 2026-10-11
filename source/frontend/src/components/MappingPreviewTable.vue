<script setup>
// 归一化结果预览表（规划 8.2 第 3 步：前 20 行归一化数据）
import { computed } from 'vue'
import { fmtOrDash } from '../utils/format'

const props = defineProps({
  rows: { type: Array, default: () => [] },
})

const columns = [
  { key: 'stat_date', label: '统计日期' },
  { key: 'platform_key', label: '平台' },
  { key: 'shop_name', label: '店铺' },
  { key: 'platform_product_code', label: '商品编码' },
  { key: 'product_name', label: '商品名称' },
  { key: 'category', label: '类目' },
  { key: 'paid_qty', label: '件数' },
  { key: 'gmv', label: 'GMV' },
  { key: 'refund_amount', label: '退款额' },
  { key: 'net_amount', label: '实际成交' },
  { key: 'visitors', label: '访客' },
  { key: 'buyers', label: '买家' },
  { key: 'ad_cost', label: '推广费' },
]

const tableRows = computed(() =>
  props.rows.map((r) => {
    const out = {}
    for (const c of columns) out[c.key] = fmtOrDash(r[c.key])
    return out
  }),
)
</script>

<template>
  <el-table :data="tableRows" size="small" border stripe max-height="420">
    <el-table-column
      v-for="c in columns"
      :key="c.key"
      :prop="c.key"
      :label="c.label"
      :min-width="c.key === 'product_name' ? 180 : 100"
      show-overflow-tooltip
    />
  </el-table>
</template>
