/** Web 墨卡托投影（归一化到 0..1）。
 *
 *  地图瓦片、轨迹渲染都用这套公式：`像素 = 256 * 2^zoom * 墨卡托坐标`。
 *  高德 2D 的投影与 EPSG:3857 逐位一致（已用官方 `lngLatToCoords` 示例数值验证），
 *  所以应用自己的画布不需要向地图「标定」，直接算即可。
 */

const PI = Math.PI;

/** 归一化 X：西经 -180 → 0，东经 180 → 1 */
export function mercX(lng: number): number {
  return (lng + 180) / 360;
}

/** 归一化 Y：北纬 85 → 0，南纬 -85 → 1（与屏幕 y 同向） */
export function mercY(lat: number): number {
  const phi = (lat * PI) / 180;
  return (1 - Math.log(Math.tan(phi) + 1 / Math.cos(phi)) / PI) / 2;
}

export function invMercX(mx: number): number {
  return mx * 360 - 180;
}

export function invMercY(my: number): number {
  const n = PI * (1 - 2 * my);
  return (180 / PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
}
