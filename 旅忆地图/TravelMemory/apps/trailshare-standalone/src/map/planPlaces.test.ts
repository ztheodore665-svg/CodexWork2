/** 地图规划纯逻辑（W12-4）的单测。
 *
 * 锁死两个不变量：
 *  ① `planPlaceOverlays` 的三分支（circle / marker / 无坐标不出现）；
 *  ② `projectPlaces` 的投影点**必须落在 padding 内**（落外面 = 被裁掉 = 少一个点，
 *    而这种丢失在界面上没有任何报错）。
 */

import { describe, expect, it } from 'vitest';

import type { IntensityBucket, Tag, UnpackedPlace } from '../types/share';
import { DEFAULT_PADDING, planPlaceOverlays, projectPlaces } from './planPlaces';

function place(
  placeId: string,
  coords: { lat?: number; lng?: number; radius?: number } = {},
): UnpackedPlace {
  return {
    placeId,
    name: `地点 ${placeId}`,
    date: '2026-11-01',
    tag: 'heritage' as Tag,
    intensity: 'high' as IntensityBucket,
    photos: [],
    ...coords,
  };
}

describe('planPlaceOverlays', () => {
  it('radius > 0 → circle；有坐标无半径 → marker；无坐标 → 不出现', () => {
    const overlays = planPlaceOverlays([
      place('p1', { lat: 30.24, lng: 120.15, radius: 1500 }),
      place('p2', { lat: 31.2, lng: 121.4 }),
      place('p3'),
      place('p4', { radius: 800 }), // 只有半径没有坐标：仍然不出现（绝不猜位置）
    ]);

    expect(overlays.map((item) => item.placeId)).toEqual(['p1', 'p2']);
    expect(overlays[0]).toMatchObject({ kind: 'circle', radius: 1500, lat: 30.24, lng: 120.15 });
    expect(overlays[1]).toMatchObject({ kind: 'marker', radius: 0, lat: 31.2, lng: 121.4 });
  });

  it('radius 为 0 / 负数 / NaN 时退化成 marker', () => {
    const overlays = planPlaceOverlays([
      place('zero', { lat: 30, lng: 120, radius: 0 }),
      place('neg', { lat: 30, lng: 120, radius: -5 }),
      place('nan', { lat: 30, lng: 120, radius: Number.NaN }),
    ]);
    expect(overlays.map((item) => item.kind)).toEqual(['marker', 'marker', 'marker']);
  });

  it('越界 / 非有限坐标被跳过', () => {
    const overlays = planPlaceOverlays([
      place('bad-lat', { lat: 91, lng: 120, radius: 100 }),
      place('bad-lng', { lat: 30, lng: 181, radius: 100 }),
      place('inf', { lat: Number.POSITIVE_INFINITY, lng: 120 }),
      place('ok', { lat: 30, lng: 120, radius: 100 }),
    ]);
    expect(overlays.map((item) => item.placeId)).toEqual(['ok']);
  });

  it('空数组不抛，返回空数组', () => {
    expect(planPlaceOverlays([])).toEqual([]);
  });
});

describe('projectPlaces', () => {
  const W = 1000;
  const H = 640;

  it('所有点都落在 padding 内（含边界极值点）', () => {
    const points = projectPlaces(
      [
        place('a', { lat: 30.0, lng: 120.0 }),
        place('b', { lat: 31.5, lng: 121.9 }),
        place('c', { lat: 29.1, lng: 119.2 }),
        place('d', { lat: 30.7, lng: 122.4 }),
      ],
      W,
      H,
    );

    expect(points).toHaveLength(4);
    for (const point of points) {
      expect(point.x).toBeGreaterThanOrEqual(DEFAULT_PADDING);
      expect(point.x).toBeLessThanOrEqual(W - DEFAULT_PADDING);
      expect(point.y).toBeGreaterThanOrEqual(DEFAULT_PADDING);
      expect(point.y).toBeLessThanOrEqual(H - DEFAULT_PADDING);
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
    }
  });

  it('纬度越大越靠上（y 越小）', () => {
    const points = projectPlaces(
      [place('south', { lat: 29.0, lng: 120 }), place('north', { lat: 31.0, lng: 120 })],
      W,
      H,
    );
    const south = points.find((item) => item.placeId === 'south');
    const north = points.find((item) => item.placeId === 'north');
    expect(north!.y).toBeLessThan(south!.y);
  });

  it('单点居中', () => {
    const [point] = projectPlaces([place('only', { lat: 30.24, lng: 120.15 })], W, H);
    expect(point.x).toBeCloseTo(W / 2, 6);
    expect(point.y).toBeCloseTo(H / 2, 6);
  });

  it('没有坐标的点不参与投影（也不影响边界框）', () => {
    const points = projectPlaces(
      [place('a', { lat: 30, lng: 120 }), place('b'), place('c', { lat: 32, lng: 122 })],
      W,
      H,
    );
    expect(points.map((item) => item.placeId)).toEqual(['a', 'c']);
  });

  it('空数组 / 非法尺寸不抛', () => {
    expect(projectPlaces([], W, H)).toEqual([]);
    expect(projectPlaces([place('a', { lat: 30, lng: 120 })], 0, 0)).toEqual([]);
    expect(projectPlaces([place('a', { lat: 30, lng: 120 })], Number.NaN, H)).toEqual([]);
  });
});
