import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

import { entryOf, injectCsp } from './src/csp';

/**
 * CSP 只在 build 注入（D9）。`apply: 'build'` 是硬要求 —— dev 下 Vite 用内联脚本
 * 与 ws 做 HMR，注入 CSP 会立刻打死 HMR。
 *
 * W12-4：**两个入口两套策略**。`transformIndexHtml` 的第二个参数（ctx）带 `path`，
 * 用它区分 `index.html` / `embed.html`；`injectCsp` 内部还会再看一眼 HTML 上的
 * `data-entry`，两条判断都指向同一个结果（见 `src/csp.ts` 的 `entryOf`）。
 */
const cspPlugin = {
  name: 'tm-csp',
  apply: 'build' as const,
  transformIndexHtml: (html: string, ctx: { path?: string; filename?: string }): string => {
    const path = ctx.path ?? ctx.filename ?? '';
    return injectCsp(html, entryOf(html, path));
  },
};

export default defineConfig({
  plugins: [react(), cspPlugin],
  server: {
    port: 5175,
    host: true,
  },
  preview: {
    port: 5175,
    host: true,
  },
  build: {
    target: 'es2020',
    // W12-4：两个 HTML 入口。`main` = 独立打开（全程零网络），`embed` = 主应用 iframe（可带地图）。
    // 路径必须用 `fileURLToPath`：`__dirname` 在 ESM 的 config 里不适用，
    // 而 `new URL(...).pathname` 在 Windows 上会给出 `/F:/...` 这种非法盘符路径。
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        embed: fileURLToPath(new URL('./embed.html', import.meta.url)),
      },
    },
  },
  test: {
    environment: 'node',
    // `.tsx` 也要收：PlaceList 的分组/渲染断言需要 jsdom（文件头 `@vitest-environment jsdom`）。
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // 沙箱里多 worker 并发写临时目录会 EPERM；这些测试的耗时本来就可忽略。
    fileParallelism: false,
    testTimeout: 20000,
  },
});
