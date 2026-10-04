<script setup>
// 适配器管理（规划 8.5）：结构化只读展示 + YAML 编辑器（textarea，不引入 monaco）
import { onMounted, ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAdapterStore } from '../stores/adapter'
import { toastError } from '../api'

const store = useAdapterStore()
const yamlText = ref('')
const savingEnabled = ref(false)
const viewModeTab = ref('overview')

onMounted(async () => {
  try {
    await store.fetchList()
    if (store.list.length) await select(store.list[0].platform_key)
  } catch (e) {
    toastError(e)
  }
})

async function select(key) {
  if (key === store.current?.platform_key) return
  // 未保存修改切换平台即丢，必须先确认
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前适配器有未保存的修改，切换平台将丢弃这些修改。确定继续？',
        '放弃未保存的修改？',
        { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' },
      )
    } catch {
      return
    }
  }
  try {
    await store.select(key)
    yamlText.value = store.current.yaml_text
    savingEnabled.value = false
  } catch (e) {
    toastError(e)
  }
}

const sem = computed(() => store.current?.structured?.metric_semantics || {})
const dirty = computed(() => store.current && yamlText.value !== store.current.yaml_text)

// 校验通过后又改了内容：保存按钮必须重新失效，
// 否则保存的是未校验文本，被后端拒绝时用户困惑「刚校验过为什么不能存」
watch(yamlText, () => {
  savingEnabled.value = false
})

async function doValidate() {
  try {
    const result = await store.validate(yamlText.value)
    if (result.valid) {
      ElMessage.success('校验通过')
      if (result.warnings?.length) {
        ElMessage.warning(`有 ${result.warnings.length} 条提示：${result.warnings[0]}`)
      }
      savingEnabled.value = true
    } else {
      savingEnabled.value = false
      ElMessage.error(result.errors[0])
    }
  } catch (e) {
    toastError(e)
    savingEnabled.value = false
  }
}

async function doSave() {
  try {
    await store.save(yamlText.value)
    ElMessage.success('已保存，后续上传/提交将使用新配置')
    await store.fetchList()
    await store.select(store.current.platform_key)
    yamlText.value = store.current.yaml_text
    savingEnabled.value = false
  } catch (e) {
    toastError(e)
  }
}

