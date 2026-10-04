import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/upload' },
  { path: '/upload', name: 'upload', component: () => import('../views/UploadView.vue'),
    meta: { title: '数据上传' } },
  { path: '/dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'),
    meta: { title: '日报看板', needsDate: true } },
  { path: '/weekly', name: 'weekly', component: () => import('../views/WeeklyView.vue'),
    meta: { title: '周度复盘', needsDate: true } },
  { path: '/diagnosis', name: 'diagnosis', component: () => import('../views/AiDiagnosisView.vue'),
    meta: { title: '业务诊断', needsDate: true } },
  { path: '/sku', name: 'sku', component: () => import('../views/SkuView.vue'),
    meta: { title: 'SKU 映射' } },
  { path: '/adapter', name: 'adapter', component: () => import('../views/AdapterView.vue'),
    meta: { title: '适配器管理' } },
  { path: '/export', name: 'export', component: () => import('../views/ExportView.vue'),
    meta: { title: '导出中心', needsDate: true } },
  // 乱输路径回首页，不留白屏
  { path: '/:pathMatch(.*)*', redirect: '/upload' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
