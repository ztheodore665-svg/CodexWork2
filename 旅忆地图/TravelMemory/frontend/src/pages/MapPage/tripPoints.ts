/** 后端行程视图 → 地图图层输入（**纯逻辑**，不碰 DOM / 不高德 API）。
 *
 * ## 为什么不写进 `layers.ts`
 *
 * `layers.ts` 是被 `layers.test.ts` 锁死的渲染决策层，职责是"给我一堆点，我决定
 * 画圆还是画 Marker"。"后端行程 → 点"这一段是**数据接入**，不是渲染决策。
 * 混进去会让 `layers.ts` 依赖 `services/api/`，那一层就被网络类型污染了。
 *
 * ## 坐标口径
 *
 * 后端行程里的 `lat/lng` 一律 WGS84（`Photo.coord_system` 常量写死），
 * 与 `MapPointInput` 要求的一致，**这里不做任何转换**；
 * WGS84 → GCJ-02 由 `layers.ts#planMapRender` 内部统一处理。
 */

import type { TripView } from '../../services/api/trips';
import type { MapPointInput } from './layers';

/** 行程里的一张照片 → 一个地图点。
 *
 * @param source 这批行程属于谁：`photo` = 我的，`friend` = 朋友导回的。
 *   图层开关按 source 分组，所以**同一个函数**能产出两种来源的点 ——
 *   分类逻辑在调用方（`splitTrips`），这里只负责搬运，不重新判断"谁是谁的"。
 */
export function tripPointsFromTrips(
  trips: readonly TripView[],
  source: 'photo' | 'friend' = 'photo',
): MapPointInput[] {
  const points: MapPointInput[] = [];
  const seen = new Set<string>();

  for (const trip of trips) {
    for (const item of trip.items) {
      if (typeof item.lat !== 'number' || typeof item.lng !== 'number') continue;
      // 同一张照片可能挂在多条行程里（重算前后），重复 id 会让高德收到两个同 id 覆盖物
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      points.push({
        id: item.id,
        lat: item.lat,
        lng: item.lng,
        // 不加 radius：本机照片没有"承诺半径"这回事，画精确 Marker 才是对的
        source,
        name: item.semanticLabel ?? trip.title,
      });
    }
  }
  return points;
}
