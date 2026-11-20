import { defineConfig } from 'vitest/config';

// 纯逻辑包：只做对象校验与序列化，不碰 DOM，node 环境即可。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // 沙箱里多 worker 并发写临时目录会 EPERM（F:\temp\...\ssr\...），
    // 关掉文件级并行即可；这几个纯逻辑文件的耗时本来就可忽略。
    fileParallelism: false,
  },
});
