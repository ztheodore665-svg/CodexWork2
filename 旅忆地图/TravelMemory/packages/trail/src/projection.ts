/**
 * 墨卡托 → 屏幕的仿射变换。
 *
 * 关键决策（依据高德 JS API 2.0 官方文档核实结论）：
 *  1. **不用 `map.getCenter()` 标定**。它返回「实时中心点」，刚调过 setCenter 时可能还在动画中间，
 *     拿它标定会引入一帧的系统性偏移。相机中心由我们自己维护。
 *  2. 高德的平面坐标 = 标准 EPSG:3857（官方 lngLatToCoords 示例与 R=6378137 球面墨卡托逐位相同），
 *     瓦片 256px，所以「每墨卡托单位(0..1) 对应多少 CSS 像素」可以直接解析算出：
 *         scale = 256 * 2^zoom
 *  3. `map.lngLatToContainer` 只在 `complete` 之后做一次校验（见 baseMap.ts），不进每帧路径。
 */

import { mercX, mercY } from '@travelmemory/geo';

/** 瓦片边长（CSS px），高德 2.0 默认值 */
export const TILE_SIZE = 256;

export interface Projection {
  /** 相机中心的墨卡托坐标 */
  cx: number;
  cy: number;
  /** 相机中心对应的容器像素坐标（容器中心） */
  ox: number;
  oy: number;
  /** 像素 / 墨卡托单位 */
  scaleX: number;
  scaleY: number;
}

/**
 * 解析投影：不需要任何地图实例，纯数学。
 * @param scaleFactor 实测/解析 的比例修正（仅在实测明显不一致时 != 1）
 */
export function analyticProjection(
  center: { lng: number; lat: number },
  zoom: number,
  width: number,
  height: number,
  scaleFactor = 1,
): Projection {
  const scale = TILE_SIZE * Math.pow(2, zoom) * scaleFactor;
  return {
    cx: mercX(center.lng),
    cy: mercY(center.lat),
    ox: width / 2,
    oy: height / 2,
    scaleX: scale,
    scaleY: scale,
  };
}

/** 每帧更新相机中心（不重新计算缩放） */
export function setCamera(proj: Projection, mx: number, my: number, ox: number, oy: number): void {
  proj.cx = mx;
  proj.cy = my;
  proj.ox = ox;
  proj.oy = oy;
}

export function projectX(proj: Projection, mx: number): number {
  return proj.ox + (mx - proj.cx) * proj.scaleX;
}

export function projectY(proj: Projection, my: number): number {
  return proj.oy + (my - proj.cy) * proj.scaleY;
}
