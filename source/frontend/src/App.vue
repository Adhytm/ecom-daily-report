<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  UploadFilled,
  TrendCharts,
  Calendar,
  Opportunity,
  Switch,
  Operation,
  Download,
  Fold,
  Expand,
  Sunny,
  Moon,
  Monitor,
} from '@element-plus/icons-vue'
import { useReportStore } from './stores/report'
import { themeMode, setTheme, isDark } from './utils/theme'
import { todayStr, addDays } from './utils/date'

const route = useRoute()
const report = useReportStore()
const collapsed = ref(false)

report.initDate()

// 日期选择跨页/刷新持久化（session 级）
watch(
  () => report.reportDate,
  (d) => {
    if (d) sessionStorage.setItem('ecom_report_date', d)
  },
)

// 「昨日/今日」按钮的基准日期：页面挂过夜后必须重新计算，
// 窗口重新获得焦点时刷新一次
const todayTick = ref(Date.now())
window.addEventListener('focus', () => {
  todayTick.value = Date.now()
})
const today = computed(() => {
  void todayTick.value
  return todayStr()
})

const themeOptions = [
  { value: 'light', label: '浅色模式', icon: Sunny },
  { value: 'dark', label: '深色模式', icon: Moon },
  { value: 'auto', label: '跟随系统', icon: Monitor },
]

const currentThemeLabel = computed(() => {
  return themeOptions.find((t) => t.value === themeMode.value)?.label || '主题'
})

function setQuickDate(offset) {
  report.reportDate = addDays(today.value, offset)
}

const isYesterday = computed(() => report.reportDate === addDays(today.value, -1))
const isToday = computed(() => report.reportDate === today.value)
</script>

<template>
  <el-container class="layout">
    <!-- 侧边栏：现代商业清爽风格 -->
    <el-aside :width="collapsed ? '68px' : '210px'" class="aside">
      <div class="logo-area">
        <div class="logo-badge">
          <el-icon :size="20"><TrendCharts /></el-icon>
        </div>
        <div v-if="!collapsed" class="logo-text">
          <div class="brand-title">电商销售日报</div>
          <div class="brand-sub">多平台智能分析</div>
        </div>
      </div>

      <el-menu
        :default-active="route.path"
        :collapse="collapsed"
        router
        class="modern-menu"
      >
        <el-menu-item-group>
          <template #title><span class="menu-group-title">经营</span></template>
          <el-menu-item index="/upload">
            <el-icon><UploadFilled /></el-icon>
            <template #title><span>数据上传</span></template>
          </el-menu-item>
          <el-menu-item index="/dashboard">
            <el-icon><TrendCharts /></el-icon>
            <template #title><span>日报看板</span></template>
          </el-menu-item>
          <el-menu-item index="/weekly">
            <el-icon><Calendar /></el-icon>
            <template #title><span>周度复盘</span></template>
          </el-menu-item>
          <el-menu-item index="/diagnosis">
            <el-icon><Opportunity /></el-icon>
            <template #title><span>业务诊断</span></template>
          </el-menu-item>
        </el-menu-item-group>
        <el-menu-item-group>
          <template #title><span class="menu-group-title">管理</span></template>
          <el-menu-item index="/sku">
            <el-icon><Switch /></el-icon>
            <template #title><span>SKU 映射</span></template>
          </el-menu-item>
          <el-menu-item index="/adapter">
            <el-icon><Operation /></el-icon>
            <template #title><span>适配器管理</span></template>
          </el-menu-item>
          <el-menu-item index="/export">
            <el-icon><Download /></el-icon>
            <template #title><span>导出中心</span></template>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>

      <div class="aside-footer" @click="collapsed = !collapsed">
        <el-icon :size="16">
          <Expand v-if="collapsed" />
          <Fold v-else />
        </el-icon>
        <span v-if="!collapsed" class="collapse-tip">收起导航</span>
      </div>
    </el-aside>

    <!-- 主体区域 -->
    <el-container class="content-container">
      <el-header class="header" height="60px">
        <div class="header-left">
          <h2 class="header-title">{{ route.meta?.title || '多平台电商销售日报' }}</h2>
          <span class="header-desc">统一多平台口径 · 3分钟出具经营日报</span>
        </div>

        <div class="header-right">
          <!-- 快捷日期药丸（仅数据类页面显示：看板/周报/诊断/导出） -->
          <template v-if="route.meta?.needsDate">
            <div class="date-quick-bar">
              <el-button
                size="small"
                :type="isYesterday ? 'primary' : 'default'"
                :plain="!isYesterday"
                round
                @click="setQuickDate(-1)"
              >
                昨日
              </el-button>
              <el-button
                size="small"
                :type="isToday ? 'primary' : 'default'"
                :plain="!isToday"
                round
                @click="setQuickDate(0)"
              >
                今日
              </el-button>
            </div>

            <div class="date-picker-wrap">
              <span class="label">日期</span>
              <el-date-picker
                v-model="report.reportDate"
                type="date"
                value-format="YYYY-MM-DD"
                :clearable="false"
                size="default"
                style="width: 140px"
              />
            </div>

            <el-divider direction="vertical" style="height: 20px" />
          </template>

          <!-- 主题切换 -->
          <el-dropdown trigger="click" @command="setTheme">
            <el-button size="default" class="theme-btn" round>
              <el-icon style="margin-right: 4px">
                <Sunny v-if="themeMode === 'light'" />
                <Moon v-else-if="themeMode === 'dark'" />
                <Monitor v-else />
              </el-icon>
              <span>{{ currentThemeLabel }}</span>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu class="theme-dropdown">
                <el-dropdown-item
                  v-for="opt in themeOptions"
                  :key="opt.value"
                  :command="opt.value"
                  :class="{ 'is-active-theme': themeMode === opt.value }"
                >
                  <el-icon style="margin-right: 8px"><component :is="opt.icon" /></el-icon>
                  <span>{{ opt.label }}</span>
                  <span v-if="themeMode === opt.value" style="margin-left: 12px; color: var(--el-color-primary)">✓</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
  color: var(--el-text-color-primary);
}

