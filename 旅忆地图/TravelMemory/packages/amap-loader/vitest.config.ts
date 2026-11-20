import { defineConfig } from 'vitest/config';

// 加载器要插 <script>、读 window，必须用 jsdom。
// fileParallelism: false 是沙箱里多 worker 争临时目录（EPERM）的解法，见 stage9 §6。
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    fileParallelism: false,
  },
});
