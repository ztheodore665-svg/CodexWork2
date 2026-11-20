/** 缩略图生成：**纯逻辑 + 依赖注入**，与 DOM 解耦。
 *
 * ## 为什么必须能注入 canvas
 *
 * jsdom 没有实现 canvas 2D：`canvas.getContext('2d')` 返回 `null`。
 * 如果这里写死 `document.createElement('canvas')`，这个包的单测就只能全部 `test.skip`。
 * 所以 canvas 工厂走依赖注入：生产环境缺省用真实 `<canvas>`，测试注入假 canvas，
 * 于是"循环降质到 ≤ 200KB"这段真正的逻辑能被测到，而不是靠人肉点浏览器。
 *
 * ## 为什么失败也不能抛
 *
 * 缩略图是"锦上添花"：没有它照片仍然能用于轨迹回放，只是导出时进不了 `journey.json`。
 * 为了一张图让整个导照片流程失败（用户以为文件选不上）是更糟的取舍 ——
 * 所以任何一步不可用都退化成内置的 1×1 PNG。
 */

import { MAX_THUMBNAIL_BYTES } from './types';

/** 缩略图长边上限（B2）。 */
export const THUMBNAIL_MAX_EDGE = 480;
/** 起始 JPEG 质量（B2）。 */
export const THUMBNAIL_START_QUALITY = 0.7;
/** 质量下界：再降画面就没法看了，宁可超一点也别出马赛克。 */
export const THUMBNAIL_MIN_QUALITY = 0.3;
/** 每次降质的步长。 */
export const THUMBNAIL_QUALITY_STEP = 0.1;

/** 1×1 透明 PNG（68 字节）。canvas 不可用时的兜底，保证 `thumbnailDataUrl` 永远可渲染。 */
export const FALLBACK_THUMBNAIL_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=';

/** 注入用 canvas 形状。刻意只声明用到的方法，不追求 `HTMLCanvasElement` 全量兼容。 */
export interface CanvasLike {
  width: number;
  height: number;
  getContext(contextId: '2d'): Canvas2DContextLike | null;
  toDataURL(type?: string, quality?: number): string;
}

/** 注入用 2D 上下文形状。 */
export interface Canvas2DContextLike {
  imageSmoothingEnabled: boolean;
  imageSmoothingQuality: string;
  drawImage(image: unknown, dx: number, dy: number, dw: number, dh: number): void;
}

/** 可被 `drawImage` 消费的源。`<img>`、`ImageBitmap`、`<canvas>` 都天然满足。 */
export interface ThumbnailSourceLike {
  readonly width?: number;
  readonly height?: number;
  readonly naturalWidth?: number;
  readonly naturalHeight?: number;
}

export interface ThumbnailDeps {
  /** canvas 工厂。返回 `null` = 当前环境没有 canvas（jsdom、老浏览器）。 */
  createCanvas?: () => CanvasLike | null;
  /** 长边上限，缺省 `THUMBNAIL_MAX_EDGE`。 */
  maxEdge?: number;
  /** 字节上限，缺省 `MAX_THUMBNAIL_BYTES`。 */
  maxBytes?: number;
  /** 起始质量，缺省 `THUMBNAIL_START_QUALITY`。 */
  startQuality?: number;
  /** 质量下界，缺省 `THUMBNAIL_MIN_QUALITY`。 */
  minQuality?: number;
  /** 质量步长，缺省 `THUMBNAIL_QUALITY_STEP`。 */
  qualityStep?: number;
}

/** data URL 解码后的字节数（估算，误差 0）。
 *
 * 只用于和 200KB 比较，所以不真去 `atob`：base64 每 4 字符对应 3 字节，
 * 减去 padding 就是精确值，成本却是 O(1)。
 */
