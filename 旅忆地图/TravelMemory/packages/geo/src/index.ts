/** 坐标与地图基础库。
 *
 * 前端唯一的坐标转换入口 —— 主应用与演示站都从这里取，
 * 避免「同一个点两个应用算出不同位置」。
 */

export { gcj02ToWgs84, outOfChina, wgs84ToGcj02, type LngLat } from './coords';
export { invMercX, invMercY, mercX, mercY } from './mercator';
export { haversine } from './distance';
export { clamp } from './number';
