// 日期工具
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export function todayStr() {
  const d = new Date()
  return fmtDate(d)
}

export function fmtDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function addDays(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00`)
  d.setDate(d.getDate() + days)
  return fmtDate(d)
}

export function weekdayName(dateStr) {
  if (!dateStr) return ''
  const d = new Date(`${dateStr}T00:00:00`)
  return WEEKDAYS[d.getDay()]
}

export function nowTime() {
  const d = new Date()
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}