export function dataUrlByteLength(dataUrl: string): number {
  const comma = dataUrl.indexOf(',');
  if (comma < 0) return 0;
  const meta = dataUrl.slice(0, comma);
  const payload = dataUrl.slice(comma + 1).replace(/\s+/g, '');
  if (!meta.includes(';base64')) return payload.length;
  if (payload.length === 0) return 0;
  let padding = 0;
  if (payload.endsWith('==')) padding = 2;
  else if (payload.endsWith('=')) padding = 1;
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

/**
 * 规划缩略图尺寸：等比缩放到长边不超过 `maxEdge`，不放大。
 *
 * @param width 原图宽（px）
 * @param height 原图高（px）
 * @param maxEdge 长边上限，缺省 480
 * @returns 目标尺寸，两个方向都 ≥ 1 的整数。非法输入（0 / 负数 / NaN / Infinity）返回 `{1,1}`。
 */
export function planThumbnailSize(
  width: number,
  height: number,
  maxEdge: number = THUMBNAIL_MAX_EDGE,
): { width: number; height: number } {
  const validEdge =
    typeof maxEdge === 'number' && Number.isFinite(maxEdge) && maxEdge > 0
      ? Math.floor(maxEdge)
      : THUMBNAIL_MAX_EDGE;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return { width: 1, height: 1 };
  }
  const longEdge = Math.max(width, height);
  if (longEdge <= validEdge) {
    return { width: Math.max(1, Math.round(width)), height: Math.max(1, Math.round(height)) };
  }
  const scale = validEdge / longEdge;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** 从图片源读出尺寸：`<img>` 用 `naturalWidth`，`ImageBitmap` / `<canvas>` 用 `width`。 */
export function getSourceSize(source: ThumbnailSourceLike): { width: number; height: number } {
  const width = source.naturalWidth ?? source.width ?? 0;
  const height = source.naturalHeight ?? source.height ?? 0;
  return {
    width: Number.isFinite(width) && width > 0 ? width : 0,
    height: Number.isFinite(height) && height > 0 ? height : 0,
  };
}

/** 缺省 canvas 工厂：真实浏览器里建一个游离 canvas；没有 `document` 时返回 `null`。 */
function defaultCreateCanvas(): CanvasLike | null {
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  return document.createElement('canvas') as unknown as CanvasLike;
}

/** 安全地拿 canvas 与 2D 上下文；任何一步失败都给 `null`（不抛）。 */
function acquireCanvas(
  deps: ThumbnailDeps,
): { canvas: CanvasLike; context: Canvas2DContextLike } | null {
  const createCanvas = deps.createCanvas ?? defaultCreateCanvas;
  let canvas: CanvasLike | null = null;
  try {
    canvas = createCanvas();
  } catch {
    return null;
  }
  if (!canvas) return null;
  let context: Canvas2DContextLike | null = null;
  try {
    context = canvas.getContext('2d');
  } catch {
    return null;
  }
  if (!context) return null;
  return { canvas, context };
}

/**
 * 把图片源压成一张缩略图 data URL（JPEG，长边 ≤ 480，解码后 ≤ 200KB）。
 *
 * 降质策略（B2）：从 `q=0.7` 起，每次 `-0.1`，直到字节数达标或 `q < 0.3`。
 * 到 `q=0.3` 仍超标时返回当时最小的那张（**不抛错**，也不返回空串）——
 * 一张略大的缩略图总比没有强，导出侧会按上限再筛一次。
 *
 * @param source 图片源（`<img>` / `ImageBitmap` / `<canvas>`）
 * @param deps 依赖注入（canvas 工厂、上限、质量参数）
 * @returns data URL；canvas 不可用或绘制失败时返回 `FALLBACK_THUMBNAIL_DATA_URL`。
 */
export async function compressToThumbnail(
  source: ThumbnailSourceLike,
  deps: ThumbnailDeps = {},
): Promise<string> {
  const acquired = acquireCanvas(deps);
  if (!acquired) return FALLBACK_THUMBNAIL_DATA_URL;
  const { canvas, context } = acquired;

  const maxEdge = deps.maxEdge ?? THUMBNAIL_MAX_EDGE;
  const maxBytes = deps.maxBytes ?? MAX_THUMBNAIL_BYTES;
  const startQuality = deps.startQuality ?? THUMBNAIL_START_QUALITY;
  const minQuality = deps.minQuality ?? THUMBNAIL_MIN_QUALITY;
  const step = deps.qualityStep ?? THUMBNAIL_QUALITY_STEP;

  const sourceSize = getSourceSize(source);
  const target = planThumbnailSize(sourceSize.width, sourceSize.height, maxEdge);

  canvas.width = target.width;
  canvas.height = target.height;
  try {
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(source, 0, 0, target.width, target.height);
  } catch {
    return FALLBACK_THUMBNAIL_DATA_URL;
  }

  let best = '';
  let quality = startQuality;
  // 用 -1e-9 容忍 0.7-0.1*4 = 0.29999999999999993 这类浮点误差，否则 q=0.3 那一档会被跳过。
  while (quality >= minQuality - 1e-9) {
    let dataUrl = '';
    try {
      dataUrl = canvas.toDataURL('image/jpeg', quality);
    } catch {
      dataUrl = '';
    }
    if (dataUrl.length === 0) return best.length > 0 ? best : FALLBACK_THUMBNAIL_DATA_URL;
    best = dataUrl;
    if (dataUrlByteLength(dataUrl) <= maxBytes) return dataUrl;
    quality = Math.round((quality - step) * 100) / 100;
  }
  return best.length > 0 ? best : FALLBACK_THUMBNAIL_DATA_URL;
}
