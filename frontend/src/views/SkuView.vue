<script setup>
// SKU 与平台映射工作台 —— 现代化商业办公渐进式架构
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Link,
  Check,
  Search,
  Plus,
  Edit,
  Delete,
  MagicStick,
  Files,
  CircleCheck,
  Collection,
  Warning,
} from '@element-plus/icons-vue'
import { useSkuStore } from '../stores/sku'
import { useAdapterStore } from '../stores/adapter'
import { api, toastError } from '../api'
import { fmtOrDash } from '../utils/format'

const skuStore = useSkuStore()
const adapterStore = useAdapterStore()
const route = useRoute()

// 业务主视图 Tabs：unmapped (待映射待办) / suggest (智能建议) / confirmed (已映射档案) / skus (内部基础品库)
const activeTab = ref(route.query.tab || 'unmapped')

const skuQuery = reactive({ q: '', category: '', page: 1, size: 20 })
const mapQuery = reactive({ platform_key: '', q: '', page: 1, size: 20 })
const categories = ref([])
const platformList = ref([])

// 快速关联单项弹窗
const singleMapVisible = ref(false)
const currentSingleItem = ref(null)
const singleSelectedSkuId = ref(null)
const singleSubmitting = ref(false)

// 批量关联选中的项目
const selectedMappings = ref([])
const batchSkuId = ref(null)
const batchSubmitting = ref(false)

// 内部 SKU 备选池（支持全量动态远程检索）
const skuSearchOptions = ref([])
const skuSearchLoading = ref(false)

// 新增/编辑内部 SKU 抽屉
const skuFormVisible = ref(false)
const skuForm = reactive({
  id: null,
  sku_code: '',
  name: '',
  category: '',
  brand: '',
  cost_price: null,
  status: 'active',
})

const matchTypeLabel = {
  unmapped: { text: '未映射', type: 'info' },
  fuzzy: { text: '模糊建议', type: 'warning' },
  exact: { text: '精确匹配', type: 'success' },
  manual: { text: '手工确认', type: 'primary' },
}

onMounted(async () => {
  // 首屏四路请求各自兜底：任何一路失败都不能让整页静默崩掉
  const results = await Promise.allSettled([
    searchSkuOptions(''),
    loadMappings(),
    skuStore.fetchSkus({ ...skuQuery }),
    skuStore.fetchSuggestions(),
  ])
  const firstFail = results.find((r) => r.status === 'rejected')
  if (firstFail) toastError(firstFail.reason)
  try {
    await adapterStore.fetchList()
    platformList.value = adapterStore.list.map((a) => ({
      key: a.platform_key,
      name: a.display_name,
    }))
    const cats = await api.meta.categories()
    categories.value = cats.items
  } catch (e) {
    toastError(e)
  }
})

function platformDisplayName(key) {
  return platformList.value.find((p) => p.key === key)?.name || key
}

// 动态搜索内部 SKU（支持根据编码或商品名快速检索，默认抓取前 50 条）
async function searchSkuOptions(query = '') {
  skuSearchLoading.value = true
  try {
    const res = await api.skus.list({ q: query || undefined, size: 50 })
    skuSearchOptions.value = res.items
  } catch {
    skuSearchOptions.value = []
  } finally {
    skuSearchLoading.value = false
  }
}

// 统一映射列表加载
async function loadMappings() {
  const matchType = activeTab.value === 'confirmed' ? 'confirmed' : 'unmapped'
  await skuStore.fetchMappings({
    platform_key: mapQuery.platform_key || undefined,
    q: mapQuery.q || undefined,
    page: mapQuery.page,
    size: mapQuery.size,
    match_type: matchType,
  })
}

// 搜索/筛选时，将页码安全重置为 1
function handleMapSearch() {
  mapQuery.page = 1
  loadMappings()
}

function handleSkuSearch() {
  skuQuery.page = 1
  skuStore.fetchSkus({ ...skuQuery })
}

