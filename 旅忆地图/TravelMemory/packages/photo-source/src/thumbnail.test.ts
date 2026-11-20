import { describe, expect, it, vi } from 'vitest';

import {
  FALLBACK_THUMBNAIL_DATA_URL,
  THUMBNAIL_MAX_EDGE,
  compressToThumbnail,
  dataUrlByteLength,
  planThumbnailSize,
  type Canvas2DContextLike,
  type CanvasLike,
  type ThumbnailSourceLike,
} from './thumbnail';
import { MAX_THUMBNAIL_BYTES } from './types';

const SMALL_JPEG = `data:image/jpeg;base64,${'A'.repeat(400)}`; // ≈300 字节
const BIG_JPEG = `data:image/jpeg;base64,${'A'.repeat(280_000)}`; // ≈210000 字节 > 200KB

/** 假 canvas：记录尺寸、绘制参数与每次 `toDataURL` 的质量。 */
function createFakeCanvas(options: {
  urls?: string[];
  context?: Canvas2DContextLike | null;
  throwOnDraw?: boolean;
} = {}): {
  createCanvas: () => CanvasLike;
  drawnSizes: Array<{ width: number; height: number }>;
  qualities: number[];
  canvasSizes: Array<{ width: number; height: number }>;
} {
  const urls = options.urls ?? [SMALL_JPEG];
  const drawnSizes: Array<{ width: number; height: number }> = [];
  const qualities: number[] = [];
  const canvasSizes: Array<{ width: number; height: number }> = [];
  let callIndex = 0;

  const context: Canvas2DContextLike | null =
    options.context === undefined
      ? {
          imageSmoothingEnabled: false,
          imageSmoothingQuality: 'low',
          drawImage: (_image: unknown, _dx: number, _dy: number, dw: number, dh: number) => {
            if (options.throwOnDraw) throw new Error('drawImage failed');
            drawnSizes.push({ width: dw, height: dh });
          },
        }
      : options.context;

  const canvas: CanvasLike = {
    width: 0,
    height: 0,
    getContext: () => context,
    toDataURL: (_type?: string, quality?: number) => {
      qualities.push(quality ?? 1);
      const url = urls[Math.min(callIndex, urls.length - 1)];
      callIndex += 1;
      return url;
    },
  };

  const createCanvas = (): CanvasLike => {
    // 记录调用方最终设置的尺寸（compressToThumbnail 在拿到 canvas 后才赋值）
    return new Proxy(canvas, {
      set(target, property, value) {
        if (property === 'width' || property === 'height') {
          const next = { width: target.width, height: target.height };
          if (property === 'width') next.width = Number(value);
          else next.height = Number(value);
          canvasSizes.push(next);
        }
        return Reflect.set(target, property, value);
      },
    });
  };

  return { createCanvas, drawnSizes, qualities, canvasSizes };
}

/** 一个 1920×1080 的"图片源"。 */
function source(width: number, height: number): ThumbnailSourceLike {
  return { naturalWidth: width, naturalHeight: height, width, height };
}

describe('planThumbnailSize', () => {
  it('横图按长边缩到 480，保持比例', () => {
    expect(planThumbnailSize(1920, 1080, 480)).toEqual({ width: 480, height: 270 });
  });

  it('竖图按高边缩到 480，保持比例', () => {
    expect(planThumbnailSize(1080, 1920, 480)).toEqual({ width: 270, height: 480 });
  });

  it('小图不放大', () => {
    expect(planThumbnailSize(100, 60, 480)).toEqual({ width: 100, height: 60 });
  });

  it('缺省 maxEdge 是 480（B2）', () => {
    expect(THUMBNAIL_MAX_EDGE).toBe(480);
    expect(planThumbnailSize(960, 960)).toEqual({ width: 480, height: 480 });
  });

  it('长边永不超过 maxEdge', () => {
    for (const [width, height] of [
      [4000, 3000],
      [3001, 2999],
      [999, 1000],
      [1, 5000],
      [5000, 1],
    ]) {
      const size = planThumbnailSize(width, height, 480);
      expect(Math.max(size.width, size.height)).toBeLessThanOrEqual(480);
      expect(size.width).toBeGreaterThanOrEqual(1);
      expect(size.height).toBeGreaterThanOrEqual(1);
    }
  });

  it('非法输入返回 1×1 而不是 NaN', () => {
    expect(planThumbnailSize(0, 0, 480)).toEqual({ width: 1, height: 1 });
    expect(planThumbnailSize(-10, 20, 480)).toEqual({ width: 1, height: 1 });
    expect(planThumbnailSize(Number.NaN, 100, 480)).toEqual({ width: 1, height: 1 });
    expect(planThumbnailSize(100, Number.POSITIVE_INFINITY, 480)).toEqual({ width: 1, height: 1 });
    // maxEdge 非法时退回缺省 480
    expect(planThumbnailSize(1920, 1080, 0)).toEqual({ width: 480, height: 270 });
  });
});

