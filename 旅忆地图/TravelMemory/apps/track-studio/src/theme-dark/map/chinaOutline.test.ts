// 纯 Node 环境：不碰 DOM（渲染用 mock ctx，加载用 mock fetch）。
import { describe, expect, it, vi } from 'vitest';

import { gcj02ToWgs84, mercX, mercY, wgs84ToGcj02 } from '@travelmemory/geo';
import { analyticProjection } from '@travelmemory/trail';

import {
  outlineCacheKey,
  loadProvinces,
  renderChinaOutline,
  stripProvinces,
  type ProvinceCollection,
} from './chinaOutline';

/** 取集合里第一个省的第一个点（兼容 Polygon / MultiPolygon 两种几何）。 */
function firstPosition(collection: ProvinceCollection): [number, number] {
  const geometry = collection.features[0].geometry;
  const ring =
    geometry.type === 'Polygon'
      ? geometry.coordinates[0]
      : geometry.coordinates[0][0];
  return [ring[0][0], ring[0][1]];
}

/** 一小块真省界的替身：一个正方形，坐标故意给 6 位小数（DataV 就是这样）。 */
function square(lng: number, lat: number, size = 0.5): number[][][] {
  return [
    [
      [lng, lat],
      [lng + size, lat],
      [lng + size, lat + size],
      [lng, lat + size],
      [lng, lat],
    ],
  ];
}

function rawCollection(): unknown {
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        // DataV 的 properties 有一堆字段，必须被裁掉
        properties: {
          adcode: 110000,
          name: '北京市',
          center: [116.405285, 39.904989],
          centroid: [116.41995, 40.18994],
          childrenNum: 16,
          level: 'province',
          parent: { adcode: 100000 },
        },
        // 带一个洞（两个环）的 Polygon
        geometry: {
          type: 'Polygon',
          coordinates: [square(116.2, 39.8)[0], square(116.42, 39.92, 0.1)[0]],
        },
      },
      {
        type: 'Feature',
        properties: { adcode: 120000, name: '天津市' },
        // MultiPolygon：两个独立的岛
        geometry: {
          type: 'MultiPolygon',
          coordinates: [square(117.1, 39.1), square(117.9, 38.6, 0.2)],
        },
      },
      {
        // 坏几何：必须被丢掉，而不是让整份数据失效
        type: 'Feature',
        properties: { adcode: 130000, name: '河北省' },
        geometry: { type: 'LineString', coordinates: [[116, 39]] },
      },
      {
        type: 'Feature',
        properties: { adcode: 140000, name: '山西省' },
        geometry: { type: 'Polygon', coordinates: [] },
      },
      { type: 'Feature', properties: { name: '没有几何' }, geometry: null },
    ],
  };
}

describe('stripProvinces —— 裁属性 / 裁精度', () => {
  it('属性只剩 adcode 与 name', () => {
    const collection = stripProvinces(rawCollection());
    expect(collection.type).toBe('FeatureCollection');
    expect(collection.features.map((feature) => feature.properties)).toEqual([
      { adcode: 110000, name: '北京市' },
      { adcode: 120000, name: '天津市' },
    ]);
  });

  it('坐标最多 4 位小数，且没有连续重复点', () => {
    const collection = stripProvinces(rawCollection());
    const decimals = (value: number): number => {
      const text = String(value);
      const dot = text.indexOf('.');
      return dot < 0 ? 0 : text.length - dot - 1;
    };

    for (const feature of collection.features) {
      const polygons =
        feature.geometry.type === 'Polygon'
          ? [feature.geometry.coordinates]
          : feature.geometry.coordinates;
      for (const polygon of polygons) {
        for (const ring of polygon) {
          for (let index = 0; index < ring.length; index += 1) {
            expect(decimals(ring[index][0])).toBeLessThanOrEqual(4);
            expect(decimals(ring[index][1])).toBeLessThanOrEqual(4);
            if (index === 0) continue;
            // 相邻两点不能完全相同（4 位小数会大量制造重复点）
            const same =
              ring[index][0] === ring[index - 1][0] && ring[index][1] === ring[index - 1][1];
            expect(same).toBe(false);
          }
        }
      }
    }
  });

  it('保留 Polygon（含洞）与 MultiPolygon 的层级结构', () => {
    const [beijing, tianjin] = stripProvinces(rawCollection()).features;
    expect(beijing.geometry.type).toBe('Polygon');
    expect(beijing.geometry.type === 'Polygon' && beijing.geometry.coordinates).toHaveLength(2);
    expect(tianjin.geometry.type).toBe('MultiPolygon');
    expect(tianjin.geometry.type === 'MultiPolygon' && tianjin.geometry.coordinates).toHaveLength(2);
  });

  it('非法输入一律返回空集合，不抛', () => {
    for (const bad of [null, undefined, 42, 'x', {}, { features: 'no' }, { features: [null, 1] }]) {
      const collection = stripProvinces(bad);
      expect(collection.type).toBe('FeatureCollection');
      expect(collection.features).toEqual([]);
    }
  });

  it('adcode 是字符串时也归一成数字；缺失时为 null（不编造）', () => {
    const collection = stripProvinces({
      type: 'FeatureCollection',
      features: [
        { properties: { adcode: '310000', name: '上海市' }, geometry: { type: 'Polygon', coordinates: square(121.4, 31.2) } },
        { properties: { name: '无码省' }, geometry: { type: 'Polygon', coordinates: square(100, 30) } },
      ],
    });
    expect(collection.features.map((feature) => feature.properties.adcode)).toEqual([310000, null]);
  });
});

