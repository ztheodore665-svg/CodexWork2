/** 球面大圆距离。
 *
 *  里程必须用它，不能用墨卡托平面距离 —— 墨卡托在高纬会把东西向距离放大
 *  （`1/cos(lat)`），于是「同样 1 度经度」在赤道算 111km、在北纬 60 度也会算成 111km，
 *  而真实距离只有 55.6km。`geo.test.ts` 里有一条对照断言专门守着这件事。
 */

/** 地球平均半径（米），IUGG 推荐值。 */
const EARTH_RADIUS_M = 6371008.8;

/** 球面大圆距离（米）。参数顺序 (lat1, lng1, lat2, lng2)。 */
export function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const PI = Math.PI;
  const dLat = ((lat2 - lat1) * PI) / 180;
  const dLng = ((lng2 - lng1) * PI) / 180;
  const rLat1 = (lat1 * PI) / 180;
  const rLat2 = (lat2 * PI) / 180;
  const sinDLat = Math.sin(dLat / 2);
  const sinDLng = Math.sin(dLng / 2);
  const h = sinDLat * sinDLat + Math.cos(rLat1) * Math.cos(rLat2) * sinDLng * sinDLng;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}
