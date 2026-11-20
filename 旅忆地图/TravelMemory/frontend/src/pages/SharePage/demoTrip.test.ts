/** 示例旅程的不变量测试。
 *
 * 这份数据是 SharePage 骨架的默认输入，pack/unpack 全链路都从它出发。
 * 它坏了（空照片、地点没 ID、两组同 ID）不会在编译期报错，
 * 只会在人类点"生成信物"时变成一个看不懂的异常 —— 所以在这里锁死。
 */

import { describe, expect, it } from 'vitest';
import { demoTrip, demoVisibility } from './demoTrip';

describe('demoTrip', () => {
  it('两天两地点，照片全部非空', () => {
    const trip = demoTrip();
    expect(trip.days).toHaveLength(2);
    const places = trip.days.flatMap((day) => day.places);
    expect(places).toHaveLength(2);
    for (const place of places) {
      expect(place.photos.length).toBeGreaterThan(0);
      for (const photo of place.photos) {
        expect(photo.image.byteLength).toBeGreaterThan(0);
      }
    }
  });

  it('恰好一个地点带 WGS84 坐标（走地理模糊），一个不带（退化原版）', () => {
    const places = demoTrip().days.flatMap((day) => day.places);
    const withCoords = places.filter((p) => typeof p.lat === 'number' && typeof p.lng === 'number');
    const without = places.filter((p) => typeof p.lat !== 'number');
    expect(withCoords).toHaveLength(1);
    expect(without).toHaveLength(1);
    // 坐标必须落在中国值域内（否则境外直通，模糊接口的语义就验不到）
    expect(withCoords[0].lat).toBeGreaterThan(0);
    expect(withCoords[0].lng).toBeGreaterThan(72);
  });

  it('ID 全局唯一（pack 的承诺与解包判重都依赖这一点）', () => {
    const trip = demoTrip();
    const ids = [
      trip.id,
      ...trip.days.map((d) => d.id),
      ...trip.days.flatMap((d) => d.places.map((p) => p.id)),
      ...trip.days.flatMap((d) => d.places.flatMap((p) => p.photos.map((ph) => ph.id))),
    ];
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('demoVisibility', () => {
  it('全可见：四层屏蔽列表都是空的', () => {
    const v = demoVisibility();
    expect(v.hiddenDayIds).toEqual([]);
    expect(v.hiddenPlaceIds).toEqual([]);
    expect(v.hiddenPhotoIds).toEqual([]);
    expect(v.hiddenCaptionPhotoIds).toEqual([]);
    expect(v.tripId).toBe(demoTrip().id);
  });
});