describe('坐标系：DataV 是 GCJ-02，而管线平面也是 GCJ-02 —— 渲染时不转换', () => {
  it('北京 GCJ 116.4074,39.9042 的偏移量与已知 GCJ 偏移一致，且往返误差为 0', () => {
    const wgs = gcj02ToWgs84(116.4074, 39.9042);

    // 本仓库实现给出的值：{ lng: 116.401157, lat: 39.902796 }
    // 北京一带 GCJ 相对 WGS 的已知偏移是 lng +0.004~0.008、lat +0.0005~0.004。
    expect(116.4074 - wgs.lng).toBeGreaterThan(0.004);
    expect(116.4074 - wgs.lng).toBeLessThan(0.009);
    expect(39.9042 - wgs.lat).toBeGreaterThan(0.0005);
    expect(39.9042 - wgs.lat).toBeLessThan(0.004);

    // 真正的正确性凭据：正向再转回去必须逐位回到原点（迭代反解的收敛性）
    const back = wgs84ToGcj02(wgs.lng, wgs.lat);
    expect(Math.abs(back.lng - 116.4074)).toBeLessThan(1e-6);
    expect(Math.abs(back.lat - 39.9042)).toBeLessThan(1e-6);
  });

  it('默认平面（gcj02）下，DataV 坐标直接 mercX/mercY —— 与轨迹同一平面，像素级对齐', () => {
    const collection = stripProvinces({
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { adcode: 110000, name: '北京市' },
          geometry: { type: 'Polygon', coordinates: square(116.405285, 39.904989, 0.4) },
        },
      ],
    });
    // 注意：stripProvinces 已把坐标收成 4 位小数（116.4053），断言要用**收过之后**的值
    const [lng, lat] = firstPosition(collection);
    const proj = analyticProjection({ lng, lat }, 13, 1280, 800);
    const { ctx, points } = mockCtx();
    renderChinaOutline(ctx, proj, collection, { color: '#fff', lineWidth: 1 });

    const [firstX, firstY] = points[0];
    const expectedX = proj.ox + (mercX(lng) - proj.cx) * proj.scaleX;
    const expectedY = proj.oy + (mercY(lat) - proj.cy) * proj.scaleY;
    expect(firstX).toBeCloseTo(expectedX, 6);
    expect(firstY).toBeCloseTo(expectedY, 6);
    // 未转换 ⇒ 首点正好落在屏幕中心（差异只来自 4 位小数取整，远小于 1px）
    expect(Math.abs(firstX - proj.ox)).toBeLessThan(1);
    expect(Math.abs(firstY - proj.oy)).toBeLessThan(1);
  });

  /**
   * 文档型测试：把"顺手转一下 WGS"的代价钉成数字。
   *
   * Z13 下 1° 经度 ≈ 1280×2^13/360 ≈ 29,127 px，GCJ 与 WGS 在北京差 0.0062° 经度
   * → 约 180px；即便只看纬度差也有 ~40px。要求 > 20px 是留足余量的下限。
   */
  it('若"顺手转成 WGS 再投影"，Z13 下会偏 > 20px（这就是之前的错位）', () => {
    const proj = analyticProjection({ lng: 116.405285, lat: 39.904989 }, 13, 1280, 800);
    const rawX = proj.ox + (mercX(116.405285) - proj.cx) * proj.scaleX;
    const wgs = gcj02ToWgs84(116.405285, 39.904989);
    const convertedX = proj.ox + (mercX(wgs.lng) - proj.cx) * proj.scaleX;
    const rawY = proj.oy + (mercY(39.904989) - proj.cy) * proj.scaleY;
    const convertedY = proj.oy + (mercY(wgs.lat) - proj.cy) * proj.scaleY;

    const distance = Math.hypot(rawX - convertedX, rawY - convertedY);
    expect(distance).toBeGreaterThan(20);
  });

  it('frame: wgs84（补点页的平面）时才转换 —— 落点等于"转成 WGS 后投影"', () => {
    const collection = stripProvinces({
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { adcode: 110000, name: '北京市' },
          geometry: { type: 'Polygon', coordinates: square(116.405285, 39.904989, 0.4) },
        },
      ],
    });
    const [rawLng, rawLat] = firstPosition(collection);
    // 补点页的投影中心是 WGS84：把同一个点转成 WGS 再当中心
    const wgs = gcj02ToWgs84(rawLng, rawLat);
    const proj = analyticProjection(wgs, 10, 1280, 800);
    const { ctx, points } = mockCtx();
    renderChinaOutline(ctx, proj, collection, { color: '#fff', lineWidth: 1, frame: 'wgs84' });

    const [firstX, firstY] = points[0];
    expect(firstX).toBeCloseTo(proj.ox + (mercX(wgs.lng) - proj.cx) * proj.scaleX, 6);
    expect(firstY).toBeCloseTo(proj.oy + (mercY(wgs.lat) - proj.cy) * proj.scaleY, 6);
    // 转换正确 ⇒ 该点落回屏幕正中
    expect(Math.abs(firstX - proj.ox)).toBeLessThan(1);
    expect(Math.abs(firstY - proj.oy)).toBeLessThan(1);
  });
});

