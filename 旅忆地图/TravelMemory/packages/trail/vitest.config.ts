import { defineConfig } from 'vitest/config';

// 内核是纯逻辑 + Canvas 2D：不依赖 React，渲染器测试用桩 canvas。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // 沙箱里多 worker 并发写临时目录会 EPERM（F:\temp\...\ssr\...），
    // 关掉文件级并行即可；这几个纯逻辑文件的耗时本来就可忽略。
    fileParallelism: false,
  },
});
