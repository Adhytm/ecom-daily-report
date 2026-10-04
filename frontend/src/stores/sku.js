import { defineStore } from 'pinia'
import { api, toastError } from '../api'

// SKU 与映射状态（规划 8.4）
export const useSkuStore = defineStore('sku', {
  state: () => ({
    skus: { items: [], total: 0 },
    mappings: { items: [], total: 0 },
    suggestions: [],
    loading: false,
  }),
  actions: {
    async fetchSkus(params) {
      this.skus = await api.skus.list(params)
    },
    async createSku(body) {
      return api.skus.create(body)
    },
    async updateSku(id, body) {
      return api.skus.update(id, body)
    },
    async removeSku(id) {
      const refs = await api.skus.refs(id)
      if (refs.sales_facts > 0 || refs.mappings > 0) {
        const err = new Error(
          `该 SKU 被 ${refs.sales_facts} 条销售明细、${refs.mappings} 条映射引用，无法删除`)
        err.code = 'SKU_IN_USE'
        throw err
      }
      return api.skus.remove(id)
    },
    async fetchMappings(params) {
      this.mappings = await api.mappings.list(params)
    },
    async fetchSuggestions(platformKey) {
      const data = await api.mappings.suggest({ platform_key: platformKey || undefined, limit: 100 })
      this.suggestions = data.items
      return data
    },
    async adopt(mappingId, skuId) {
      await api.mappings.update(mappingId, { sku_id: skuId })
      this.suggestions = this.suggestions.filter((s) => s.mapping_id !== mappingId)
    },
    async batchMap(items) {
      try {
        return await api.mappings.batch(items)
      } catch (e) {
        toastError(e)
        throw e
      }
    },
  },
})