/** 记录调用的假 ctx（只实现 renderChinaOutline 用到的那几个方法/属性）。 */
function mockCtx() {
  const calls = {
    save: 0,
    restore: 0,
    beginPath: 0,
    moveTo: 0,
    lineTo: 0,
    closePath: 0,
    fill: 0,
    stroke: 0,
  };
  /** 所有 moveTo 的坐标（按调用顺序）—— 用来断言"画到了哪个像素"。 */
  const points: number[][] = [];
  const ctx = {
    lineJoin: '',
    lineCap: '',
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    save: () => void (calls.save += 1),
    restore: () => void (calls.restore += 1),
    beginPath: () => void (calls.beginPath += 1),
    moveTo: (x: number, y: number) => {
      calls.moveTo += 1;
      points.push([x, y]);
    },
    lineTo: () => void (calls.lineTo += 1),
    closePath: () => void (calls.closePath += 1),
    fill: () => void (calls.fill += 1),
    stroke: () => void (calls.stroke += 1),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, points, state: ctx };
}

/** 用北京做中心，保证两个省都在视口内。 */
function beijingProjection() {
  return analyticProjection({ lng: 116.9, lat: 39.4 }, 6, 800, 600);
}

describe('renderChinaOutline —— 纯绘制', () => {
  it('每个多边形一次 fill，每个环一次独立 stroke（外环更粗）', () => {
    const collection = stripProvinces(rawCollection());
    const { ctx, calls } = mockCtx();

    renderChinaOutline(ctx, beijingProjection(), collection, {
      color: 'rgba(100,180,255,0.42)',
      majorColor: 'rgba(120,200,255,0.72)',
      lineWidth: 1,
      fillColor: 'rgba(20,40,80,0.28)',
    });

    // 多边形数：北京（1 个 Polygon）+ 天津（MultiPolygon 里的 2 个）= 3 次填充
    expect(calls.fill).toBe(3);
    // 描边：北京 2 环（外环 + 洞）+ 天津 2 个多边形各 1 环 = 4
    expect(calls.stroke).toBe(4);
    // 每环一个 path：fill 用 3 个 + stroke 用 4 个 = 7
    expect(calls.beginPath).toBe(7);
    // closePath 比 beginPath 多 1：填充是把「一个多边形的所有环」塞进同一条 path
    // （3 条），而每个环都要 close 一次（北京 2 + 天津 2 = 4）→ 填充 4 + 描边 4 = 8
    expect(calls.closePath).toBe(8);
    // save/restore 永远成对
    expect(calls.save).toBe(1);
    expect(calls.restore).toBe(1);
  });

  it('最后一条 stroke 的线宽 = lineWidth + 0.5（省界重点），普通环 = lineWidth', () => {
    const collection = stripProvinces(rawCollection());
    const { ctx, state } = mockCtx();
    const widths: number[] = [];
    const original = ctx.stroke.bind(ctx);
    (ctx as unknown as { stroke: () => void }).stroke = () => {
      widths.push(state.lineWidth);
      original();
    };

    renderChinaOutline(ctx, beijingProjection(), collection, {
      color: '#111',
      majorColor: '#fff',
      lineWidth: 1,
      fillColor: 'rgba(0,0,0,0.1)',
    });

    // 每个多边形 2 环：第 1 个环是省界（1.5），第 2 个是洞（1）
    expect(widths).toEqual([1.5, 1, 1.5, 1.5]);
  });

  it('空集合 / 空几何不抛，也不画任何东西', () => {
    const { ctx, calls } = mockCtx();
    expect(() =>
      renderChinaOutline(ctx, beijingProjection(), { type: 'FeatureCollection', features: [] }, {
        color: '#fff',
        lineWidth: 1,
      }),
    ).not.toThrow();
    expect(calls.beginPath).toBe(0);
    expect(calls.stroke).toBe(0);

    // 只有坏几何的原始数据，strip 之后是空集合
    const empty = stripProvinces({
      features: [{ properties: { name: '坏' }, geometry: { type: 'Polygon', coordinates: [] } }],
    });
    expect(() =>
      renderChinaOutline(ctx, beijingProjection(), empty, { color: '#fff', lineWidth: 1 }),
    ).not.toThrow();
  });

  it('整个省都在视口外 → 被裁剪掉（一次 stroke 都不发）', () => {
    const collection = stripProvinces(rawCollection());
    const { ctx, calls } = mockCtx();
    // 把镜头放到南半球，北京/天津都在屏幕外
    const projection = analyticProjection({ lng: 116.9, lat: -33.9 }, 6, 800, 600);
    renderChinaOutline(ctx, projection, collection, { color: '#fff', lineWidth: 1 });
    expect(calls.stroke).toBe(0);
  });
});

