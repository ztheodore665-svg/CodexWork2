/** 图层管理器单测（纯逻辑，node 环境直接跑）。
 *
 * 重点守住两条红线：
 * 1. **W6-4**：`radius > 0` → 只画半透明圆，**绝不画 Marker**；
 * 2. **PROJECT.md §二、[1]**：输入 WGS84、输出 GCJ-02，业务侧不自己转坐标。
 */

import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LAYER_VISIBILITY,
  isRenderable,
  planMapRender,
  sharePointsFromPlaces,
  toggleLayer,
  withoutItem,
  type MapPointInput,
} from './layers';

/** 天安门 WGS84。转成 GCJ-02 后应与 coords.test.ts 同值（116.403672, 39.910634）。 */
const TIANANMEN: MapPointInput = {
  id: 'p_share1',
  lat: 39.90923,
  lng: 116.397428,
  radius: 800,
  source: 'share',
  name: '故宫',
};

describe('planMapRender：模糊圈决策', () => {
  it('radius > 0 → Circle，且不产生任何 Marker', () => {
    const plan = planMapRender([TIANANMEN]);
    expect(plan.items).toHaveLength(1);
    expect(plan.items[0].kind).toBe('circle');
    expect(plan.items.filter((i) => i.kind === 'marker')).toHaveLength(0);
  });

  it('圆的圆心是 GCJ-02（WGS84 输入不允许原样出去）', () => {
    const plan = planMapRender([TIANANMEN]);
    const item = plan.items[0];
    if (item.kind !== 'circle') throw new Error('应当是 circle');
    expect(item.center.lng).toBe(116.403672);
    expect(item.center.lat).toBe(39.910634);
    expect(item.radius).toBe(800);
  });

  it('radius = 0 / 缺省 → Marker', () => {
    const plan = planMapRender([
      { id: 'a', lat: 39.9, lng: 116.4, radius: 0, source: 'photo' },
      { id: 'b', lat: 39.9, lng: 116.4, source: 'photo' },
    ]);
    expect(plan.items.map((i) => i.kind)).toEqual(['marker', 'marker']);
  });

  it('radius 为负数 / NaN 视同无效 → 退化为 Marker 而不是消失', () => {
    const plan = planMapRender([
      { id: 'a', lat: 39.9, lng: 116.4, radius: -5, source: 'share' },
      { id: 'b', lat: 39.9, lng: 116.4, radius: Number.NaN, source: 'share' },
    ]);
    expect(plan.items.map((i) => i.kind)).toEqual(['marker', 'marker']);
  });

  it('半径取承诺半径，不取实际偏移量（契约注释的代码化）', () => {
    const plan = planMapRender([TIANANMEN]);
    const item = plan.items[0];
    if (item.kind !== 'circle') throw new Error('应当是 circle');
    // 800 = 后端返回的 radius_m；圆心与真实位置的距离不由本模块关心
    expect(item.radius).toBe(TIANANMEN.radius);
  });
});

describe('planMapRender：输入清洗', () => {
  it('坐标非有限数 / 越界的点整点丢弃', () => {
    const plan = planMapRender([
      { id: 'nan', lat: Number.NaN, lng: 116.4, source: 'photo' },
      { id: 'oob-lat', lat: 91, lng: 116.4, source: 'photo' },
      { id: 'oob-lng', lat: 39.9, lng: 181, source: 'photo' },
      { id: 'inf', lat: 39.9, lng: Number.POSITIVE_INFINITY, source: 'photo' },
      { id: 'ok', lat: 39.9, lng: 116.4, source: 'photo' },
    ]);
    expect(plan.items.map((i) => i.id)).toEqual(['ok']);
  });

  it('isRenderable 与 planMapRender 的判定一致', () => {
    expect(isRenderable(TIANANMEN)).toBe(true);
    expect(isRenderable({ id: 'x', lat: Number.NaN, lng: 1, source: 'photo' })).toBe(false);
  });
});

describe('planMapRender：图层开关与视野', () => {
  it('关掉某图层 → 该来源的点进 hidden，不出现在 items', () => {
    const plan = planMapRender([TIANANMEN, { id: 'ph', lat: 39.9, lng: 116.4, source: 'photo' }], {
      share: false,
      photo: true,
      friend: false,
    });
    expect(plan.items.map((i) => i.id)).toEqual(['ph']);
    expect(plan.hidden).toBe(1);
  });

  it('朋友的分享默认不进计划（必须显式打开）', () => {
    const friendPoint = { id: 'f1', lat: 39.9, lng: 116.4, source: 'friend' as const };
    const off = planMapRender([friendPoint]);
    expect(off.items).toHaveLength(0);
    expect(off.hidden).toBe(1);

    const on = planMapRender([friendPoint], { share: true, photo: true, friend: true });
    expect(on.items.map((i) => i.id)).toEqual(['f1']);
  });

  it('空计划 → bounds 为 null（调用方必须保持地图不动）', () => {
    const plan = planMapRender([]);
    expect(plan.bounds).toBeNull();
    expect(plan.items).toHaveLength(0);
  });

  it('bounds 是全部可见点的 GCJ-02 包围盒', () => {
    const plan = planMapRender([
      TIANANMEN,
      { id: 'p2', lat: 34.6636, lng: 112.4342, source: 'share', radius: 500 }, // 洛阳
    ]);
    expect(plan.bounds).not.toBeNull();
    const { sw, ne } = plan.bounds!;
    expect(sw.lat).toBeLessThan(ne.lat);
    expect(sw.lng).toBeLessThan(ne.lng);
    // 天安门与洛阳一东一西，包围盒经度跨度应远大于 1 度
    expect(ne.lng - sw.lng).toBeGreaterThan(1);
  });
});

describe('辅助函数', () => {
  it('toggleLayer 只翻目标图层', () => {
    const next = toggleLayer(DEFAULT_LAYER_VISIBILITY, 'share');
    expect(next).toEqual({ share: false, photo: true, friend: false });
  });

  it('withoutItem 按 ID 移除', () => {
    const plan = planMapRender([TIANANMEN, { id: 'ph', lat: 39.9, lng: 116.4, source: 'photo' }]);
    expect(withoutItem(plan.items, 'ph').map((i) => i.id)).toEqual(['p_share1']);
    expect(withoutItem(plan.items, 'nope')).toHaveLength(2);
  });
});

describe('sharePointsFromPlaces：UnpackedPlace → 图层输入', () => {
  it('没坐标的地点不上地图；有坐标的带上承诺半径', () => {
    const points = sharePointsFromPlaces([
      { placeId: 'p1', name: '龙门石窟', lat: 34.55, lng: 112.45, radius: 600 },
      { placeId: 'p2', name: '某处', }, // 无坐标
      { placeId: 'p3', name: '老君山', lat: 33.75, lng: 111.6 }, // 无 radius
    ]);
    expect(points).toHaveLength(2);
    expect(points[0]).toMatchObject({ id: 'p1', radius: 600, source: 'share' });
    // 无 radius → 0（画 Marker，与 planMapRender 的判定闭环）
    expect(points[1]).toMatchObject({ id: 'p3', radius: 0 });
  });

  it('端到端：模糊圈地点 + 无半径地点混排', () => {
    const plan = planMapRender(
      sharePointsFromPlaces([
        { placeId: 'p1', name: 'A', lat: 34.55, lng: 112.45, radius: 600 },
        { placeId: 'p2', name: 'B', lat: 34.6, lng: 112.5 },
      ]),
    );
    expect(plan.items.map((i) => i.kind)).toEqual(['circle', 'marker']);
  });
});
