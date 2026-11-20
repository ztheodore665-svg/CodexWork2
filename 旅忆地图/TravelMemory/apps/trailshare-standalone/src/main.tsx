/** 应用入口 —— 按 HTML 上的 `data-entry` 分发到两套主题之一（阶段 13 / W13-4）。
 *
 * ## 两个入口，两套承诺，两套 UI
 *
 * ```
 * index.html (data-entry="standalone") → theme-dark/App  ：全程零网络、没有地图、纯文本列表
 * embed.html (data-entry="embed")      → theme-light/App ：允许地图底图、有 PlaceMap、有 iframe 桥
 * ```
 *
 * **为什么动态 import 而不是静态 import 两套再挑**：静态 import 会让两个入口的产物
 * 都带上两套 UI（以及高德适配层）。独立版的产物里从此躺着一份"能联网"的代码，
 * 与"全程零网络请求"的自我承诺冲突。动态 import 之后，两套 UI 各自成 chunk，
 * 只有真正需要它的入口才会把那一块下下来。
 *
 * ## 不用 top-level await
 *
 * 构建目标 `es2020` 不支持 TLA（Vite 会直接报错）。这里用 async IIFE，
 * 语义一样且不必抬 `build.target`。
 *
 * 与 Track Studio 一样刻意**不用 `StrictMode`**：它的双挂载会让身份读取跑两次、
 * 也让 `initBridge` 在 dev 下注册/注销两次（真机上表现为偶发的"ready 发了两次"）。
 */

import { createRoot } from 'react-dom/client';

import type { EntryKind } from './csp';

/** 入口种类：来自 HTML 的 `data-entry`；缺省按最严格的 standalone 处理（fail-closed）。 */
function readEntryKind(): EntryKind {
  return document.documentElement.dataset.entry === 'embed' ? 'embed' : 'standalone';
}

/** 挂载。两块 UI 都接收 `entry`（深色版忽略它，但保持同一份调用形状）。 */
async function bootstrap(): Promise<void> {
  const container = document.getElementById('root');
  if (!container) throw new Error('#root 不存在：index.html / embed.html 被改坏了');

  const entry = readEntryKind();
  const { default: App } =
    entry === 'standalone' ? await import('./theme-dark/App') : await import('./theme-light/App');

  createRoot(container).render(<App entry={entry} />);

  // Service Worker 只在**独立入口**的生产构建里注册（§8.4 / B6）：
  //  - dev 下注册会缓存 HMR 产物，改代码不生效；
  //  - embed 是 iframe 里的一次性视图，缓存它只会让主应用拿到旧版本。
  if (import.meta.env.PROD && entry === 'standalone' && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        /* 注册失败不影响使用：离线能力是第二层证据，不是前置条件 */
      });
    });
  }
}

void bootstrap();