describe('loadProvinces —— 失败一律 null', () => {
  it('fetch reject → null', async () => {
    const fetchImpl = vi.fn(() => Promise.reject(new Error('offline'))) as unknown as typeof fetch;
    await expect(loadProvinces('/geo/china-provinces.json', fetchImpl)).resolves.toBeNull();
  });

  it('非 2xx → null', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }),
    ) as unknown as typeof fetch;
    await expect(loadProvinces('/geo/x.json', fetchImpl)).resolves.toBeNull();
  });

  it('空集合 → null（视为"没有可用数据"，让调用方退回格网）', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ type: 'FeatureCollection', features: [] }),
      }),
    ) as unknown as typeof fetch;
    await expect(loadProvinces('/geo/x.json', fetchImpl)).resolves.toBeNull();
  });

  it('正常数据 → 精简后的集合', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(rawCollection()) }),
    ) as unknown as typeof fetch;
    const collection = (await loadProvinces('/geo/x.json', fetchImpl)) as ProvinceCollection;
    expect(collection).not.toBeNull();
    expect(collection.features).toHaveLength(2);
    expect(collection.features[0].properties.name).toBe('北京市');
  });
});

describe('outlineCacheKey —— 视图缓存键', () => {
  const base = { width: 1280, height: 800, cx: 12945343.6, cy: 4861186.2, scaleX: 1200.5, scaleY: 1200.5 };

  it('同一视图 → 同一个键（会命中缓存）', () => {
    expect(outlineCacheKey(base)).toBe(outlineCacheKey({ ...base }));
  });

  it('平移 1px 以上 → 键变了（要重建）', () => {
    const moved = { ...base, cx: base.cx + 1 / base.scaleX };
    expect(outlineCacheKey(moved)).not.toBe(outlineCacheKey(base));
  });

  it('缩放变化 → 键变了', () => {
    expect(outlineCacheKey({ ...base, scaleX: base.scaleX * 2, scaleY: base.scaleY * 2 })).not.toBe(
      outlineCacheKey(base),
    );
  });

  it('亚像素抖动不触发重建（中心量化到 1px）', () => {
    const jitter = { ...base, cx: base.cx + 0.2 / base.scaleX };
    expect(outlineCacheKey(jitter)).toBe(outlineCacheKey(base));
  });

  it('画布尺寸变化 → 键变了', () => {
    expect(outlineCacheKey({ ...base, width: 800 })).not.toBe(outlineCacheKey(base));
  });
});
