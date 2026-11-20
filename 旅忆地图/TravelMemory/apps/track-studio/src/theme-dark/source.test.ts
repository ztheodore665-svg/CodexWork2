/** 深色主题的**源码契约**测试（node 环境，不跑组件）。
 *
 * 这里放两类断言：
 *
 *  1. `ui/Timeline.tsx` 的四条性能契约 —— **从 `showcase/src/timeline.test.ts` 复制**
 *     （断言逐条保留：`draw()` 里不许出现 `getBoundingClientRect`、必须用缓存的
 *     `sizeRef.current`、尺寸测量搬到了 `ResizeObserver`、交互取坐标仍按真实位置读一次）。
 *
 *     为什么不在 `ui/Timeline.test.tsx` 里做：那份测试跑在 `jsdom` 环境，
 *     `import.meta.url` 是 `http://localhost/...`，而 `fs.readFileSync` 只接受
 *     `file:` URL —— 实测报 `The URL must be of scheme file`。所以「读源码」的断言
 *     只能留在 node 环境（`vitest` 默认），组件行为断言留在 jsdom 文件里。
 *
 *  2. 卡片层与时间轴的**同源关系**：两个组件都是从 showcase 复制的，
 *     一旦有人删掉 `forwardRef` / `useImperativeHandle`，主循环就拿不到句柄，
 *     表现是"卡片永远不动、时间轴永远不画"，而 React 不会报错。
 */

import { beforeAll, describe, expect, it } from 'vitest';

interface FsLike {
  readFileSync(target: URL | string, encoding: string): string;
}

let read: (relative: string) => string = () => '';

beforeAll(async () => {
  const specifier = ['node', 'fs'].join(':');
  const fs = (await import(/* @vite-ignore */ specifier)) as unknown as FsLike;
  read = (relative: string): string =>
    fs.readFileSync(new URL(relative, import.meta.url), 'utf8');
});

function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/[^\n]*/g, '$1');
}

/** 抽出 `const draw = useCallback(` 到 `useImperativeHandle` 之间的函数体。 */
function drawBody(source: string): string {
  const start = source.indexOf('const draw = useCallback(');
  const end = source.indexOf('useImperativeHandle(ref', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return stripComments(source.slice(start, end));
}

describe('时间轴每帧绘制（showcase 契约，逐条保留）', () => {
  it('draw() 里不读布局（不调 getBoundingClientRect）', () => {
    expect(drawBody(read('./ui/Timeline.tsx'))).not.toContain('getBoundingClientRect');
  });

  it('draw() 用缓存尺寸，且首帧未测量时安全跳过', () => {
    const body = drawBody(read('./ui/Timeline.tsx'));
    expect(body).toContain('sizeRef.current');
    expect(body).toContain('if (width <= 0) return;');
  });

  it('尺寸测量搬到了 ResizeObserver，且带无 ResizeObserver 的降级', () => {
    const source = read('./ui/Timeline.tsx');
    expect(source).toContain('new ResizeObserver(');
    expect(source).toContain("typeof ResizeObserver === 'undefined'");
  });

  it('交互取坐标仍按真实位置读一次（pointer 事件，不是每帧）', () => {
    const source = read('./ui/Timeline.tsx');
    expect(source).toContain('qFromClientX');
    const at = source.indexOf('const qFromClientX');
    expect(stripComments(source.slice(at, at + 400))).toContain('getBoundingClientRect');
  });
});

describe('两个组件都暴露命令式句柄（主循环每帧要用）', () => {
  it('Timeline 暴露 draw(q)', () => {
    const source = read('./ui/Timeline.tsx');
    expect(source).toContain('export interface TimelineHandle');
    expect(source).toContain('draw(q: number): void');
    expect(source).toContain('useImperativeHandle(ref');
  });

  it('PhotoCards 暴露 update(...) 与 anchors', () => {
    const source = read('./ui/PhotoCards.tsx');
    expect(source).toContain('export interface PhotoCardsHandle');
    expect(source).toContain('readonly anchors: Float32Array');
    expect(source).toContain('useImperativeHandle(');
  });

  it('Hud 暴露 update(...)（只在值变化时写 DOM）', () => {
    const source = read('./ui/Hud.tsx');
    expect(source).toContain('export interface HudHandle');
    expect(source).toContain('if (el && el.textContent !== text)');
  });
});
