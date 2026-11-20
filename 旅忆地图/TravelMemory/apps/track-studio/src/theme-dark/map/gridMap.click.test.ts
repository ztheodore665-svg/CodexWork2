// 纯 Node 环境：只测两个与 DOM 无关的换算函数（点击链路的真正逻辑都在这里）。
import { describe, expect, it } from 'vitest';

import { gcj02ToWgs84, mercX, wgs84ToGcj02 } from '@travelmemory/geo';
import { analyticProjection } from '@travelmemory/trail';

import { planeToWgs84, screenPointToPlane } from './gridMap';

/** 北京一带的一个 WGS84 点（真实坐标，方便对照）。 */
const WGS = { lng: 116.397428, lat: 39.90923 };

describe('screenPointToPlane —— 屏幕点 → 平面坐标', () => {
  it('屏幕中心 = 投影中心（wgs84 平面：喂 WGS 中心就反算出 WGS）', () => {
    const proj = analyticProjection(WGS, 12, 1280, 800);
    const plane = screenPointToPlane(proj, proj.ox, proj.oy);
    expect(plane.lng).toBeCloseTo(WGS.lng, 9);
    expect(plane.lat).toBeCloseTo(WGS.lat, 9);
  });

  it('屏幕中心 = 投影中心（gcj02 平面：喂 GCJ 中心就反算出 GCJ）', () => {
    const gcj = wgs84ToGcj02(WGS.lng, WGS.lat);
    const proj = analyticProjection(gcj, 12, 1280, 800);
    const plane = screenPointToPlane(proj, proj.ox, proj.oy);
    expect(plane.lng).toBeCloseTo(gcj.lng, 9);
    expect(plane.lat).toBeCloseTo(gcj.lat, 9);
  });

  it('向右 100px → 经度变大，且增量 = 100 / scaleX（墨卡托）', () => {
    const proj = analyticProjection(WGS, 12, 1280, 800);
    const right = screenPointToPlane(proj, proj.ox + 100, proj.oy);
    const expected = proj.cx + 100 / proj.scaleX;
    expect(mercX(right.lng)).toBeCloseTo(expected, 6);
    expect(right.lng).toBeGreaterThan(WGS.lng);
  });
});

describe('planeToWgs84 —— 点击结果的唯一转换边界', () => {
  it("平面是 'gcj02' → 必须转成 WGS84（差值 > 0.001°，约 100m 以上）", () => {
    const gcj = wgs84ToGcj02(WGS.lng, WGS.lat);
    const result = planeToWgs84('gcj02', gcj.lng, gcj.lat);

    // 与"未转换值"相比必须明显不同 —— 这正是之前点一次偏 500m 的 bug
    expect(Math.abs(result.lng - gcj.lng)).toBeGreaterThan(0.001);
    expect(Math.abs(result.lat - gcj.lat)).toBeGreaterThan(0.0001);
    // 转完要回到真实的 WGS 点
    expect(result.lng).toBeCloseTo(WGS.lng, 5);
    expect(result.lat).toBeCloseTo(WGS.lat, 5);
    // 与库函数一致（唯一的换算实现）
    const viaLib = gcj02ToWgs84(gcj.lng, gcj.lat);
    expect(result.lng).toBe(viaLib.lng);
    expect(result.lat).toBe(viaLib.lat);
  });

  it("平面是 'wgs84'（补点页）→ 原样返回，再转一次就会错 500m", () => {
    const result = planeToWgs84('wgs84', WGS.lng, WGS.lat);
    expect(result).toEqual({ lng: WGS.lng, lat: WGS.lat });

    // 反证：如果这里也转一次，就会偏离 0.006°
    const wrong = gcj02ToWgs84(WGS.lng, WGS.lat);
    expect(Math.abs(wrong.lng - WGS.lng)).toBeGreaterThan(0.004);
  });

  it('端到端：gcj02 平面点击屏幕中心 → 回调坐标 ≈ 该点的 WGS84', () => {
    const gcj = wgs84ToGcj02(WGS.lng, WGS.lat);
    const proj = analyticProjection(gcj, 12, 1280, 800);
    const plane = screenPointToPlane(proj, proj.ox, proj.oy);
    const wgs = planeToWgs84('gcj02', plane.lng, plane.lat);

    expect(wgs.lng).toBeCloseTo(WGS.lng, 5);
    expect(wgs.lat).toBeCloseTo(WGS.lat, 5);
    // 与"没转换"的平面值相比，经度差 > 0.001°（约 90m）
    expect(Math.abs(wgs.lng - plane.lng)).toBeGreaterThan(0.001);
  });
});
