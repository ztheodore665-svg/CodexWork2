import { defineConfig } from 'vitest/config';

// 纯逻辑包：不碰 DOM，node 环境即可。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
