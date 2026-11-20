import { describe, expect, it } from 'vitest';
import {
  analyticProjection,
  projectX,
  projectY,
  setCamera,
  TILE_SIZE,
  type Projection,
} from './projection';
import { mercX, mercY } from '@travelmemory/geo';

/**
 * 投影是「墨卡托 → 屏幕」的仿射变换，纯数学、无地图实例依赖。
 *
 * 说明：任务书里写的是 `fallbackProjection`，但源码已统一收敛为
 * `analyticProjection(center, zoom, width, height, scaleFactor = 1)`（导出名见
 * src/engine/projection.ts:34）。本文件按**当前实际 API** 断言，语义与数值要求不变。
 */

const W = 800;
const H = 600;

describe('analyticProjection 基准值', () => {
  it('{lng:0,lat:0}, zoom 0, 800x600 → scaleX 256 / ox 400 / oy 300 / cx 0.5 / cy 0.5', () => {
    const proj = analyticProjection({ lng: 0, lat: 0 }, 0, W, H);

    expect(proj.scaleX).toBe(256);
    expect(proj.scaleX).toBe(TILE_SIZE);
    expect(proj.ox).toBe(400);
    expect(proj.oy).toBe(300);
    expect(proj.cx).toBe(0.5);
    expect(proj.cy).toBe(0.5);
  });

  it('Web 墨卡托像素为正方形：scaleY === scaleX', () => {
    for (const zoom of [0, 5, 9.5, 13]) {
      const proj = analyticProjection({ lng: 116.404, lat: 39.915 }, zoom, W, H);
      expect(proj.scaleY).toBe(proj.scaleX);
    }
  });

  it('cx / cy 就是中心点的墨卡托坐标（与 mercX / mercY 一致）', () => {
    const center = { lng: 116.404, lat: 39.915 };
    const proj = analyticProjection(center, 10, W, H);
    expect(proj.cx).toBeCloseTo(mercX(center.lng), 15);
    expect(proj.cy).toBeCloseTo(mercY(center.lat), 15);
    expect(proj.cx).toBeCloseTo(mercX(center.lng), 15);
  });

  it('zoom 每 +1，scaleX 精确翻倍', () => {
    let previous = analyticProjection({ lng: 0, lat: 0 }, 0, W, H).scaleX;
    expect(previous).toBe(256);

    for (let zoom = 1; zoom <= 18; zoom += 1) {
      const current = analyticProjection({ lng: 0, lat: 0 }, zoom, W, H).scaleX;
      expect(current).toBe(previous * 2);
      expect(Number.isFinite(current)).toBe(true);
      previous = current;
    }
  });

  it('scaleFactor 线性放大比例', () => {
    const base = analyticProjection({ lng: 0, lat: 0 }, 10, W, H);
    const scaled = analyticProjection({ lng: 0, lat: 0 }, 10, W, H, 1.5);
    expect(scaled.scaleX).toBe(base.scaleX * 1.5);
    // scaleFactor 不影响中心与容器原点
    expect(scaled.cx).toBe(base.cx);
    expect(scaled.cy).toBe(base.cy);
    expect(scaled.ox).toBe(base.ox);
    expect(scaled.oy).toBe(base.oy);
  });
});

describe('projectX / projectY', () => {
  const proj = analyticProjection({ lng: 116.404, lat: 39.915 }, 10, W, H);

  it('中心点投到自己身上：projectX(proj, proj.cx) === proj.ox', () => {
    expect(projectX(proj, proj.cx)).toBe(proj.ox);
    expect(projectY(proj, proj.cy)).toBe(proj.oy);
  });

  it('中心点右边的点投到 ox 右侧，上方的点投到 oy 上方', () => {
    expect(projectX(proj, proj.cx + 0.01)).toBeGreaterThan(proj.ox);
    expect(projectX(proj, proj.cx - 0.01)).toBeLessThan(proj.ox);
    // 墨卡托 my 与屏幕 y 同向：纬度更高 → my 更小 → 屏幕 y 更小
    expect(projectY(proj, proj.cy - 0.01)).toBeLessThan(proj.oy);
  });

  it('像素位移与 Δ墨卡托线性成正比（scaleX 就是比例系数）', () => {
    const dx = projectX(proj, proj.cx + 0.02) - projectX(proj, proj.cx);
    expect(dx).toBeCloseTo(0.02 * proj.scaleX, 9);
  });
});

describe('墨卡托方向各向异性', () => {
  /**
   * 墨卡托只拉伸纬度方向，经度方向的像素位移与纬度无关。
   * 如果这条断言挂了，说明实现里误把 cos(lat) 也乘到了 x 上，
   * 表现会是「高纬地区轨迹横向变窄」。
   */
  it('纬度 0 与纬度 40 处，同样 Δlng 的像素位移相等', () => {
    const deltaLng = 0.1;
    const atEquator = analyticProjection({ lng: 100, lat: 0 }, 10, W, H);
    const atForty = analyticProjection({ lng: 100, lat: 40 }, 10, W, H);

    const shift = (proj: Projection): number =>
      projectX(proj, mercX(100 + deltaLng)) - projectX(proj, mercX(100));

    expect(shift(atEquator)).toBeCloseTo(shift(atForty), 9);
    expect(shift(atEquator)).toBeGreaterThan(0);

    // 反过来：同样 Δlat 的纵向像素位移在高纬一定更大（墨卡托拉伸）
    const latShift = (baseLat: number): number => {
      const local = analyticProjection({ lng: 100, lat: baseLat }, 10, W, H);
      return Math.abs(projectY(local, mercY(baseLat + 10)) - projectY(local, mercY(baseLat)));
    };

    expect(latShift(40)).toBeGreaterThan(latShift(0));
  });
});

describe('setCamera', () => {
  it('只更新中心与容器原点，不动缩放', () => {
    const proj = analyticProjection({ lng: 0, lat: 0 }, 12, W, H);
    const scaleX = proj.scaleX;
    const scaleY = proj.scaleY;

    setCamera(proj, 0.25, 0.75, 100, 200);

    expect(proj.cx).toBe(0.25);
    expect(proj.cy).toBe(0.75);
    expect(proj.ox).toBe(100);
    expect(proj.oy).toBe(200);
    expect(proj.scaleX).toBe(scaleX);
    expect(proj.scaleY).toBe(scaleY);

    // 新中心点投到新的容器原点
    expect(projectX(proj, 0.25)).toBe(100);
    expect(projectY(proj, 0.75)).toBe(200);
  });
});
