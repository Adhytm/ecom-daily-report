import { defineStore } from 'pinia'
import { api } from '../api'

// 适配器管理状态（规划 8.5）
export const useAdapterStore = defineStore('adapter', {
  state: () => ({
    list: [],
    current: null,   // 详情（yaml_text + structured）
    validating: false,
    saving: false,
    validateResult: null,
  }),
  actions: {
    async fetchList() {
      const data = await api.adapters.list()
      this.list = data.items
    },
    async select(key) {
      this.current = await api.adapters.get(key)
      this.validateResult = null
      return this.current
    },
    async validate(yamlText) {
      this.validating = true
      try {
        this.validateResult = await api.adapters.validate(this.current.platform_key, yamlText)
        return this.validateResult
      } finally {
        this.validating = false
      }
    },
    async save(yamlText) {
      this.saving = true
      try {
        return await api.adapters.update(this.current.platform_key, yamlText)
      } finally {
        this.saving = false
      }
    },
    async reset() {
      await api.adapters.reset(this.current.platform_key)
      await this.select(this.current.platform_key)
      await this.fetchList()
    },
  },
})
