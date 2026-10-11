// 金额 / 比率 / 变化率格式化（规划 8.1）
export function formatMoney(v, { wan = true } = {}) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  if (wan && Math.abs(n) >= 10000) return `¥${(n / 10000).toFixed(2)}万`
  return `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function formatRate(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}

export function formatNum(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return Number.isInteger(n) ? n.toLocaleString('zh-CN') : n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

// 变化率：+12.3% / -4.5%；isPP 时 +1.2pp；返回 { text, cls }
export function formatDelta(v, isPP = false) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return { text: '—', cls: 'delta-none' }
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  const text = isPP ? `${sign}${n.toFixed(1)}pp` : `${sign}${(n * 100).toFixed(1)}%`
  const cls = n > 0 ? 'delta-up' : n < 0 ? 'delta-down' : 'delta-none'
  return { text, cls }
}

export function fmtOrDash(v) {
  if (v === null || v === undefined || v === '') return '—'
  return v
}
