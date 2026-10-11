import { defineStore } from 'pinia'
import { api } from '../api'
import { todayStr } from '../utils/date'

// 日报看板状态（规划 8.3）
export const useReportStore = defineStore('report', {
  state: () => ({
    reportDate: '',
    platforms: [],
    shops: [],
    data: null,
    loading: false,
    generating: false,
    summary: null,
    history: { items: [], total: 0 },
    metaOptions: { platforms: [], shops: [], categories: [] },
    // 请求序号：快速切换日期/筛选时，慢的旧响应不得覆盖新响应
    _fetchSeq: 0,
    _initDatePromise: null,
  }),
  actions: {
    async initDate() {
      if (this.reportDate) return
      // in-flight 守卫：App 与各视图 onMounted 可能并发调用
      if (this._initDatePromise) return this._initDatePromise
      this._initDatePromise = (async () => {
        // 优先恢复用户上次选择的日期（刷新不丢上下文），首次使用再取最新数据日
        const saved = sessionStorage.getItem('ecom_report_date')
        if (saved) {
          this.reportDate = saved
          return
        }
        try {
          const { date } = await api.latestDate()
          this.reportDate = date || todayStr()
        } catch {
          this.reportDate = todayStr()
        }
      })()
      return this._initDatePromise
    },
    async fetchMeta() {
      // meta 选项变化频率低，会话内缓存一次即可
      if (this.metaOptions.platforms.length || this.metaOptions.categories.length) return
      const [p, s, c] = await Promise.all([
        api.meta.platforms(), api.meta.shops(), api.meta.categories(),
      ])
      this.metaOptions = { platforms: p.items, shops: s.items, categories: c.items }
    },
    async fetchReport() {
      const seq = ++this._fetchSeq
      this.loading = true
      try {
        const data = await api.reports.daily({
          date: this.reportDate,
          platforms: this.platforms.join(','),
          shops: this.shops.join(','),
        })
        if (seq === this._fetchSeq) this.data = data
      } catch (e) {
        if (seq === this._fetchSeq) {
          // 无数据日期必须清空看板：残留上一日期的数据会让用户
          // 把昨天的数字当今天看（日报工具最致命的误读）
          this.data = null
          // NO_DATA_FOR_DATE 是「正常空态」而非错误，由视图渲染空态，不再向上抛
          if (e?.code === 'NO_DATA_FOR_DATE') return
        }
        throw e
      } finally {
        if (seq === this._fetchSeq) this.loading = false
      }
    },
    async generate() {
      this.generating = true
      try {
        // 日报 = 当日全量口径，不随看板筛选走（带 scope 的生成后端不落库）
        return await api.reports.generate({ date: this.reportDate })
      } finally {
        this.generating = false
      }
    },
    async fetchSummary(date) {
      this.summary = await api.reports.summary(date || this.reportDate)
      return this.summary
    },
    async fetchHistory() {
      this.history = await api.reports.history({ page: 1, size: 50 })
    },
  },
})
