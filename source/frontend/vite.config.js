import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：/api 代理到后端 8000 端口（规划 2.3）
// 生产模式：构建产物输出到 backend/app/static，由 FastAPI 挂载 /
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
})