async function doReset() {
  try {
    await ElMessageBox.confirm(
      `确定将「${store.current.display_name}」恢复为内置默认配置？自定义修改将丢失。`,
      '恢复默认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await store.reset()
    yamlText.value = store.current.yaml_text
    savingEnabled.value = false
    ElMessage.success('已恢复内置默认')
  } catch (e) {
    toastError(e)
  }
}

function basisText(v) {
  return { paid: '支付口径', ordered: '下单口径', shipped: '发货口径' }[v] || v
}
function refundText(v) {
  return { separate: '退款单独字段，需扣除', deducted: '已扣除退款', none: '无退款数据' }[v] || v
}
</script>

<template>
  <div class="adapter-view page-container">
    <div class="layout">
      <!-- 左：平台列表 -->
      <el-card shadow="never" class="side">
        <template #header>平台适配器</template>
        <div
          v-for="item in store.list"
          :key="item.platform_key"
          class="side-item"
          :class="{ active: store.current?.platform_key === item.platform_key }"
          @click="select(item.platform_key)"
        >
          <div class="s-name">{{ item.display_name }}</div>
          <div class="s-meta">
            <el-tag v-if="item.is_builtin" size="small" type="info">内置</el-tag>
            <span class="s-key">{{ item.platform_key }} · v{{ item.version }}</span>
          </div>
        </div>
      </el-card>

      <!-- 右：结构化展示 / YAML 编辑切换 -->
      <div class="detail" v-if="store.current">
        <el-card shadow="never">
          <template #header>
            <div class="head-row">
              <div class="title-box">
                <span class="platform-title">{{ store.current.display_name }}</span>
                <el-tag size="small" :type="store.current.source === 'override' ? 'warning' : 'info'" effect="plain">
                  {{ store.current.source === 'override' ? '用户自定义覆盖' : '内置 baseline' }}
                </el-tag>
              </div>
            </div>
          </template>

          <el-tabs v-model="viewModeTab">
            <!-- Tab 1: 业务口径与字段映射对照 -->
            <el-tab-pane label="📊 业务口径与列映射对照" name="overview">
              <!-- 口径声明卡片 -->
              <div class="sem-cards">
                <div class="sem-card"><span class="k">GMV 口径</span><span class="v">{{ basisText(sem.gmv_basis) }}</span></div>
                <div class="sem-card"><span class="k">含运费</span><span class="v">{{ sem.gmv_includes_shipping ? '是（与其他平台不可直接比较）' : '否' }}</span></div>
                <div class="sem-card"><span class="k">含税</span><span class="v">{{ sem.gmv_includes_tax ? '是' : '否' }}</span></div>
                <div class="sem-card"><span class="k">退款处理</span><span class="v">{{ refundText(sem.refund_handling) }}</span></div>
                <div class="sem-card"><span class="k">实际成交公式</span><span class="v mono">{{ sem.net_amount_formula }}</span></div>
                <div class="sem-card"><span class="k">数据粒度</span><span class="v">{{ sem.grain }}</span></div>
              </div>

              <!-- 列映射对照 -->
              <h4>列映射对照（源表头 → 统一标准字段）</h4>
              <el-table :data="Object.entries(store.current.structured.column_map).map(([s, d]) => ({ s, d }))" size="small" border stripe max-height="280">
                <el-table-column prop="s" label="平台源表头" min-width="160" />
                <el-table-column label="→" width="50" align="center">→</el-table-column>
                <el-table-column prop="d" label="系统标准字段" min-width="160" />
              </el-table>

              <h4 v-if="Object.keys(store.current.structured.column_aliases || {}).length">列别名容错清单</h4>
              <el-table
                v-if="Object.keys(store.current.structured.column_aliases || {}).length"
                :data="Object.entries(store.current.structured.column_aliases).map(([k, v]) => ({ k, v: v.join('、') }))"
                size="small"
                border
                stripe
              >
                <el-table-column prop="k" label="标准指标" width="180" />
                <el-table-column prop="v" label="兼容的别名源表头" />
              </el-table>

              <h4>行过滤规则</h4>
              <el-table :data="store.current.structured.row_filters" size="small" border stripe>
                <el-table-column prop="column" label="过滤字段" width="180" />
                <el-table-column prop="op" label="条件" width="120" />
                <el-table-column label="参数">
                  <template #default="{ row }">
                    {{ row.values ? row.values.join('、') : (row.value ?? '—') }}
                  </template>
                </el-table-column>
              </el-table>

              <h4>去重策略</h4>
              <p class="dedup">
                {{ store.current.structured.dedup.enabled
                  ? `按 ${store.current.structured.dedup.keys.join(' + ')} 分组，策略：${store.current.structured.dedup.strategy}`
                  : '未启用' }}
              </p>
            </el-tab-pane>

            <!-- Tab 2: YAML 高级配置代码 -->
            <el-tab-pane label="⚙️ YAML 规则配置与调试" name="yaml">
              <div class="editor-toolbar">
                <span class="editor-tip">支持直接修改 YAML 规则，修改后须先点击「语法校验」：</span>
                <div class="tools">
                  <el-button size="small" :loading="store.validating" @click="doValidate">语法校验</el-button>
                  <el-button size="small" type="primary" :disabled="!savingEnabled" :loading="store.saving" @click="doSave">
                    保存生效
                  </el-button>
                  <el-button size="small" type="danger" plain @click="doReset">恢复内置默认</el-button>
                </div>
              </div>

              <el-alert
                v-if="store.validateResult && !store.validateResult.valid"
                type="error"
                :closable="false"
                class="val-result"
              >
                <div v-for="(e, i) in store.validateResult.errors" :key="i">· {{ e }}</div>
              </el-alert>
              <el-alert
                v-else-if="store.validateResult?.valid && store.validateResult.warnings?.length"
                type="warning"
                :closable="false"
                class="val-result"
              >
                <div v-for="(w, i) in store.validateResult.warnings" :key="i">· {{ w }}</div>
              </el-alert>
              <el-input
                v-model="yamlText"
                type="textarea"
                :rows="22"
                class="yaml-editor"
                spellcheck="false"
              />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.adapter-view {
  width: 100%;
}
.layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 16px;
}
@media (max-width: 960px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
.side {
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}
.side-item {
  padding: 12px 14px;
  border-radius: var(--radius-md);
  cursor: pointer;
  margin-bottom: 6px;
  border: 1px solid transparent;
  transition: all var(--dur-fast) var(--ease-out);
}
.side-item:hover {
  background: var(--el-fill-color-light);
}
.side-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
}
:global(html.dark) .side-item.active {
  background: rgba(91, 155, 213, 0.15);
  border-color: var(--el-color-primary);
}
.s-name {
  font-weight: 600;
  font-size: var(--text-md);
  color: var(--el-text-color-primary);
}
.s-meta {
  margin-top: 4px;
  display: flex;
  gap: 6px;
  align-items: center;
}
.s-key {
  color: var(--el-text-color-secondary);
  font-size: var(--text-xs);
}
.head-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.title-box {
  display: flex;
  align-items: center;
  gap: 10px;
}
.platform-title {
  font-weight: 700;
  font-size: var(--text-lg);
  color: var(--el-text-color-primary);
}
.editor-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  background: var(--el-fill-color-light);
  padding: 10px 14px;
  border-radius: var(--radius-md);
  flex-wrap: wrap;
  gap: 8px;
}
.editor-tip {
  font-size: var(--text-sm);
  color: var(--el-text-color-secondary);
}
.tools {
  display: flex;
  gap: 8px;
}
.sem-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}
@media (max-width: 768px) {
  .sem-cards {
    grid-template-columns: repeat(2, 1fr);
  }
}
.sem-card {
  background: var(--el-fill-color-light);
  border-radius: var(--radius-md);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sem-card .k {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}
.sem-card .v {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.v.mono {
  font-family: Consolas, monospace;
}
h4 {
  margin: 16px 0 10px;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-color-primary);
}
.dedup {
  color: var(--el-text-color-regular);
  font-size: var(--text-sm);
  margin: 0;
}
.yaml-editor :deep(textarea) {
  font-family: Consolas, "Courier New", monospace;
  font-size: var(--text-sm);
  line-height: 1.6;
  border-radius: var(--radius-md);
}
.val-result {
  margin-bottom: 10px;
  border-radius: var(--radius-md);
}
</style>