describe('dataUrlByteLength', () => {
  it('按 base64 长度精确估算解码字节数', () => {
    expect(dataUrlByteLength(`data:image/jpeg;base64,${'A'.repeat(400)}`)).toBe(300);
    expect(dataUrlByteLength('data:image/jpeg;base64,')).toBe(0);
    expect(dataUrlByteLength('no-comma')).toBe(0);
  });
});

describe('compressToThumbnail', () => {
  it('一次就达标时返回 data:image/ 且解码后 ≤ 200KB', async () => {
    const fake = createFakeCanvas({ urls: [SMALL_JPEG] });
    const url = await compressToThumbnail(source(1920, 1080), { createCanvas: fake.createCanvas });

    expect(url.startsWith('data:image/')).toBe(true);
    expect(dataUrlByteLength(url)).toBeLessThanOrEqual(MAX_THUMBNAIL_BYTES);
    expect(fake.qualities).toEqual([0.7]);
    expect(fake.drawnSizes).toEqual([{ width: 480, height: 270 }]);
  });

  it('超限时按 q 0.7 → 0.6 → 0.5 逐档降质，直到达标', async () => {
    const fake = createFakeCanvas({ urls: [BIG_JPEG, BIG_JPEG, SMALL_JPEG] });
    const url = await compressToThumbnail(source(800, 600), { createCanvas: fake.createCanvas });

    expect(fake.qualities.map((quality) => Number(quality.toFixed(2)))).toEqual([0.7, 0.6, 0.5]);
    expect(url).toBe(SMALL_JPEG);
  });

  it('一直超限时停在 q≈0.3 并返回最小的那张，不抛错', async () => {
    const fake = createFakeCanvas({ urls: [BIG_JPEG] });
    const url = await compressToThumbnail(source(800, 600), { createCanvas: fake.createCanvas });

    expect(fake.qualities.length).toBe(5); // 0.7 / 0.6 / 0.5 / 0.4 / 0.3
    expect(Number(fake.qualities[fake.qualities.length - 1].toFixed(2))).toBe(0.3);
    expect(url.startsWith('data:image/')).toBe(true);
  });

  it('canvas 不可用（无 document / 工厂返回 null）时返回内置 1×1 PNG', async () => {
    const url = await compressToThumbnail(source(800, 600), { createCanvas: () => null });
    expect(url).toBe(FALLBACK_THUMBNAIL_DATA_URL);
    expect(dataUrlByteLength(url)).toBeGreaterThan(0);
  });

  it('canvas 工厂抛错时返回兜底常量', async () => {
    const url = await compressToThumbnail(source(800, 600), {
      createCanvas: () => {
        throw new Error('boom');
      },
    });
    expect(url).toBe(FALLBACK_THUMBNAIL_DATA_URL);
  });

  it('getContext 返回 null（jsdom 的真实行为）时返回兜底常量', async () => {
    const fake = createFakeCanvas({ context: null });
    const url = await compressToThumbnail(source(800, 600), { createCanvas: fake.createCanvas });
    expect(url).toBe(FALLBACK_THUMBNAIL_DATA_URL);
  });

  it('drawImage 抛错时返回兜底常量', async () => {
    const fake = createFakeCanvas({ throwOnDraw: true });
    const url = await compressToThumbnail(source(800, 600), { createCanvas: fake.createCanvas });
    expect(url).toBe(FALLBACK_THUMBNAIL_DATA_URL);
  });

  it('jsdom 下不注入 canvas 也不抛错（真实环境降级路径）', async () => {
    // jsdom 有 document.createElement('canvas')，但 getContext('2d') 内部会抛
    // "Not implemented" —— 这正是老浏览器/受限环境的降级路径，必须不抛错。
    // 把 jsdom virtualConsole 打到 stderr 的噪音静音，避免污染测试输出。
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const url = await compressToThumbnail(source(1920, 1080));
      expect(url.startsWith('data:image/')).toBe(true);
    } finally {
      errorSpy.mockRestore();
    }
  });

  it('按注入的 maxEdge 缩放', async () => {
    const fake = createFakeCanvas({ urls: [SMALL_JPEG] });
    await compressToThumbnail(source(2000, 1000), {
      createCanvas: fake.createCanvas,
      maxEdge: 200,
    });
    expect(fake.drawnSizes).toEqual([{ width: 200, height: 100 }]);
  });
});
