// @vitest-environment jsdom
/** `StudioEmbed` 的**接线**测试（阶段 11 QA 补测）。
 *
 *  为什么需要这一份：`services/studio-bridge/bridge.ts` 自己的单测全绿，
 *  但它证明不了"组件真的把桥建起来了"。握手链路上有一环只有渲染测试能碰到：
 *  iframe 挂在 Radix `DialogPortal` 里，Portal 首帧返回 null、靠
 *  `useLayoutEffect` 二次渲染才吐出子节点 —— 如果建桥的 effect 只看
 *  `iframeRef.current` 且依赖数组里没有"iframe 已挂载"这个信号，
 *  桥就永远不会被创建，而所有单测都不会红。这个文件就是钉住那一环的。
 *
 *  断言口径（与产品文案一致）：
 *    - 收到合法的 `ready` → 副标题从「正在与小应用握手…」变成「已传入 N 张缩略图」；
 *    - 5 秒内没收到 → 出现「握手超时」错误卡片（用户能看懂的那条）。
 *
 *  注意：这里**不**断言"小应用侧收到了 init"（那要真浏览器，见 QA 报告 C4）。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import React from 'react';

import { StudioEmbed } from './StudioEmbed';

const STUDIO_ORIGIN = 'http://localhost:5174';

function renderEmbed(): { iframe: () => HTMLIFrameElement | null } {
  render(
    React.createElement(StudioEmbed, {
      open: true,
      mode: 'track',
      photos: [],
      onClose: () => {},
    }),
  );
  return {
    iframe: () => document.querySelector('iframe.studio-embed-frame') as HTMLIFrameElement | null,
  };
}

function subtitleText(): string {
  return document.querySelector('#studio-embed-desc')?.textContent ?? '';
}

function dispatchReady(source: Window | null, origin = STUDIO_ORIGIN): void {
  window.dispatchEvent(
    new MessageEvent('message', {
      data: { type: 'ready' },
      origin,
      source: source as unknown as MessageEventSource | null,
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('StudioEmbed —— 握手接线', () => {
  it('iframe 指向 embed.html（阶段 13：小应用拆成独立版与嵌入版两个入口）', async () => {
    const view = renderEmbed();
    await waitFor(() => expect(view.iframe()).not.toBeNull());

    const src = view.iframe()!.src;
    expect(new URL(src).pathname).toBe('/embed.html');
    // 嵌入所需的两个参数一个都不能少：embedded=1 让小应用知道自己在 iframe 里，
    // parentOrigin 告诉它该把 ready / import-to-main 发给谁。
    expect(src).toContain('embedded=1');
    expect(src).toContain('parentOrigin=');
  });

  it('iframe 挂载后，来自它的 ready 必须能把桥激活（副标题变成"已传入 N 张"）', async () => {
    const view = renderEmbed();

    // iframe 由 Portal 二次渲染后才出现；等它出现是这条用例的前提
    await waitFor(() => expect(view.iframe()).not.toBeNull());

    const iframe = view.iframe();
    expect(iframe).not.toBeNull();
    const contentWindow = iframe!.contentWindow;
    expect(contentWindow).not.toBeNull();

    dispatchReady(contentWindow);

    await waitFor(() => expect(subtitleText()).toContain('已传入'), { timeout: 3000 });
  });

  it('来自错误 origin 的 ready 不得激活桥', async () => {
    const view = renderEmbed();
    await waitFor(() => expect(view.iframe()).not.toBeNull());

    dispatchReady(view.iframe()!.contentWindow, 'https://evil.example');

    // 给足一帧的时间，确认状态没被推动
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(subtitleText()).toContain('正在与小应用握手');
  });

  it('5 秒内收不到 ready → 出现"握手超时"错误卡片', async () => {
    renderEmbed();

    await waitFor(() => expect(document.querySelector('.studio-embed-error')).not.toBeNull(), {
      timeout: 7000,
    });
    const alert = document.querySelector('.studio-embed-error')!;
    expect(alert.textContent).toContain('握手超时');
  }, 12000);
});
