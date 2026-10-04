import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
// 全局设计系统（tokens + Element 覆写 + 工具类），必须在组件样式之前加载
import './styles/index.css'
import App from './App.vue'
import router from './router'
import { initTheme } from './utils/theme'

initTheme()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
