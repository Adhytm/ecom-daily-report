import { defineStore } from 'pinia'
import { api, toastError } from '../api'

// 上传状态（规划 8.2）
// items：解析成功待提交的卡片；failedItems：识别/解析失败、可改选平台复活的卡片。
// 两份清单都以服务端 Upload 记录为准（parsed / failed），刷新后可完整恢复，
// 不会出现「后端已登记、前端忘了」的孤儿状态。
export const useUploadStore = defineStore('upload', {
  state: () => ({
    items: [],
    failedItems: [],
    uploading: false,
    committing: false,
    loadingPending: false,
  }),
  actions: {
    // 从后端恢复待处理清单：parsed 记录经只读 preview 端点重算出完整卡片；
    // failed 记录渲染为「识别失败」卡片，用户选平台 reparse 即可复活
    async loadPending() {
      this.loadingPending = true
      try {
        const [parsedRes, failedRes] = await Promise.all([
          api.uploads.list({ status: 'parsed', size: 100 }),
          api.uploads.list({ status: 'failed', size: 100 }),
        ])
        const parsed = []
        const unrecoverable = []
        // 并发恢复预览：串行 await 在文件多（上限 100）时刷新恢复极慢
        const previews = await Promise.all(
          (parsedRes.items || []).map(async (row) => {
            try {
              return { ok: true, data: await api.uploads.preview(row.id) }
            } catch (e) {
              return { ok: false, row, error: e }
            }
          }),
        )
        for (const p of previews) {
          if (p.ok) parsed.push(p.data)
          else {
            // 原始文件丢失等无法恢复的，降级成失败卡片，引导移除后重传
            unrecoverable.push({
              upload_id: p.row.id,
              filename: p.row.filename,
              error_message: p.error?.message || '无法恢复，请移除后重新上传',
              unrecoverable: true,
            })
          }
        }
        this.items = parsed
        this.failedItems = [
          ...unrecoverable,
          ...(failedRes.items || []).map((r) => ({
            upload_id: r.id,
            filename: r.filename,
            error_message: r.error_message || '解析失败',
          })),
        ]
      } finally {
        this.loadingPending = false
      }
    },
    async uploadFiles(fileList) {
      this.uploading = true
      try {
        const fd = new FormData()
        for (const f of fileList) fd.append('files', f)
        let data = null
        let rejected = []
        try {
          data = await api.uploads.create(fd)
          rejected = data.errors || []
        } catch (e) {
          // 全部失败（400）：detail.files 仍是逐文件错误；识别/解析失败的记录
          // 后端已登记（含原始文件），统一走 loadPending 恢复为失败卡片
          rejected = e.detail?.files || []
          if (!rejected.length) throw e
        }
        await this.loadPending()
        // 只有「未进入解析就被拒」（类型不支持 / 超限，无后端记录）的需要逐条提示
        const noRecord = rejected.filter((x) => !x.error?.detail?.upload_id)
        if (noRecord.length) {
          toastError(new Error(
            noRecord.map((x) => `${x.filename}：${x.error?.message || '解析失败'}`).join('；'),
          ))
        }
        return data
      } finally {
        this.uploading = false
      }
    },
    async reparse(item, platformKey) {
      const data = await api.uploads.reparse(item.upload_id, platformKey)
      // 卡片被新数据整体替换时保留用户的「确认重复入库」勾选
      data._forceCommit = !!item._forceCommit
      const idx = this.items.findIndex((x) => x.upload_id === item.upload_id)
      if (idx >= 0) this.items.splice(idx, 1, data)
      else this.items.push(data)
      this.failedItems = this.failedItems.filter((x) => x.upload_id !== item.upload_id)
      return data
    },
    async removeItem(item) {
      await api.uploads.remove(item.upload_id)
      this.items = this.items.filter((x) => x.upload_id !== item.upload_id)
      this.failedItems = this.failedItems.filter((x) => x.upload_id !== item.upload_id)
    },
    // 逐个提交：单文件失败不中断其余文件；重复文件（duplicate_of）默认跳过，
    // 需用户在卡片上勾选「确认重复入库」（_forceCommit）才携带 force=true。
    // 返回 { results, failures, skippedDuplicates }，调用方负责汇总提示；
    // 成功的卡片移除，失败/跳过的保留在清单中
    async commitAll() {
      this.committing = true
      const results = []
      const failures = []
      let skippedDuplicates = 0
      try {
        for (const item of [...this.items]) {
          if (!(item.stats?.valid > 0)) continue
          if (item.duplicate_of && !item._forceCommit) {
            skippedDuplicates += 1
            continue
          }
          try {
            results.push(await api.uploads.commit(item.upload_id, !!item._forceCommit))
            this.items = this.items.filter((x) => x.upload_id !== item.upload_id)
          } catch (e) {
            item._commitError = e?.message || '入库失败'
            failures.push({ filename: item.filename, error: e })
          }
        }
        return { results, failures, skippedDuplicates }
      } finally {
        this.committing = false
      }
    },
    async loadErrors(uploadId, params) {
      return api.uploads.errors(uploadId, params)
    },
  },
})
