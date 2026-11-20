/** 位置补全纯逻辑（W12-2a）的单测。
 *
 * 重点断言三件事：
 *  ① 坐标校验的**边界**（±90 / ±180）与非法类型（`NaN`、字符串、`null`）；
 *  ② `applyLocation` 写回后，待补列表真的少一张；
 *  ③ 写回是纯函数：入参数组不被修改。
 */

import { describe, expect, it } from 'vitest';

import {
  applyLocation,
  initialViewOf,
  isValidLatLng,
  nextPendingId,
  pendingLocationIds,
  toLocationFixItems,
  type LocationFixItem,
} from './validate';

interface TestPhoto {
  id: string;
  name: string;
  thumbnailDataUrl: string;
  lat?: number;
  lng?: number;
}

function photo(id: string, lat?: number, lng?: number): TestPhoto {
  return { id, name: `${id}.jpg`, thumbnailDataUrl: '', ...(lat !== undefined ? { lat } : {}), ...(lng !== undefined ? { lng } : {}) };
}

describe('isValidLatLng', () => {
  it('边界值：±90 纬度、±180 经度都合法', () => {
    expect(isValidLatLng(90, 180)).toBe(true);
    expect(isValidLatLng(-90, -180)).toBe(true);
    expect(isValidLatLng(0, 0)).toBe(true);
    expect(isValidLatLng(30.2431, 120.1459)).toBe(true);
  });

  it('越界一位小数就非法', () => {
    expect(isValidLatLng(90.0001, 0)).toBe(false);
    expect(isValidLatLng(-90.0001, 0)).toBe(false);
    expect(isValidLatLng(0, 180.0001)).toBe(false);
    expect(isValidLatLng(0, -180.0001)).toBe(false);
  });

  it('NaN / Infinity / 字符串 / null / undefined 一律非法', () => {
    expect(isValidLatLng(Number.NaN, 120)).toBe(false);
    expect(isValidLatLng(30, Number.NaN)).toBe(false);
    expect(isValidLatLng(Number.POSITIVE_INFINITY, 120)).toBe(false);
    expect(isValidLatLng('30', '120')).toBe(false);
    expect(isValidLatLng(30, '120')).toBe(false);
    expect(isValidLatLng(null, null)).toBe(false);
    expect(isValidLatLng(undefined, undefined)).toBe(false);
    expect(isValidLatLng({}, [])).toBe(false);
  });
});

describe('applyLocation', () => {
  it('写回后该照片带上坐标，待补数量减一', () => {
    const photos = [photo('a'), photo('b'), photo('c')];
    expect(pendingLocationIds(photos)).toEqual(['a', 'b', 'c']);

    const next = applyLocation(photos, 'b', 30.2408, 120.0985);

    expect(pendingLocationIds(next)).toEqual(['a', 'c']);
    const updated = next.find((item) => item.id === 'b');
    expect(updated?.lat).toBe(30.2408);
    expect(updated?.lng).toBe(120.0985);
  });

  it('是纯函数：入参数组与元素都不被修改', () => {
    const photos = [photo('a'), photo('b')];
    const snapshot = JSON.stringify(photos);
    const next = applyLocation(photos, 'a', 1, 2);

    expect(JSON.stringify(photos)).toBe(snapshot);
    expect(next).not.toBe(photos);
    expect(next[0]).not.toBe(photos[0]);
  });

  it('非法坐标 / 未知 id 一律不写（返回内容不变的副本）', () => {
    const photos = [photo('a'), photo('b')];
    expect(pendingLocationIds(applyLocation(photos, 'a', Number.NaN, 120))).toEqual(['a', 'b']);
    expect(pendingLocationIds(applyLocation(photos, 'a', 91, 120))).toEqual(['a', 'b']);
    expect(pendingLocationIds(applyLocation(photos, 'zzz', 30, 120))).toEqual(['a', 'b']);
    expect(pendingLocationIds(applyLocation(photos, '', 30, 120))).toEqual(['a', 'b']);
  });

  it('把一张已经定位的照片再写一次也能覆盖（重复补标）', () => {
    const photos = [photo('a', 10, 20)];
    const next = applyLocation(photos, 'a', 11, 21);
    expect(next[0].lat).toBe(11);
    expect(next[0].lng).toBe(21);
  });
});

describe('nextPendingId', () => {
  it('按顺序给出下一张；全部补完后返回 null', () => {
    const photos = [photo('a'), photo('b')];
    expect(nextPendingId(photos, null)).toBe('a');
    expect(nextPendingId(photos, 'a')).toBe('a');

    const afterA = applyLocation(photos, 'a', 30, 120);
    expect(nextPendingId(afterA, 'a')).toBe('b');

    const afterBoth = applyLocation(afterA, 'b', 31, 121);
    expect(nextPendingId(afterBoth, 'b')).toBeNull();
  });

  it('按列表顺序前进，到底回绕到第一张（不跳来跳去）', () => {
    // b 已补完（不再待补），剩下 a / c
    const afterB = [photo('a'), photo('b', 30, 120), photo('c')];
    // 刚补完 b：下一张应该是它后面的 c
    expect(nextPendingId(afterB, 'b')).toBe('c');

    // 只剩 a：刚补完最后一张 c → 后面没有了 → 回绕到第一张待补
    const afterC = [photo('a'), photo('b', 30, 120), photo('c', 31, 121)];
    expect(nextPendingId(afterC, 'c')).toBe('a');

    // 未知 id：退回第一张待补
    expect(nextPendingId(afterB, 'zzz')).toBe('a');
  });
});

describe('toLocationFixItems', () => {
  it('只保留缺坐标的照片，并保持顺序', () => {
    const items: LocationFixItem[] = toLocationFixItems([
      photo('a', 30, 120),
      photo('b'),
      { id: 'c', name: 'c.jpg', thumbnailDataUrl: 'data:image/png;base64,xx', lat: 1 },
    ]);

    expect(items.map((item) => item.photoId)).toEqual(['b', 'c']);
    expect(items[1].thumbnailDataUrl).toBe('data:image/png;base64,xx');
    // 只有 lat 没有 lng = 脏数据，算"缺坐标"，且不把半截坐标带进去
    expect(items[1].lat).toBeUndefined();
    expect(items[1].lng).toBeUndefined();
  });
});

describe('initialViewOf', () => {
  it('有定位照片时取重心并放大到街道级', () => {
    const view = initialViewOf([photo('a', 30, 120), photo('b', 32, 122)]);
    expect(view.center.lat).toBeCloseTo(31, 6);
    expect(view.center.lng).toBeCloseTo(121, 6);
    expect(view.zoom).toBeGreaterThan(8);
  });

  it('一张定位照片都没有时退回全国视野', () => {
    const view = initialViewOf([photo('a'), photo('b')]);
    expect(view.zoom).toBeLessThan(6);
    expect(Number.isFinite(view.center.lat)).toBe(true);
    expect(Number.isFinite(view.center.lng)).toBe(true);
  });
});
