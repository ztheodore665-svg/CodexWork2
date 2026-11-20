/** 地图上"画什么、画在哪"的**纯逻辑**（W12-4）。
 *
 * ## 为什么要单独一个文件
 *
 * `PlaceMap.tsx` 一 import 就会摸 `window.AMap`，在 Node（vitest）里跑不起来。
 * 而"哪个地点该画圈、哪个该画点、没有坐标的要不要出现"以及"降级格网上每个点落在
 * 哪个像素"这两件事是**决策**，必须有测试锁死：
 *  - 画错形状不会报错，只会让人觉得"这里怎么没有圆"；
 *  - 点落到 padding 外面（=被裁掉）也不会报错，只会少一个点。
 *
 * ## 坐标系（重要）
 *
 * 进来的 `lat/lng` 是 **WGS84**（协议里存的、后端模糊后的值）。
 *  - `planPlaceOverlays`：**不做转换**，原样吐出 WGS84 —— 转 GCJ-02 是 `PlaceMap`
 *    在给高德之前做的（降级示意不需要转，转了反而错）。
 *  - `projectPlaces`：等距圆柱近似，纯示意，两套坐标系对它没有意义。
 */

import type { UnpackedPlace } from '../types/share';

/** 覆盖物形状：带半径的圈 / 一个点。 */
export type OverlayKind = 'circle' | 'marker';

/** 一个要在图上画出来的地点。 */
export interface PlaceOverlay {
  placeId: string;
  kind: OverlayKind;
  /** WGS84 */
  lat: number;
  lng: number;
  /** 米。`marker` 恒为 0。 */
  radius: number;
}

/** 一个投影后的屏幕点（像素，相对 SVG/画布左上角）。 */
export interface ProjectedPoint {
  placeId: string;
  x: number;
  y: number;
}

/** 坐标是否可用（两边都必须是有限数且在合法范围内）。 */
function hasCoords(place: UnpackedPlace): boolean {
  const { lat, lng } = place;
  if (typeof lat !== 'number' || !Number.isFinite(lat)) return false;
  if (typeof lng !== 'number' || !Number.isFinite(lng)) return false;
  if (lat < -90 || lat > 90) return false;
  if (lng < -180 || lng > 180) return false;
  return true;
}

/**
 * 决定每个地点画什么。
 *
 * 规则（与 `UnpackedPlace` 的可选字段一一对应）：
 *  - `radius > 0` → `circle`（承诺半径，**不是**本次实际偏移量）；
 *  - 有坐标但没有正半径 → `marker`（只知道大概位置，画不出圈）；
 *  - 没有坐标 → **不出现在结果里**（绝不猜位置，也绝不落到 (0,0)）。
 *
 * @param places 解包出来的地点（顺序保持）
 */
export function planPlaceOverlays(places: readonly UnpackedPlace[]): PlaceOverlay[] {
  const out: PlaceOverlay[] = [];
  for (const place of places) {
    if (!hasCoords(place)) continue;
    const lat = place.lat as number;
    const lng = place.lng as number;
    const radius = typeof place.radius === 'number' && Number.isFinite(place.radius) ? place.radius : 0;
    if (radius > 0) {
      out.push({ placeId: place.placeId, kind: 'circle', lat, lng, radius });
    } else {
      out.push({ placeId: place.placeId, kind: 'marker', lat, lng, radius: 0 });
    }
  }
  return out;
}

/** 默认内边距（像素）：点落在边界上会被裁一半，留出余量。 */
export const DEFAULT_PADDING = 32;

/** 内边距的合法化：不小于 0，且不超过可用边长的一半（否则内部矩形为负）。 */
function safePadding(padding: number, width: number, height: number): number {
  const raw = Number.isFinite(padding) && padding > 0 ? padding : 0;
  const limit = Math.max(0, Math.min(width, height) / 2 - 1);
  return Math.min(raw, limit);
}

/**
 * 把地点投影到 `width × height` 的画布上（降级格网用）。
 *
 * 做法：等距圆柱近似（`x ∝ lng`、`y ∝ -lat`，纬度向上），先按边界框求一个
 * **等比**缩放（不拉伸形状），再把结果居中到 `padding` 内。
 *
 * 三种退化情况：
 *  - 没有带坐标的地点 → `[]`；
 *  - 只有一个点（或所有点重合）→ 落在画布正中；
 *  - `width/height` 非法 → 返回 `[]`（调用方画不出东西，但不该抛）。
 *
 * 不变量：所有返回点的 `x ∈ [padding, width - padding]`、`y ∈ [padding, height - padding]`。
 *
 * @param places 地点（没有坐标的会被跳过）
 * @param width  画布宽（像素）
 * @param height 画布高（像素）
 * @param padding 内边距（像素，默认 32）
 */
export function projectPlaces(
  places: readonly UnpackedPlace[],
  width: number,
  height: number,
  padding: number = DEFAULT_PADDING,
): ProjectedPoint[] {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return [];

  const located = places.filter((place) => hasCoords(place));
  if (located.length === 0) return [];

  const pad = safePadding(padding, width, height);
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  let minLng = Number.POSITIVE_INFINITY;
  let maxLng = Number.NEGATIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  for (const place of located) {
    const lng = place.lng as number;
    const lat = place.lat as number;
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }

  const spanLng = maxLng - minLng;
  const spanLat = maxLat - minLat;

  // 单点（或全部重合）：直接放正中
  if (spanLng === 0 && spanLat === 0) {
    const cx = width / 2;
    const cy = height / 2;
    return located.map((place) => ({ placeId: place.placeId, x: cx, y: cy }));
  }

  // 等比缩放：取两个方向里更"紧"的那个，避免形状被拉伸
  const scaleX = spanLng > 0 ? innerW / spanLng : Number.POSITIVE_INFINITY;
  const scaleY = spanLat > 0 ? innerH / spanLat : Number.POSITIVE_INFINITY;
  const scale = Math.min(scaleX, scaleY);

  // 缩放后内容的实际尺寸，用于在内部矩形里居中
  const usedW = spanLng > 0 ? spanLng * scale : 0;
  const usedH = spanLat > 0 ? spanLat * scale : 0;
  const offsetX = pad + (innerW - usedW) / 2;
  const offsetY = pad + (innerH - usedH) / 2;

  return located.map((place) => {
    const lng = place.lng as number;
    const lat = place.lat as number;
    // 纬度向上：北（lat 大）在画布上方，所以 y 用 maxLat - lat
    const x = offsetX + (lng - minLng) * scale;
    const y = offsetY + (maxLat - lat) * scale;
    return { placeId: place.placeId, x: clamp(x, pad, width - pad), y: clamp(y, pad, height - pad) };
  });
}

/** 夹到 `[min, max]`（浮点误差可能让结果差一点点越过边界）。 */
function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  if (value < min) return min;
  if (value > max) return max;
  return value;
}
