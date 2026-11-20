import { afterEach, describe, expect, it, vi } from 'vitest';
import { Renderer } from './renderer';

/**
 * P0 回归网：canvas 尺寸自锁。
 *
 * 背景（真实发生过的线上级问题）：`.play-canvas` 只有 `position:absolute; inset:0`、
 * 没有 `width/height`。canvas 是替换元素，`width:auto` 会按**固有尺寸**（默认 300×150）
 * 解析，`inset:0` 的 right/bottom 因过约束被忽略 —— 而固有尺寸又被渲染器写回的
 * `canvas.width` 属性反向决定。于是 `resize()` 量自己 → 写自己 → 永远长不大；
 * dpr≥2 时更糟：属性翻倍 → 固有尺寸翻倍 → 下次量到更大值 → 再翻倍，
 * 每触发一次 window resize 就指数膨胀。
 *
 * 修复的两条契约，本文件逐条锁死：
 *  1. `resize()` 必须量**父容器**，不得量 canvas 自身（下面的桩会给 canvas 自己的
 *     `getBoundingClientRect` 装上断言，一旦被调用即测试失败）；
 *  2. 父容器尺寸不变时 `resize()` 必须**幂等**（返回 false、不再改属性），
 *     连续调用不得让画布膨胀。
 *
 * 环境说明：`vite.config.ts` 是 `environment: 'node'`，没有 jsdom / 布局引擎，
 * 所以这里用最小桩替换 canvas、并用 `vi.stubGlobal` 提供 `window.devicePixelRatio`
 * 与 `window.innerWidth/innerHeight`（`resize()` 的 DEV 护栏会读它们）。
 */

interface HostBox {
  width: number;
  height: number;
}

function makeRect(width: number, height: number): DOMRect {
  return {
    width,
    height,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as unknown as DOMRect;
}

/**
 * @param host 父容器尺寸；传 null 表示 canvas 尚未挂载（parentElement 为 null）
 */
function makeCanvas(host: HostBox | null, selfSize: HostBox = { width: 300, height: 150 }) {
  return {
    width: 300,
    height: 150,
    getContext: () => ({}),
    getBoundingClientRect: (): DOMRect => {
      if (host) {
        throw new Error('P0 回归：resize() 量了 canvas 自身，应量父容器');
      }
      return makeRect(selfSize.width, selfSize.height);
    },
    parentElement: host ? { getBoundingClientRect: () => makeRect(host.width, host.height) } : null,
  };
}

function setViewport(width: number, height: number, devicePixelRatio = 2): void {
  vi.stubGlobal('window', { devicePixelRatio, innerWidth: width, innerHeight: height });
}

function instantiate(canvas: ReturnType<typeof makeCanvas>): Renderer {
  return new Renderer(canvas as unknown as HTMLCanvasElement);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Renderer.resize 尺寸契约', () => {
  it('量父容器：1280x720 的视口 → width/height 1280/720，dpr 上限 1.5 时属性 1920x1080', () => {
    setViewport(1280, 720);
    const canvas = makeCanvas({ width: 1280, height: 720 });
    const renderer = instantiate(canvas);

    expect(renderer.width).toBe(1280);
    expect(renderer.height).toBe(720);
    expect(renderer.dpr).toBe(1.5);
    expect(canvas.width).toBe(1920);
    expect(canvas.height).toBe(1080);
  });

  it('父容器尺寸不变时 resize() 幂等（不得膨胀）', () => {
    setViewport(1280, 720);
    const canvas = makeCanvas({ width: 1280, height: 720 });
    const renderer = instantiate(canvas);
    const attrWidth = canvas.width;
    const attrHeight = canvas.height;

    // 连续 10 次模拟 window resize 事件：曾经每次都会把属性翻倍
    for (let i = 0; i < 10; i += 1) {
      expect(renderer.resize()).toBe(false);
    }

    expect(renderer.width).toBe(1280);
    expect(renderer.height).toBe(720);
    expect(canvas.width).toBe(attrWidth);
    expect(canvas.height).toBe(attrHeight);
  });

  it('父容器变大后会重新测量（尺寸能跟上来）', () => {
    setViewport(1280, 720);
    const host: HostBox = { width: 1280, height: 720 };
    const canvas = makeCanvas(host);
    const renderer = instantiate(canvas);
    expect(renderer.width).toBe(1280);

    host.width = 1600;
    host.height = 900;
    setViewport(1600, 900);

    expect(renderer.resize()).toBe(true);
    expect(renderer.width).toBe(1600);
    expect(renderer.height).toBe(900);
    expect(canvas.width).toBe(2400);
    expect(canvas.height).toBe(1350);
  });

  it('父容器缺失（尚未挂载）时回退量自身，不抛错', () => {
    setViewport(640, 480);
    const canvas = makeCanvas(null, { width: 640, height: 480 });
    const renderer = instantiate(canvas);

    expect(renderer.width).toBe(640);
    expect(renderer.height).toBe(480);
    expect(canvas.width).toBe(960);
    expect(canvas.height).toBe(720);
  });

  it('dpr 上限为 1.5（3x 屏只按 1.5 倍渲染，控制填充率）', () => {
    setViewport(1000, 500, 3);
    const canvas = makeCanvas({ width: 1000, height: 500 });
    const renderer = instantiate(canvas);

    expect(renderer.dpr).toBe(1.5);
    expect(canvas.width).toBe(1500);
    expect(canvas.height).toBe(750);
  });

  it('父容器尺寸为 0（塌陷）时回退到 window.innerWidth/innerHeight', () => {
    setViewport(1280, 720);
    const canvas = makeCanvas({ width: 0, height: 0 });
    const renderer = instantiate(canvas);

    // rect.width 为 0 属 falsy，resize() 会退到视口尺寸，而不是把画布写成 0
    expect(renderer.width).toBe(1280);
    expect(renderer.height).toBe(720);
  });
});