/* 侧边栏：半透材质 + 细腻边框，融入页面背景层次 */
.aside {
  background: color-mix(in srgb, var(--el-bg-color) 88%, transparent);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-right: 1px solid var(--el-border-color-extra-light);
  display: flex;
  flex-direction: column;
  transition: width var(--dur-med) var(--ease-out);
  overflow: hidden;
  z-index: 10;
}

.logo-area {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
  flex-shrink: 0;
}

.logo-badge {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, var(--el-color-primary), var(--el-color-primary-dark-2));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(31, 78, 121, 0.3);
}

.logo-text {
  min-width: 0;
  overflow: hidden;
}

.brand-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  letter-spacing: -0.2px;
}

.brand-sub {
  font-size: var(--text-xs);
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.modern-menu {
  flex: 1;
  border-right: none;
  background: transparent;
  padding: 12px 10px;
}

.modern-menu :deep(.el-menu-item-group__title) {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  letter-spacing: 2px;
  padding: 12px 12px 4px;
  height: auto;
  line-height: 1.6;
}

.modern-menu :deep(.el-menu-item) {
  height: 42px;
  line-height: 42px;
  border-radius: var(--radius-md);
  margin-bottom: 2px;
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.modern-menu :deep(.el-menu-item:hover) {
  background-color: var(--el-fill-color-light);
  color: var(--el-color-primary);
}

.modern-menu :deep(.el-menu-item.is-active) {
  background-color: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.aside-footer {
  height: 46px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 20px;
  border-top: 1px solid var(--el-border-color-extra-light);
  cursor: pointer;
  color: var(--el-text-color-secondary);
  font-size: var(--text-sm);
  transition: color var(--dur-fast), background-color var(--dur-fast);
  flex-shrink: 0;
}

.aside-footer:hover {
  color: var(--el-color-primary);
  background-color: var(--el-fill-color-light);
}

/* 顶栏：半透明毛玻璃（内容从其下滚过时保持可读） */
.content-container {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.header {
  background: color-mix(in srgb, var(--el-bg-color) 82%, transparent);
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
  border-bottom: 1px solid var(--el-border-color-extra-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 20;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-title {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--el-text-color-primary);
  letter-spacing: -0.2px;
}

.header-desc {
  font-size: var(--text-xs);
  color: var(--el-text-color-placeholder);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.date-quick-bar {
  display: flex;
  gap: 6px;
}

.date-picker-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
}

.date-picker-wrap .label {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.theme-btn {
  display: inline-flex;
  align-items: center;
  padding: 6px 14px;
  font-size: var(--text-sm);
}

.main {
  padding: 22px 26px;
  overflow: auto;
  background: transparent;
}
</style>
