import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

import { injectCsp, type EntryKind } from './src/csp';

/** 用 HTML 的文件名判断入口种类：`embed.html` → embed，其余（index.html）→ standalone。 */
function entryKindOf(path: string): EntryKind {
  return path.includes('embed.html') ? 'embed' : 'standalone';
}

/**
 * 只在 `vite build` 时把 CSP 注入产物（D9），且**按入口注入不同策略**（阶段 13 / W13-1）。
 *
 * `apply: 'build'` 是硬要求：dev 下 Vite 用内联脚本 + ws 做 HMR，
 * 注入 `script-src 'self'` / `connect-src 'self'` 会直接把 HMR 打死。
 *
 * `ctx.path` 在 build 阶段就是被处理的 HTML 文件（`/index.html`、`/embed.html`）；
 * 少见的宿主不发 `path` 时退回 `ctx.filename`，两者都没有就按最严的 standalone 来。
 */
const cspPlugin = {
  name: 'tm-csp',
  apply: 'build' as const,
  transformIndexHtml: (html: string, ctx: { path?: string; filename?: string }): string =>
    injectCsp(html, entryKindOf(ctx.path ?? ctx.filename ?? '')),
};

export default defineConfig({
  plugins: [react(), cspPlugin],
  server: {
    port: 5174,
    host: true,
  },
  preview: {
    port: 5174,
    host: true,
  },
  build: {
    target: 'es2020',
    rollupOptions: {
      // 多页构建：两个 HTML 各是一个入口，各带自己的 CSP（见上面的插件）。
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        embed: fileURLToPath(new URL('./embed.html', import.meta.url)),
      },
    },
  },
  test: {
    environment: 'node',
    // `*.test.tsx` 是深色主题的组件测试（自己用 `// @vitest-environment jsdom` 声明环境）。
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // 沙箱里多 worker 并发写临时目录会 EPERM；这几个测试的耗时本来就可忽略。
    fileParallelism: false,
  },
});
