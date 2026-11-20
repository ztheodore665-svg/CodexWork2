import { defineConfig } from 'vitest/config';

// 这个包要摸浏览器 API：<input type=file>、URLSearchParams(location.search)、history.replaceState。
// jsdom 25 全都实现了；只有 canvas 2D 没有（getContext('2d') 返回 null），
// 所以缩略图逻辑一律走依赖注入的假 canvas，见 src/thumbnail.test.ts。
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    fileParallelism: false,
  },
});
