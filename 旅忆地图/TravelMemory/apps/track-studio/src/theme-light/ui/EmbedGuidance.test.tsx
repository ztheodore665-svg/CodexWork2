// @vitest-environment jsdom
/** `embed.html` 被直接打开时的整页引导（任务书 §6.4）。
 *
 * 锁三件事：
 *  1. 文案与链接是**任务书里的原话**（标题带句号、链接指向 `./index.html`）；
 *  2. 组件**绝不自动跳转** —— 这条用"源码里不许出现跳转 API"来断言，
 *     理由见下面 `describe('不自动跳转')` 的注释（运行时真的很难断言"没发生什么"）；
 *  3. `theme-light/App.tsx` 真的把这个引导**接上了**（`entry === 'embed' && !inIframe`），
 *     否则组件再对也没人渲染它。
 *
 * 源码用 Vite 的 `?raw` 读（`import x from './x.tsx?raw'`）：
 * jsdom 环境下 `import.meta.url` 是 http(s) 的，`fs.readFileSync` 只接受 `file:`
 * （实测报 `The URL must be of scheme file`），而 `?raw` 走 Vite 的转换管线不受影响。
 */

import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EmbedGuidance } from './EmbedGuidance';
import guidanceSource from './EmbedGuidance.tsx?raw';
import appSource from '../App.tsx?raw';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

describe('EmbedGuidance —— 文案与出口', () => {
  it('标题是任务书原话（带句号），并给出「独立打开轨迹演示 →」链接', async () => {
    await act(async () => {
      root.render(<EmbedGuidance />);
    });

    const heading = container.querySelector('h1');
    expect(heading?.textContent).toBe('这个页面需要在主应用内使用。');

    const link = container.querySelector('a');
    expect(link).not.toBeNull();
    // 相对路径：产物里 embed.html 与 index.html 同目录，部署到子路径也不会断
    expect(link?.getAttribute('href')).toBe('./index.html');
    expect(link?.textContent?.replace(/\s+/g, '')).toBe('独立打开轨迹演示→');

    // 说清"为什么不能直接用"，避免用户以为是坏了
    expect(container.textContent).toContain('embed.html');
  });
});

describe('EmbedGuidance —— 不自动跳转', () => {
  /**
   * 为什么是源码断言而不是运行时断言：
   * "没有发生跳转"在 jsdom 里没法可靠观测 —— `window.location` 的 setter 不可替换
   * （赋值会真的触发 navigation not-implemented 报错，而不是留下可断言的痕迹），
   * spy 也拦不住 `location.replace`。所以退一步锁住**跳转 API 的出现**：
   * 只要源码里没有 `location.href =` / `assign(` / `replace(` / `window.open(`，
   * 它就不可能悄悄跳走。这也是任务书 §6.4"绝不自动跳转"唯一可自动化的证据。
   */
  it('组件源码里没有 location 跳转 / window.open', () => {
    expect(guidanceSource).not.toContain('location.href =');
    expect(guidanceSource).not.toContain('location.assign(');
    expect(guidanceSource).not.toContain('location.replace(');
    expect(guidanceSource).not.toContain('window.open(');
    expect(guidanceSource).not.toContain('navigate(');
  });
});

describe('EmbedGuidance —— 接线', () => {
  it('theme-light/App.tsx 在 embed 入口且不在 iframe 时渲染引导页', () => {
    // 判定口径：只看入口种类 + 是否在 iframe 里（不把 `?embedded=1` 掺进来，
    // 否则同源 harness `/?embedded=1` 的能力按钮会一起变灰）
    expect(appSource).toContain("entry === 'embed' && !inIframe");
    expect(appSource).toContain('<EmbedGuidance />');
    expect(appSource).toContain("document.documentElement.dataset.entry");
    expect(appSource).toContain('window.parent !== window');
  });
});
