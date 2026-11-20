/** 图层管理器（**纯逻辑**，不碰 DOM / 不高德 API）。
 *
 * ## 为什么图层管理器必须是纯函数
 *
 * 地图组件（`MapCanvas.tsx`）要调高德 JS API，那个东西在 Node 里不存在，
 * 连 import 都做不到。所以"哪些点画什么、藏在哪个图层、视野怎么框"
 * 全部在这里算完，输出一份**渲染计划**（`RenderPlan`），地图组件只当执行器。
 * 这样做换来三件事：
 *
 *   1. 单测能覆盖全部渲染决策（含 W6-4 的"模糊圈不画 Marker"这条红线）；
 *   2. 地图组件坏掉（key 没配、脚本被墙）时，决策逻辑照样可验证；
 *   3. 60fps 渲染的脏活留在人类手里，但"画什么"已经被测试锁死。
 *
 * ## 输入坐标一律 WGS84
 *
 * 存储红线（PROJECT.md §二、[1]）：坐标 WGS84 存储、GCJ-02 展示。
 * 转换发生在本模块内部（`wgs84ToGcj02`），**调用方永远只碰 WGS84**。
 */

import { wgs84ToGcj02, type LngLat } from '@travelmemory/geo';

/** 一个待渲染的点。`radius > 0` 是"画模糊圈"的充要条件。 */
export interface MapPointInput {
  /** 稳定 ID。分享点是 `placeId`，照片点是 photo id。 */
  id: string;
  /** WGS84 纬度。 */
  lat: number;
  /** WGS84 经度。 */
  lng: number;
  /** 承诺半径（米）。**不是**本次实际偏移量 —— 后端 `ObfuscatedPoint.radius`。 */
  radius?: number;
  /** 来源分组：信物解出的地点 / 本机照片 / 朋友导回主应用的行程。图层开关按它分组。 */
  source: 'share' | 'photo' | 'friend';
  /** 展示名（Tooltip / 无障碍标签用）。 */
  name?: string;
}

/** 半透明模糊圈。半径 = 承诺半径。 */
export interface CircleItem {
  kind: 'circle';
  id: string;
  /** GCJ-02 圆心（已转换，高德直接用）。 */
  center: LngLat;
  /** 承诺半径（米）。 */
  radius: number;
  source: 'share' | 'photo' | 'friend';
  name?: string;
}

/** 精确 Marker。只用于 `radius` 缺省或为 0 的点。 */
export interface MarkerItem {
  kind: 'marker';
  id: string;
  /** GCJ-02 位置（已转换）。 */
  position: LngLat;
  source: 'share' | 'photo' | 'friend';
  name?: string;
}

export type RenderItem = CircleItem | MarkerItem;

/** 图层可见性。
 *
 *  `share` / `photo` 默认开（都是"我的"数据）；
 *  `friend` **默认关**：朋友的点画上来容易被误认成自己去过的地方，
 *  必须由用户在地图抽屉里显式打开（W15-5 的开关，阶段 17 接通）。
 */
export interface LayerVisibility {
  share: boolean;
  photo: boolean;
  friend: boolean;
}

export const DEFAULT_LAYER_VISIBILITY: LayerVisibility = {
  share: true,
  photo: true,
  friend: false,
};

/** 视野包围盒（GCJ-02）。空输入时为 `null` —— 调用方应保持地图不动，
 *  而不是 fitBounds 到全世界（那是"初始视野丢了"的经典事故）。 */
export interface MapBounds {
  sw: LngLat;
  ne: LngLat;
}

export interface RenderPlan {
  /** 按输入顺序排列的执行清单。 */
  items: RenderItem[];
  /** 视野包围盒。`null` = 没有可渲染的点。 */
  bounds: MapBounds | null;
  /** 被图层开关藏起来的点数（UI 显示"已隐藏 N 个点"）。 */
  hidden: number;
}

/** 把点列表变成渲染计划。
 *
 * 核心规则（W6-4）：
 * - `radius > 0` → **Circle 半透明圆，不画 Marker**（模糊位置的点不能同时暴露精确 Marker）；
 * - `radius` 缺省 / 0 → Marker；
 * - 坐标非法（NaN / 越界）的点**整点丢弃**：宁可少画一个点，
 *   也不能把 NaN 喂给高德（它的表现是静默画到 (0,0)，几内达湾里一个点）。
 */
export function planMapRender(points: readonly MapPointInput[], visibility: LayerVisibility = DEFAULT_LAYER_VISIBILITY): RenderPlan {
  const items: RenderItem[] = [];
  let hidden = 0;
  let minLng = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLng = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;

  for (const point of points) {
    if (!isRenderable(point)) continue;
    if (!visibility[point.source]) {
      hidden += 1;
      continue;
    }
    const center = wgs84ToGcj02(point.lng, point.lat);
    const item: RenderItem =
      typeof point.radius === 'number' && Number.isFinite(point.radius) && point.radius > 0
        ? { kind: 'circle', id: point.id, center, radius: point.radius, source: point.source, name: point.name }
        : { kind: 'marker', id: point.id, position: center, source: point.source, name: point.name };
    items.push(item);
    minLng = Math.min(minLng, center.lng);
    minLat = Math.min(minLat, center.lat);
    maxLng = Math.max(maxLng, center.lng);
    maxLat = Math.max(maxLat, center.lat);
  }

  const bounds: MapBounds | null =
    items.length > 0
      ? { sw: { lng: minLng, lat: minLat }, ne: { lng: maxLng, lat: maxLat } }
      : null;
  return { items, bounds, hidden };
}

/** 坐标有效性：两端都是有限数，且在值域内。 */
export function isRenderable(point: MapPointInput): boolean {
  if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng)) return false;
  if (point.lat < -90 || point.lat > 90 || point.lng < -180 || point.lng > 180) return false;
  return true;
}

/** 按 ID 移除一个渲染项（照片被删 / 地点被拒时用）。找不到返回原数组的拷贝。 */
export function withoutItem(items: readonly RenderItem[], id: string): RenderItem[] {
  return items.filter((item) => item.id !== id);
}

/** 切换某个图层的可见性，返回新状态（纯函数，UI 直接拿去 setState）。 */
export function toggleLayer(visibility: LayerVisibility, layer: keyof LayerVisibility): LayerVisibility {
  return { ...visibility, [layer]: !visibility[layer] };
}

/** 由 `UnpackedPlace`（信物解包结果）构造图层输入。
 *
 *  单独一个函数而不是让 UI 自己拼：`radius` 的默认值语义（缺省 = 0 = 画 Marker）
 *  必须只有一个地方说了算。
 */
export function sharePointsFromPlaces(
  places: readonly { placeId: string; name: string; lat?: number; lng?: number; radius?: number }[],
): MapPointInput[] {
  const points: MapPointInput[] = [];
  for (const place of places) {
    if (typeof place.lat !== 'number' || typeof place.lng !== 'number') continue; // 没坐标 = 不上地图
    points.push({
      id: place.placeId,
      lat: place.lat,
      lng: place.lng,
      radius: place.radius ?? 0,
      source: 'share',
      name: place.name,
    });
  }
  return points;
}