function handleTabChange(tab) {
  if (tab === 'skus') {
    skuQuery.page = 1
    skuStore.fetchSkus({ ...skuQuery })
  } else if (tab === 'suggest') {
    skuStore.fetchSuggestions(mapQuery.platform_key || undefined)
  } else {
    mapQuery.page = 1
    loadMappings()
  }
}

// 单项行内快速关联
function openSingleMap(row) {
  currentSingleItem.value = row
  singleSelectedSkuId.value = row.sku_id || null
  singleMapVisible.value = true
  searchSkuOptions('')
}

async function confirmSingleMap() {
  if (!singleSelectedSkuId.value || !currentSingleItem.value) {
    ElMessage.warning('请选择要关联的内部标准商品')
    return
  }
  singleSubmitting.value = true
  try {
    await skuStore.batchMap([
      {
        platform_key: currentSingleItem.value.platform_key,
        platform_product_code: currentSingleItem.value.platform_product_code,
        sku_id: singleSelectedSkuId.value,
      },
    ])
    ElMessage.success('商品关联成功！')
    singleMapVisible.value = false
    await loadMappings()
    await skuStore.fetchSuggestions()
  } catch (e) {
    toastError(e)
  } finally {
    singleSubmitting.value = false
  }
}

// 批量关联
async function batchMapTo() {
  if (!selectedMappings.value.length || !batchSkuId.value) {
    ElMessage.warning('请先勾选映射行并选择目标内部 SKU')
    return
  }
  batchSubmitting.value = true
  const items = selectedMappings.value.map((m) => ({
    platform_key: m.platform_key,
    platform_product_code: m.platform_product_code,
    sku_id: batchSkuId.value,
  }))
  try {
    await skuStore.batchMap(items)
    ElMessage.success(`成功批量关联 ${items.length} 个商品`)
    selectedMappings.value = []
    batchSkuId.value = null
    await loadMappings()
    await skuStore.fetchSuggestions()
  } catch (e) {
    toastError(e)
  } finally {
    batchSubmitting.value = false
  }
}

// 采纳单项建议
async function adopt(item, cand) {
  try {
    await skuStore.adopt(item.mapping_id, cand.sku_id)
    ElMessage.success(`已采纳关联：${cand.name}`)
    await skuStore.fetchSuggestions()
    await loadMappings()
  } catch (e) {
    toastError(e)
  }
}

