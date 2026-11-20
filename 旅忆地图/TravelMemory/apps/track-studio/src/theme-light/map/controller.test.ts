// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { initMap } from './controller';

/**
 * jsdom 里跑：没有 `VITE_AMAP_JS_KEY`，`loadAmap()` 立刻判定"未配置"并返回 null，
 * 于是 `createBaseMap` 走降级路径。这条路径正是**没配 Key 的真实用户路径**（D6 / B4），
 * 必须保证：控制器能返回、投影是有限数、destroy 不抛。
 */
describe('initMap（无高德 Key）', () => {
  it('返回 fallback 控制器，calibrate 给出有限投影，destroy 不抛', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);

    const controller = await initMap(container, {
      center: { lng: 120.1459, lat: 30.2431 },
      zoom: 11,
    });

    expect(controller.kind).toBe('fallback');
    expect(controller.coordinateSystem).toBe('wgs84');

    const proj = controller.calibrate(800, 600, 11);
    expect(Number.isFinite(proj.scaleX)).toBe(true);
    expect(Number.isFinite(proj.scaleY)).toBe(true);
    expect(proj.scaleX).toBeGreaterThan(0);
    expect(proj.ox).toBe(400);
    expect(proj.oy).toBe(300);

    // 播放态会 25Hz 调这个东西，必须不抛
    expect(() => controller.setCenter(120.15, 30.24)).not.toThrow();
    expect(() => controller.setZoom(12)).not.toThrow();
    expect(() => controller.setInteractive(false)).not.toThrow();

    // 降级控制器立即就绪（没有瓦片要等）
    await expect(controller.whenReady()).resolves.toBeUndefined();

    expect(() => controller.destroy()).not.toThrow();
    container.remove();
  });

  it('两次 initMap 互不影响（每次挂载都用新容器）', async () => {
    const first = document.createElement('div');
    const second = document.createElement('div');
    document.body.append(first, second);

    const a = await initMap(first, { center: { lng: 120, lat: 30 }, zoom: 10 });
    const b = await initMap(second, { center: { lng: 121, lat: 31 }, zoom: 9 });

    // 降级控制器不往容器里塞 DOM（paintFallback: false，格网由渲染器统一画）
    expect(first.childElementCount).toBe(0);
    expect(second.childElementCount).toBe(0);

    a.destroy();
    b.destroy();
    first.remove();
    second.remove();
  });
});
