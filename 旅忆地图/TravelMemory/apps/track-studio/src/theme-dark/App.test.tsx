// @vitest-environment jsdom
/** 深色版 App 的**端到端连线测试**（阶段 13 / W13-2）。
 *
 * 只验证一件事：状态机与两条入口真的接上了 ——
 * `landing` →（点「用演示轨迹开始」）→ 演示线列表 →（点一条）→ 播放舞台，
 * 播放舞台里 HUD / 时间轴 / 导出按钮 / 重播都在。
 *
 * 为什么值得一条测试：这条链路上任何一处 import 写错（比如 `demoJourneys` 或
 * `gridMap` 的路径）、任何一个 ref 没接上，都不会有类型错误，只会表现成
 * "点了没反应 / 页面白屏"。单测各组件发现不了。
 *
 * 预期噪音：jsdom 没有 2D 上下文，`new Renderer(canvas)` 会抛
 * `Not implemented: HTMLCanvasElement.prototype.getContext` —— 这正好走到
 * `PlayStage` 的降级分支（显示"当前浏览器不支持 Canvas 2D"），页面结构与控件
 * 仍然完整。所以 stderr 里那一行是**预期输出**，不是失败信号。
 */

import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import App from './App';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('dark App smoke', () => {
  it('landing → 演示轨迹 → 播放（jsdom 里 canvas 不可用也不许崩）', async () => {
    await act(async () => {
      root.render(<App />);
    });
    expect(container.querySelector('.theme-dark')).not.toBeNull();
    expect(container.textContent).toContain('用演示轨迹开始');
    expect(container.textContent).toContain('从系统相册导入');

    const demoButton = Array.from(container.querySelectorAll('button')).find(
      (b) => (b.textContent ?? '').trim() === '用演示轨迹开始',
    );
    await act(async () => {
      demoButton?.click();
    });
    // 演示列表出现
    expect(container.querySelectorAll('.demo-card').length).toBe(2);

    const firstDemo = container.querySelector<HTMLButtonElement>('.demo-card');
    await act(async () => {
      firstDemo?.click();
    });
    // 进入播放页：时间轴与控制都在
    expect(container.querySelector('.play')).not.toBeNull();
    expect(container.querySelector('.timeline')).not.toBeNull();
    expect(container.querySelector('.hud')).not.toBeNull();
    expect(container.textContent).toContain('导出视频');
    expect(container.textContent).toContain('重播');
  });
});
