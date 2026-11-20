// @vitest-environment jsdom
/** 时间轴的测试（阶段 13 / W13-2：从 `showcase/src/timeline.test.ts` 移植并扩写）。
 *
 * showcase 那份是**纯源码契约**测试（读 `Timeline.tsx` 的文本，断言 `draw()` 里
 * 不出现 `getBoundingClientRect`）。这里保留那几条，同时补上**真跑组件**的用例：
 *
 *  1. 四个倍速按钮（0.5x / 1x / 2x / 4x）与「重播」都在，点击会回调；
 *  2. 播放按钮直接驱动 `Clock`（`playing` 翻转）；
 *  3. `draw(q)` 每帧被调用，且**一次布局都不读** —— 这条用行为断言（统计
 *     `getBoundingClientRect` 调用次数），比读源码更硬；
 *  4. 尺寸测量在挂载期完成（jsdom 没有 ResizeObserver，走 window.resize 降级）。
 *
 * 为什么能跑：`jsdom` 没有 2D 上下文，所以这里给
 * `HTMLCanvasElement.prototype.getContext` 打一个记录调用的桩，
 * 断言"画了"而不是"画成什么样"（像素正确性属于视觉走查）。
 */

import { createRef } from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { buildJourney, Clock, type Journey, type Photo } from '@travelmemory/trail';

import { Timeline, type TimelineHandle } from './Timeline';

/* ── 一个只记录调用的 2D 上下文桩 ─────────────────────────────── */

interface RecordedContext {
  calls: string[];
  gradientStops: number;
}

function installCanvasStub(): { context: RecordedContext; rectCalls: () => number } {
  const recorded: RecordedContext = { calls: [], gradientStops: 0 };
  const gradient = {
    addColorStop: (): void => {
      recorded.gradientStops += 1;
    },
  };
  const noop =
    (name: string) =>
    (): void => {
      recorded.calls.push(name);
    };
  const context = {
    // 状态
    lineCap: 'butt',
    lineWidth: 1,
    strokeStyle: '' as unknown,
    fillStyle: '' as unknown,
    font: '',
    textBaseline: 'alphabetic',
    // 方法
    setTransform: noop('setTransform'),
    clearRect: noop('clearRect'),
    save: noop('save'),
    restore: noop('restore'),
    beginPath: noop('beginPath'),
    moveTo: noop('moveTo'),
    lineTo: noop('lineTo'),
    stroke: noop('stroke'),
    fill: noop('fill'),
    arc: noop('arc'),
    fillText: noop('fillText'),
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
  };

  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => context as unknown as CanvasRenderingContext2D,
  );

  let rects = 0;
  vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockImplementation(() => {
    rects += 1;
    return {
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 800,
      bottom: 68,
      width: 800,
      height: 68,
      toJSON: () => ({}),
    } as DOMRect;
  });

  return { context: recorded, rectCalls: () => rects };
}

/** 4 张跨两天的照片（时间轴要画天分界刻度与照片刻度）。 */
function photosFixture(): Photo[] {
  const base = Date.UTC(2026, 3, 18, 1, 0, 0);
  return [
    { id: 'a', lat: 30.2431, lng: 120.1459, takenAt: base, placeName: '断桥', locationSource: 'exif' },
    {
      id: 'b',
      lat: 30.2408,
      lng: 120.0985,
      takenAt: base + 3 * 3600 * 1000,
      placeName: '灵隐寺',
      locationSource: 'exif',
    },
    {
      id: 'c',
      lat: 30.1728,
      lng: 120.1009,
      takenAt: base + 30 * 3600 * 1000,
      placeName: '宋城',
      locationSource: 'exif',
    },
    {
      id: 'd',
      lat: 30.1878,
      lng: 120.144,
      takenAt: base + 34 * 3600 * 1000,
      placeName: '之江路',
      locationSource: 'exif',
    },
  ];
}

const JOURNEY: Journey = buildJourney({
  id: 'dark-timeline',
  title: '时间轴测试',
  subtitle: '4 张',
  photos: photosFixture(),
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // React 18 要求显式声明"当前是 act 环境"，否则每个 act 都打一条警告
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
  vi.restoreAllMocks();
});

