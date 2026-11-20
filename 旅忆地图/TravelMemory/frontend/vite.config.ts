// vitest/config 的 defineConfig 才是带 `test` 字段类型的那一个：
// 从 'vite' 导入的话，`test` 会被判成不存在于 UserConfig 上（tsc 直接红）。
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 后端 API 基地址。**必须用 `||` 而不是 `??`**：
// `.env.local` 里 `VITE_API_BASE=` 是**留空**的（语义 = "走同源相对路径 + 开发代理），
// 而 `??` 只在 null/undefined 时兜底 —— 空串会原样穿过，代理 target 变成 `''`，
// 于是 dev server 下每一个 /api 请求都静默失败（表现为"照片导入失败"、
// "问答没反应"，而后端直连完全正常，极难联想到是代理）。
const API_BASE = process.env.VITE_API_BASE || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发期把 /api 代理到后端，省掉跨域配置（生产由 Nginx 同域反代）
    proxy: {
      '/api': { target: API_BASE, changeOrigin: true },
    },
  },
  test: {
    // node 而非 jsdom：密码学与协议层不碰 DOM，跑得更快也不会被 jsdom 的
    // crypto 实现差异坑到（jsdom 没有 crypto.subtle）。
    environment: 'node',
    // 放在 src/ 下而不是 tests/：tsconfig 的 include 是 ["src"]，
    // 放外面就进不了 `npm run typecheck`，测试文件的类型错误会在 CI 里溜过去。
    include: ['src/**/*.test.ts'],
    reporters: 'default',
  },
});
