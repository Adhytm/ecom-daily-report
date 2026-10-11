import axios from 'axios'
import { ElMessage } from 'element-plus'

// axios 实例：统一响应包裹解包（规划 7：{ok, data} / {ok, error}）
const http = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

http.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'ok' in body) {
      if (body.ok) return body.data
      return Promise.reject(normalizeError(body.error))
    }
    return body
  },
  (err) => {
    const body = err.response?.data
    if (body && body.ok === false && body.error) {
      return Promise.reject(normalizeError(body.error))
    }
    return Promise.reject(normalizeError({ code: 'NETWORK_ERROR', message: err.message }))
  },
)

function normalizeError(error) {
  const e = new Error(error?.message || '请求失败')
  e.code = error?.code || 'INTERNAL_ERROR'
  e.detail = error?.detail
  return e
}

export function toastError(e) {
  ElMessage.error(e?.message || String(e))
}

// 下载：fetch 校验响应后再落盘。裸 <a href> 在失败时会让浏览器直接
// 导航到 JSON 错误页（SPA 状态全丢，且调用方已提示「已开始下载」）。
export async function downloadUrl(url, filenameHint = '') {
  let resp
  try {
    resp = await fetch(url)
  } catch {
    throw normalizeError({ code: 'NETWORK_ERROR', message: '网络异常，下载失败' })
  }
  if (!resp.ok) {
    // 失败时后端返回统一错误包裹 JSON
    let message = `下载失败（HTTP ${resp.status}）`
    let code = 'DOWNLOAD_FAILED'
    try {
      const body = await resp.json()
      if (body && body.ok === false && body.error) {
        message = body.error.message || message
        code = body.error.code || code
      }
    } catch { /* 非 JSON 错误体，用默认文案 */ }
    throw normalizeError({ code, message })
  }
  const blob = await resp.blob()
  // 优先用后端 Content-Disposition 里的文件名（含中文名 RFC 5987 编码）
  let filename = filenameHint
  const cd = resp.headers.get('Content-Disposition') || ''
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i)
  const plain = cd.match(/filename="?([^";]+)"?/i)
  if (star) filename = decodeURIComponent(star[1])
  else if (plain) filename = plain[1]
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = filename || 'download'
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(objUrl), 5000)
}

// ---- 接口封装 ----

export const api = {
  health: () => http.get('/health'),
  latestDate: () => http.get('/reports/latest-date'),
  meta: {
    categories: () => http.get('/meta/categories'),
    platforms: () => http.get('/meta/platforms'),
    shops: () => http.get('/meta/shops'),
  },
  adapters: {
    list: () => http.get('/adapters'),
    get: (key) => http.get(`/adapters/${key}`),
    update: (key, yamlText) => http.put(`/adapters/${key}`, { yaml_text: yamlText }),
    validate: (key, yamlText) => http.post(`/adapters/${key}/validate`, { yaml_text: yamlText }),
    reset: (key) => http.post(`/adapters/${key}/reset`),
  },
  uploads: {
    create: (formData, onProgress) => http.post('/uploads', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onProgress,
    }),
    commit: (id, force = false) => http.post(`/uploads/${id}/commit${force ? '?force=true' : ''}`),
    preview: (id) => http.get(`/uploads/${id}/preview`),
    reparse: (id, platformKey) => http.post(`/uploads/${id}/reparse`, { platform_key: platformKey }),
    remove: (id) => http.delete(`/uploads/${id}`),
    list: (params) => http.get('/uploads', { params }),
    errors: (id, params) => http.get(`/uploads/${id}/errors`, { params }),
  },
  skus: {
    list: (params) => http.get('/skus', { params }),
    create: (body) => http.post('/skus', body),
    update: (id, body) => http.put(`/skus/${id}`, body),
    remove: (id) => http.delete(`/skus/${id}`),
    refs: (id) => http.get(`/skus/${id}/reference-count`),
  },
  mappings: {
    list: (params) => http.get('/sku-mappings', { params }),
    update: (id, body) => http.put(`/sku-mappings/${id}`, body),
    batch: (items) => http.post('/sku-mappings/batch', { items }),
    suggest: (body) => http.post('/sku-mappings/suggest', body),
  },
  reports: {
    daily: (params) => http.get('/reports/daily', { params }),
    generate: (body) => http.post('/reports/daily/generate', body),
    summary: (date) => http.get(`/reports/daily/${date}/summary`),
    history: (params) => http.get('/reports', { params }),
    exportUrl: (date) => `/api/reports/daily/${date}/export`,
    // 可回灌明细导出：导出文件用平台原始列名，可直接拖回上传页重新入库
    detailExports: (date) => http.get(`/reports/daily/${date}/detail-exports`),
    detailExportUrl: (date, platformKey) =>
      `/api/reports/daily/${date}/detail-exports/${platformKey}`,
  },
  rules: {
    list: () => http.get('/anomaly-rules'),
    update: (id, body) => http.put(`/anomaly-rules/${id}`, body),
  },
  weekly: {
    get: (date) => http.get('/reports/weekly', { params: { date } }),
    summary: (date) => http.get('/reports/weekly/summary', { params: { date } }),
    exportUrl: (date) => `/api/reports/weekly/export?date=${date}`,
  },
  ai: {
    // 本地大模型推理常超过默认 120s 超时，单独放宽到 10 分钟
    diagnose: (body) => http.post('/ai/diagnose', body, { timeout: 600000 }),
  },
  mock: {
    generate: (body) => http.post('/mock-data/generate', body),
  },
}

export default http