// 一键采纳所有高置信度（≥90%）算法建议
async function adoptAllHighConfidence() {
  const candidatesToAdopt = []
  for (const item of skuStore.suggestions) {
    const top = item.candidates?.[0]
    if (top && top.score >= 90) {
      candidatesToAdopt.push({ item, cand: top })
    }
  }

  if (!candidatesToAdopt.length) {
    ElMessage.info('当前没有匹配度 ≥ 90% 的高置信度建议')
    return
  }

  try {
    await ElMessageBox.confirm(
      `系统已智能识别出 ${candidatesToAdopt.length} 个高置信度（匹配度 ≥ 90%）的商品建议，确定一键全部采纳？`,
      '批量采纳确认',
      { type: 'success', confirmButtonText: '确定采纳', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  let successCount = 0
  const failedNames = []
  for (const { item, cand } of candidatesToAdopt) {
    try {
      await skuStore.adopt(item.mapping_id, cand.sku_id)
      successCount++
    } catch {
      // 个别失败不中断整批，但必须让用户知道哪些没成
      failedNames.push(item.product_name || item.platform_product_code)
    }
  }

  if (failedNames.length) {
    ElMessage.warning(
      `已采纳 ${successCount} 个；${failedNames.length} 个失败：${failedNames.slice(0, 3).join('、')}${failedNames.length > 3 ? ' 等' : ''}`,
    )
  } else {
    ElMessage.success(`已一键采纳 ${successCount} 个高置信度商品关联！`)
  }
  await skuStore.fetchSuggestions()
  await loadMappings()
}

// 内部 SKU 表单
function openSkuForm(row) {
  Object.assign(
    skuForm,
    row || {
      id: null,
      sku_code: '',
      name: '',
      category: '',
      brand: '',
      cost_price: null,
      status: 'active',
    },
  )
  skuFormVisible.value = true
}

async function saveSku() {
  if (!skuForm.sku_code || !skuForm.name) {
    ElMessage.warning('SKU 编码和名称为必填项')
    return
  }
  try {
    const body = {
      sku_code: skuForm.sku_code,
      name: skuForm.name,
      category: skuForm.category || null,
      brand: skuForm.brand || null,
      cost_price: skuForm.cost_price || null,
      status: skuForm.status,
    }
    if (skuForm.id) {
      await skuStore.updateSku(skuForm.id, body)
      ElMessage.success('SKU 已更新')
    } else {
      await skuStore.createSku(body)
      ElMessage.success('SKU 已创建')
    }
    skuFormVisible.value = false
    await skuStore.fetchSkus({ ...skuQuery })
    await searchSkuOptions('')
  } catch (e) {
    toastError(e)
  }
}

async function removeSku(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除内部标准 SKU「${row.sku_code} ${row.name}」？若它仍被销售明细或映射记录引用，系统将拒绝删除（需先解除相关映射）。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await skuStore.removeSku(row.id)
    ElMessage.success('已删除')
    await skuStore.fetchSkus({ ...skuQuery })
    await searchSkuOptions('')
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="sku-workbench page-container">
    <!-- 顶部业务待办速览胶囊栏 -->
    <div class="kpi-banner">
      <div
        class="kpi-pill"
        :class="{ active: activeTab === 'unmapped', highlight: skuStore.mappings.total > 0 && activeTab === 'unmapped' }"
        @click="activeTab = 'unmapped'"
      >
        <el-icon class="icon warn"><Warning /></el-icon>
        <div class="info">
          <div class="label">待映射商品</div>
          <div class="val">{{ activeTab === 'unmapped' ? skuStore.mappings.total : '待处理' }}</div>
        </div>
      </div>

      <div
        class="kpi-pill"
        :class="{ active: activeTab === 'suggest' }"
        @click="activeTab = 'suggest'"
      >
        <el-icon class="icon suggest"><MagicStick /></el-icon>
        <div class="info">
          <div class="label">智能匹配建议</div>
          <div class="val">{{ skuStore.suggestions.length }} 项就绪</div>
        </div>
      </div>

      <div
        class="kpi-pill"
        :class="{ active: activeTab === 'confirmed' }"
        @click="activeTab = 'confirmed'"
      >
        <el-icon class="icon ok"><CircleCheck /></el-icon>
        <div class="info">
          <div class="label">已确认映射字典</div>
          <div class="val">{{ activeTab === 'confirmed' ? skuStore.mappings.total : '已建档' }}</div>
        </div>
      </div>

      <div
        class="kpi-pill"
        :class="{ active: activeTab === 'skus' }"
        @click="activeTab = 'skus'"
      >
        <el-icon class="icon main"><Collection /></el-icon>
        <div class="info">
          <div class="label">内部标准品档案库</div>
          <div class="val">{{ skuStore.skus.total }} 款</div>
        </div>
      </div>
    </div>

    <!-- 主工作区：选项卡按需聚焦 -->
    <el-card shadow="never" class="main-card">
      <el-tabs v-model="activeTab" class="business-tabs" @tab-change="handleTabChange">
        <!-- Tab 1: 待映射商品（核心待办） -->
        <el-tab-pane name="unmapped">
          <template #label>
            <span class="tab-label">
              <el-badge
                v-if="skuStore.mappings.total > 0 && activeTab === 'unmapped'"
                :value="skuStore.mappings.total"
                class="badge-dot"
              >
                待映射商品待办
              </el-badge>
              <span v-else>待映射商品待办</span>
            </span>
          </template>

          <!-- 顶部工具与批量操作条 -->
          <div class="tab-toolbar">
            <div class="filters">
              <el-select
                v-model="mapQuery.platform_key"
                placeholder="全部渠道平台"
                clearable
                style="width: 170px"
                @change="handleMapSearch"
              >
                <el-option
                  v-for="p in platformList"
                  :key="p.key"
                  :value="p.key"
                  :label="p.name"
                />
              </el-select>
              <el-input
                v-model="mapQuery.q"
                placeholder="搜索商品名称或平台编码"
                clearable
                style="width: 220px"
                :prefix-icon="Search"
                @change="handleMapSearch"
              />
            </div>

            <div class="batch-zone">
              <el-select
                v-model="batchSkuId"
                placeholder="搜索并选择关联的目标 SKU…"
                filterable
                remote
                :remote-method="searchSkuOptions"
                :loading="skuSearchLoading"
                clearable
                style="width: 260px"
              >
                <el-option
                  v-for="s in skuSearchOptions"
                  :key="s.id"
                  :value="s.id"
                  :label="`${s.sku_code} · ${s.name}`"
                />
              </el-select>
              <el-button
                type="primary"
                :disabled="!selectedMappings.length || !batchSkuId"
                :loading="batchSubmitting"
                @click="batchMapTo"
              >
                批量关联（{{ selectedMappings.length }}）
              </el-button>
            </div>
          </div>

          <!-- 未映射列表 -->
          <el-table
            :data="skuStore.mappings.items"
            size="small"
            border
            stripe
            class="mt-10"
            row-key="id"
            @selection-change="(rows) => (selectedMappings = rows)"
          >
            <el-table-column type="selection" width="42" align="center" reserve-selection />
            <el-table-column prop="platform_key" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ platformDisplayName(row.platform_key) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="platform_product_code" label="平台商品ID" width="160" />
            <el-table-column
              prop="platform_product_name"
              label="平台原始商品名称"
              min-width="260"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <span class="p-name">{{ row.platform_product_name || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  type="primary"
                  size="small"
                  link
                  :icon="Link"
                  @click="openSingleMap(row)"
                >
                  关联内部 SKU
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            layout="total, prev, pager, next"
            :total="skuStore.mappings.total"
            :page-size="mapQuery.size"
            :current-page="mapQuery.page"
            @current-change="(p) => { mapQuery.page = p; loadMappings() }"
          />
        </el-tab-pane>

        <!-- Tab 2: 智能建议（算法推荐） -->
        <el-tab-pane label="智能匹配建议" name="suggest">
          <div class="suggest-header">
            <div class="desc">
              <span>系统已基于商品名称语义相似度自动计算出可能对应的内部标准品：</span>
            </div>
            <div class="actions">
              <el-button
                type="success"
                :icon="Check"
                @click="adoptAllHighConfidence"
              >
                ⚡ 一键采纳所有高置信度建议（≥90%）
              </el-button>
              <el-button
                type="primary"
                plain
                :icon="Search"
                @click="skuStore.fetchSuggestions(mapQuery.platform_key || undefined)"
              >
                重新计算建议
              </el-button>
            </div>
          </div>

          <el-empty
            v-if="!skuStore.suggestions.length"
            description="暂无待采纳的算法建议，所有平台商品均已映射或暂无未建档商品。"
          />

          <div v-else class="suggest-list">
            <div
              v-for="item in skuStore.suggestions"
              :key="item.mapping_id"
              class="suggest-card"
            >
              <div class="sc-head">
                <div class="sc-title">
                  <el-tag size="small" effect="plain" class="mr-6">
                    {{ platformDisplayName(item.platform_key) }}
                  </el-tag>
                  <span class="sc-name">{{ fmtOrDash(item.product_name) }}</span>
                </div>
                <span class="sc-code">编码: {{ item.platform_product_code }}</span>
              </div>

              <div class="candidates-grid">
                <div
                  v-for="c in item.candidates"
                  :key="c.sku_id"
                  class="candidate-box"
                >
                  <div class="c-left">
                    <div class="c-title">{{ c.sku_code }} · {{ c.name }}</div>
                    <div class="c-score-bar">
                      <span class="c-score-text">匹配置信度</span>
                      <el-progress
                        :percentage="Math.round(c.score)"
                        :color="c.score >= 90 ? '#67C23A' : '#E6A23C'"
                        :stroke-width="6"
                        class="c-progress"
                      />
                      <span class="c-pct" :style="{ color: c.score >= 90 ? '#67C23A' : '#E6A23C' }">
                        {{ Math.round(c.score) }}%
                      </span>
                    </div>
                  </div>
                  <el-button
                    type="primary"
                    size="small"
                    :type="c.score >= 90 ? 'success' : 'primary'"
                    plain
                    :icon="Check"
                    @click="adopt(item, c)"
                  >
                    采纳此映射
                  </el-button>
                </div>
                <div v-if="!item.candidates.length" class="no-cand">
                  暂无高相似度候选品，建议直接前往「待映射商品」手工关联
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- Tab 3: 已确认映射字典 -->
        <el-tab-pane label="已确认映射字典" name="confirmed">
          <div class="tab-toolbar">
            <div class="filters">
              <el-select
                v-model="mapQuery.platform_key"
                placeholder="全部渠道平台"
                clearable
                style="width: 170px"
                @change="handleMapSearch"
              >
                <el-option
                  v-for="p in platformList"
                  :key="p.key"
                  :value="p.key"
                  :label="p.name"
                />
              </el-select>
              <el-input
                v-model="mapQuery.q"
                placeholder="搜索平台商品名称或平台编码"
                clearable
                style="width: 220px"
                :prefix-icon="Search"
                @change="handleMapSearch"
              />
            </div>
          </div>

          <el-table :data="skuStore.mappings.items" size="small" border stripe class="mt-10">
            <el-table-column prop="platform_key" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ platformDisplayName(row.platform_key) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="platform_product_code" label="平台商品ID" width="160" />
            <el-table-column
              prop="platform_product_name"
              label="平台原始商品名称"
              min-width="220"
              show-overflow-tooltip
            />
            <el-table-column label="关联的内部标准品" min-width="220">
              <template #default="{ row }">
                <div class="mapped-sku-val">
                  <b>{{ row.sku_code }}</b>
                  <span class="sku-name-text">{{ row.sku_name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="映射方式" width="110" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="matchTypeLabel[row.match_type]?.type || 'info'" effect="plain">
                  {{ matchTypeLabel[row.match_type]?.text || row.match_type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  type="primary"
                  size="small"
                  link
                  @click="openSingleMap(row)"
                >
                  变更绑定
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            layout="total, prev, pager, next"
            :total="skuStore.mappings.total"
            :page-size="mapQuery.size"
            :current-page="mapQuery.page"
            @current-change="(p) => { mapQuery.page = p; loadMappings() }"
          />
        </el-tab-pane>

        <!-- Tab 4: 内部标准品档案库 -->
        <el-tab-pane label="内部标准品档案库" name="skus">
          <div class="tab-toolbar">
            <div class="filters">
              <el-input
                v-model="skuQuery.q"
                placeholder="搜索标准品名称或编码"
                clearable
                style="width: 220px"
                :prefix-icon="Search"
                @change="handleSkuSearch"
              />
              <el-select
                v-model="skuQuery.category"
                placeholder="品类筛选"
                clearable
                style="width: 150px"
                @change="handleSkuSearch"
              >
                <el-option v-for="c in categories" :key="c" :value="c" :label="c" />
              </el-select>
            </div>
            <el-button type="primary" :icon="Plus" @click="openSkuForm(null)">
              新建标准 SKU
            </el-button>
          </div>

          <el-table :data="skuStore.skus.items" size="small" border stripe class="mt-10">
            <el-table-column prop="sku_code" label="SKU 编码" width="140" />
            <el-table-column prop="name" label="商品名称" min-width="220" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="brand" label="品牌" width="120" />
            <el-table-column prop="cost_price" label="标准成本价 (元)" width="130" align="right">
              <template #default="{ row }">
                {{ row.cost_price !== null && row.cost_price !== undefined ? `¥${Number(row.cost_price).toFixed(2)}` : '未设置' }}
              </template>
            </el-table-column>
            <el-table-column label="在售状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
                  {{ row.status === 'active' ? '在售' : '停售' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" :icon="Edit" @click="openSkuForm(row)">
                  编辑
                </el-button>
                <el-button link type="danger" size="small" :icon="Delete" @click="removeSku(row)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            layout="total, prev, pager, next"
            :total="skuStore.skus.total"
            :page-size="skuQuery.size"
            :current-page="skuQuery.page"
            @current-change="(p) => { skuQuery.page = p; skuStore.fetchSkus({ ...skuQuery }) }"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 单项快速关联弹窗 -->
    <el-dialog
      v-model="singleMapVisible"
      title="关联内部标准商品"
      width="480px"
      append-to-body
    >
      <div v-if="currentSingleItem" class="map-dialog-body">
        <div class="item-meta">
          <div class="row">
            <span class="label">渠道平台：</span>
            <el-tag size="small">{{ platformDisplayName(currentSingleItem.platform_key) }}</el-tag>
          </div>
          <div class="row">
            <span class="label">平台编码：</span>
            <span class="code">{{ currentSingleItem.platform_product_code }}</span>
          </div>
          <div class="row">
            <span class="label">商品名称：</span>
            <span class="name">{{ currentSingleItem.platform_product_name || '—' }}</span>
          </div>
        </div>

        <div class="target-select-zone">
          <div class="select-label">选择对应的内部标准 SKU：</div>
          <el-select
            v-model="singleSelectedSkuId"
            placeholder="输入编码或名称搜索标准商品…"
            filterable
            remote
            :remote-method="searchSkuOptions"
            :loading="skuSearchLoading"
            style="width: 100%"
          >
            <el-option
              v-for="s in skuSearchOptions"
              :key="s.id"
              :value="s.id"
              :label="`${s.sku_code} · ${s.name} ${s.category ? '(' + s.category + ')' : ''}`"
            />
          </el-select>
        </div>
      </div>
      <template #footer>
        <el-button @click="singleMapVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="singleSubmitting"
          @click="confirmSingleMap"
        >
          确认关联
        </el-button>
      </template>
    </el-dialog>

    <!-- 内部 SKU 新增 / 编辑抽屉 -->
    <el-drawer
      v-model="skuFormVisible"
      :title="skuForm.id ? '编辑内部标准品' : '新增内部标准品'"
      size="380px"
    >
      <el-form label-width="90px">
        <el-form-item label="SKU 编码" required>
          <el-input
            v-model="skuForm.sku_code"
            :disabled="!!skuForm.id"
            placeholder="如 SKU-1001 (唯一)"
          />
        </el-form-item>
        <el-form-item label="标准名称" required>
          <el-input v-model="skuForm.name" placeholder="商品标准全称" />
        </el-form-item>
        <el-form-item label="所属品类">
          <el-input v-model="skuForm.category" placeholder="如 坚果炒货 / 连衣裙" />
        </el-form-item>
        <el-form-item label="品牌名称">
          <el-input v-model="skuForm.brand" placeholder="如 旗舰自主品牌" />
        </el-form-item>
        <el-form-item label="标准成本价">
          <el-input-number
            v-model="skuForm.cost_price"
            :precision="2"
            :min="0"
            style="width: 100%"
            placeholder="用于毛利与杜邦分析"
          />
        </el-form-item>
        <el-form-item label="销售状态">
          <el-radio-group v-model="skuForm.status">
            <el-radio value="active">在售</el-radio>
            <el-radio value="discontinued">停售</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveSku">保存档案</el-button>
          <el-button @click="skuFormVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-drawer>
  </div>
</template>

<style scoped>
.sku-workbench {
  width: 100%;
}

/* 顶部待办业务胶囊横幅 */
.kpi-banner {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

@media (max-width: 1024px) {
  .kpi-banner {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .kpi-banner {
    grid-template-columns: 1fr;
  }
}

.kpi-pill {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-med) var(--ease-out), border-color var(--dur-fast);
}

.kpi-pill:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: var(--shadow-hover);
  transform: translateY(-2px);
}

.kpi-pill.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.kpi-pill .icon {
  font-size: 20px;
  padding: 10px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.kpi-pill .icon.warn {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.kpi-pill .icon.suggest {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.kpi-pill .icon.ok {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.kpi-pill .icon.main {
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
}

:global(html.dark) .kpi-pill .icon.warn {
  background: rgba(230, 162, 60, 0.15);
}

:global(html.dark) .kpi-pill .icon.suggest {
  background: rgba(31, 78, 121, 0.25);
}

:global(html.dark) .kpi-pill .icon.ok {
  background: rgba(103, 194, 58, 0.15);
}

:global(html.dark) .kpi-pill .icon.main {
  background: var(--el-fill-color);
}

.kpi-pill .info .label {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.kpi-pill .info .val {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--el-text-color-primary);
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}

.main-card {
  border-radius: var(--radius-lg);
  border-color: var(--el-border-color-lighter);
  box-shadow: var(--shadow-sm);
}

.business-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

.tab-label {
  font-size: var(--text-md);
  font-weight: 600;
}

.tab-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.batch-zone {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mt-10 {
  margin-top: 10px;
}

.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

.p-name {
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.mapped-sku-val {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mapped-sku-val b {
  color: var(--el-color-primary);
}

.sku-name-text {
  color: var(--el-text-color-regular);
}

/* 建议卡片 */
.suggest-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: var(--el-fill-color-light);
  border-radius: var(--radius-md);
  margin-bottom: 14px;
}

.suggest-header .desc {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.suggest-header .actions {
  display: flex;
  gap: 10px;
}

.suggest-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.suggest-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 14px 18px;
  background: var(--el-bg-color);
  transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-med) var(--ease-out);
}

.suggest-card:hover {
  border-color: var(--el-border-color);
  box-shadow: var(--shadow-hover);
}

.sc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.sc-title {
  display: flex;
  align-items: center;
}

.mr-6 {
  margin-right: 6px;
}

.sc-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.sc-code {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}

.candidates-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 10px;
  margin-top: 10px;
}

.candidate-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-fill-color-lighter);
  border-radius: var(--radius-md);
  padding: 10px 14px;
  gap: 10px;
}

.c-left {
  flex: 1;
  min-width: 0;
}

.c-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.c-score-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.c-score-text {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
}

.c-progress {
  flex: 1;
}

.c-pct {
  font-size: var(--text-xs);
  font-weight: 700;
  width: 32px;
  font-variant-numeric: tabular-nums;
}

.no-cand {
  font-size: var(--text-xs);
  color: var(--el-text-color-placeholder);
  padding: 8px 0;
}

/* 弹窗内容 */
.map-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.item-meta {
  background: var(--el-fill-color-light);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.item-meta .row {
  display: flex;
  align-items: center;
  font-size: var(--text-sm);
}

.item-meta .label {
  width: 75px;
  color: var(--el-text-color-secondary);
}

.item-meta .code {
  font-family: monospace;
  color: var(--el-text-color-regular);
}

.item-meta .name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.target-select-zone .select-label {
  font-size: var(--text-sm);
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}
</style>
