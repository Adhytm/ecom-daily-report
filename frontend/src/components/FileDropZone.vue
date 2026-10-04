<script setup>
// 文件拖拽上传区（规划 8.2 第 1 步：拖拽 + 点击，多选，限 .csv/.xlsx/.xls，单文件 <= 50MB）
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  // 解析/上传中锁定：防止并发上传互相覆盖待处理清单
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['files'])
const dragging = ref(false)
const inputRef = ref(null)

const ALLOWED = ['.csv', '.xlsx', '.xlsm', '.xls', '.tsv', '.txt']
const MAX_MB = 50

function validateAndEmit(fileList) {
  if (props.disabled) return
  const ok = []
  for (const f of fileList) {
    const lower = f.name.toLowerCase()
    if (!ALLOWED.some((ext) => lower.endsWith(ext))) {
      ElMessage.warning(`不支持的文件类型：${f.name}（支持 ${ALLOWED.map((e) => e.slice(1).toUpperCase()).join('/')}）`)
      continue
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      ElMessage.warning(`文件超过 ${MAX_MB}MB 限制：${f.name}`)
      continue
    }
    ok.push(f)
  }
  if (ok.length) emit('files', ok)
}

function onDrop(e) {
  dragging.value = false
  if (props.disabled) return
  validateAndEmit(Array.from(e.dataTransfer.files || []))
}

function onPick(e) {
  validateAndEmit(Array.from(e.target.files || []))
  e.target.value = ''
}

function onZoneClick() {
  if (props.disabled) return
  inputRef.value?.click()
}
</script>

<template>
  <div
    class="drop-zone"
    :class="{ dragging, disabled }"
    @dragover.prevent="!disabled && (dragging = true)"
    @dragleave="dragging = false"
    @drop.prevent="onDrop"
    @click="onZoneClick"
  >
    <input
      ref="inputRef"
      type="file"
      multiple
      accept=".csv,.xlsx,.xlsm,.xls,.tsv,.txt"
      style="display: none"
      @change="onPick"
    />
    <div class="inner">
      <div class="icon">☁</div>
      <div class="main-text">{{ disabled ? '正在解析文件，请稍候…' : '拖拽平台导出文件到此处，或点击选择文件' }}</div>
      <div class="sub-text">支持淘宝/天猫、抖店、拼多多、京东后台导出的 CSV / XLSX / XLSM / XLS / TSV，可多选，单文件 ≤ 50MB</div>
    </div>
  </div>
</template>

<style scoped>
.drop-zone {
  border: 2px dashed var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color);
  cursor: pointer;
  transition: all 0.2s;
}
.drop-zone:hover, .drop-zone.dragging {
  border-color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}
.drop-zone.disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.drop-zone.disabled:hover {
  border-color: var(--el-border-color);
  background: var(--el-bg-color);
}
.inner { text-align: center; padding: 36px 16px; }
.icon { font-size: 40px; color: var(--el-color-primary); }
.main-text { margin-top: 8px; font-size: 15px; color: var(--el-text-color-primary); }
.sub-text { margin-top: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