/** 挂载并返回（可空的）命令式句柄。 */
async function mountTimeline(
  onSpeedChange: (speed: number) => void = () => undefined,
  onReplay: () => void = () => undefined,
): Promise<{ ref: { current: TimelineHandle | null }; clock: Clock }> {
  const ref = createRef<TimelineHandle>();
  const clock = new Clock();
  await act(async () => {
    root.render(
      <Timeline
        ref={ref}
        journey={JOURNEY}
        clock={clock}
        playing={false}
        speed={1}
        onSpeedChange={onSpeedChange}
        onReplay={onReplay}
      />,
    );
  });
  return { ref: ref as { current: TimelineHandle | null }, clock };
}

function buttonByText(text: string): HTMLButtonElement | null {
  const buttons = Array.from(container.querySelectorAll('button'));
  return buttons.find((button) => (button.textContent ?? '').trim() === text) ?? null;
}

describe('Timeline —— 交互', () => {
  it('四个倍速按钮与「重播」都在', async () => {
    await mountTimeline();
    for (const label of ['0.5x', '1x', '2x', '4x', '重播']) {
      expect(buttonByText(label)).not.toBeNull();
    }
  });

  it('点倍速按钮会回调（2x）', async () => {
    const onSpeedChange = vi.fn();
    await mountTimeline(onSpeedChange);

    await act(async () => {
      buttonByText('2x')?.click();
    });
    expect(onSpeedChange).toHaveBeenCalledWith(2);
  });

  it('点重播会回调', async () => {
    const onReplay = vi.fn();
    await mountTimeline(() => undefined, onReplay);

    await act(async () => {
      buttonByText('重播')?.click();
    });
    expect(onReplay).toHaveBeenCalledTimes(1);
  });

  it('播放按钮直接驱动 Clock（playing 翻转）', async () => {
    const { clock } = await mountTimeline();
    expect(clock.playing).toBe(false);

    const playButton = container.querySelector('button[aria-label]');
    expect(playButton).not.toBeNull();

    await act(async () => {
      (playButton as HTMLButtonElement).click();
    });
    expect(clock.playing).toBe(true);
  });
});

describe('Timeline —— 每帧绘制', () => {
  it('draw(q) 会真的画（清屏 + 描边），且不读布局', async () => {
    const stub = installCanvasStub();
    const { ref } = await mountTimeline();

    // 挂载期读过一次布局（量画布宽度），之后每帧都不许再读
    const before = stub.rectCalls();

    await act(async () => {
      ref.current?.draw(0.4);
    });

    expect(stub.context.calls).toContain('clearRect');
    expect(stub.context.calls).toContain('stroke');
    // 性能契约：draw() 里一次 getBoundingClientRect 都没有（否则每帧强制同步重排）
    expect(stub.rectCalls()).toBe(before);
  });

  it('q=0 与 q=1 都不抛（端点夹取）', async () => {
    const stub = installCanvasStub();
    const { ref } = await mountTimeline();

    await act(async () => {
      ref.current?.draw(0);
      ref.current?.draw(1);
      ref.current?.draw(-3);
      ref.current?.draw(9);
    });
    // 越界 q 被夹到 [0,1]：仍然每一帧都清了屏
    expect(stub.context.calls.filter((name) => name === 'clearRect').length).toBeGreaterThanOrEqual(4);
  });
});

/* ── 源码契约去哪了 ─────────────────────────────────────────── */
/* showcase 的 `timeline.test.ts` 是"读源码"的断言（`draw()` 里不许出现
 * `getBoundingClientRect` 等）。**那几条逐字保留在 `theme-dark/source.test.ts`**：
 * 本文件跑在 jsdom 环境，`import.meta.url` 是 http(s) 的，`fs.readFileSync`
 * 只接受 `file:` URL，所以"读源码"只能放 node 环境。
 * 本文件用行为断言把同一条契约又钉了一遍（统计 `getBoundingClientRect` 调用次数）。 */
