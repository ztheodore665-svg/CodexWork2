/** 应用入口 —— 按 HTML 上的 `data-entry` 分发到两套主题之一（阶段 13 / W13-1）。
 *
 * ## 两个入口，同一份入口代码
 *
 * `index.html` 写 `<html data-entry="standalone">`，`embed.html` 写 `data-entry="embed"`。
 * 这里读那一个属性决定动态 import 哪套 App：
 *
 * ```
 * standalone → theme-dark/App   （深色，零网络：底图是自绘降级格网）
 * embed      → theme-light/App  （浅色，允许高德底图）
 * ```
 *
 * **为什么动态 import 而不是静态 import 两套再挑一个**：
 * 静态 import 会让两个入口的产物都包含两套 UI（含高德适配层），
 * 独立版的 JS 里就永远躺着一份"能联网"的代码，与"standalone 零网络"的
 * 自我承诺相冲突。动态 import 之后，两套 UI 各自是独立 chunk，
 * 只有真正需要它的入口才会把那一块下下来。
 *
 * ## 注意：这里用 async IIFE 而不是 top-level await
 *
 * 构建目标 `es2020` 不支持 TLA（Vite 会报"Top-level await is not available"）。
 * 用 IIFE 语义完全一样，且不逼着我们抬 `build.target`。
 *
 * 刻意**不用 `StrictMode`**：它会在开发模式下双调用 effect，而播放页的 effect
 * 会创建一个底图实例 + 一条 rAF 循环。双挂载会导致「地图容器被销毁两次」
 * 这类只在 dev 出现、在生产不出现的错位现象，掩盖真实问题。
 * 生产构建本来也不受 StrictMode 影响，所以这里直接省略。
 */

import { createRoot } from 'react-dom/client';

import { isProd } from './env';

/** 从 `<html data-entry>` 读入口种类；非法/缺失一律按 standalone（fail-closed）。 */
function readEntryKind(): 'standalone' | 'embed' {
  const raw = document.documentElement.dataset.entry;
  return raw === 'embed' ? 'embed' : 'standalone';
}

/** 挂载。两块 UI 的 App 模块都默认导出组件，各自 import 自己的样式表。 */
async function bootstrap(): Promise<void> {
  const container = document.getElementById('root');
  if (!container) throw new Error('#root 不存在：HTML 入口被改坏了');

  const kind = readEntryKind();
  const { default: App } =
    kind === 'standalone' ? await import('./theme-dark/App') : await import('./theme-light/App');

  createRoot(container).render(<App />);

  // Service Worker 只在**独立入口**的生产构建里注册（§8.4 / B6）：
  //  - dev 下注册会缓存 HMR 产物，改代码不生效；
  //  - 嵌入版跑在 iframe 里、且底图要联网，缓存应用壳没有收益，只会让主应用的
  //    "换一版"变得不可控，所以 embed 入口不注册。
  if (kind === 'standalone' && isProd() && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        /* 注册失败不影响使用：离线能力是第二层证据，不是前置条件 */
      });
    });
  }
}

void bootstrap();
