import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端默认 http://127.0.0.1:8000；容器/多机场景可用环境变量 VITE_BACKEND 覆盖
//（docker compose 里前端容器构建时传入 VITE_BACKEND=http://backend:8000，
//  但 nginx 反代方案下代理在后端完成，构建时无需真正连后端）
const BACKEND = process.env.VITE_BACKEND || 'http://127.0.0.1:8000'

export default defineConfig({
  base: './', // 打包后可由任意静态服务器/FastAPI 托管
  plugins: [vue()],
  server: {
    host: true, // 允许容器/局域网访问
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
  },
})
