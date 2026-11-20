/** 位置补全纯逻辑（W12-2b）的单测。
 *
 * 覆盖三类：
 *  ① 坐标校验的边界（±90 / ±180）与非法类型（`NaN`、字符串、`null`）；
 *  ② 「待补列表」的筛选：只认后端的 `hasLocation === false`；
 *  ③ 补标成功后的本地移除（纯函数、不改入参）。
 */

import { describe, expect, it } from 'vitest';

import type { PhotoListItem } from '../api/photos';
import {
  displayNameOf,
  isValidLatLng,
  pendingCount,
  removePhotoById,
  selectPendingPhotos,
  toLocationFixItems,
} from './validate';

/** 造一张最小可用的照片库条目（只填断言会用到的字段）。 */
function photo(overrides: Partial<PhotoListItem> & { id: string }): PhotoListItem {
  return {
    hash: `hash-${overrides.id}`,
    takenAt: null,
    takenAtLocal: null,
    tzName: null,
    lat: null,
    lng: null,
    coordSystem: 'WGS84',
    locationSource: null,
    hasLocation: false,
    locationConfidence: null,
    caption: null,
    createdAt: '2026-10-01T00:00:00',
    ...overrides,
  };
}

describe('isValidLatLng', () => {
  it('边界合法：±90 / ±180', () => {
    expect(isValidLatLng(90, 180)).toBe(true);
    expect(isValidLatLng(-90, -180)).toBe(true);
    expect(isValidLatLng(0, 0)).toBe(true);
    expect(isValidLatLng(30.2431, 120.1459)).toBe(true);
  });

  it('越界一位小数非法', () => {
    expect(isValidLatLng(90.0001, 0)).toBe(false);
    expect(isValidLatLng(-90.0001, 0)).toBe(false);
    expect(isValidLatLng(0, 180.0001)).toBe(false);
    expect(isValidLatLng(0, -180.0001)).toBe(false);
  });

  it('NaN / Infinity / 字符串 / null / undefined 非法', () => {
    expect(isValidLatLng(Number.NaN, 120)).toBe(false);
    expect(isValidLatLng(30, Number.NaN)).toBe(false);
    expect(isValidLatLng(Number.NEGATIVE_INFINITY, 120)).toBe(false);
    expect(isValidLatLng('30.2', '120.1')).toBe(false);
    expect(isValidLatLng(null, null)).toBe(false);
    expect(isValidLatLng(undefined, 120)).toBe(false);
    expect(isValidLatLng(null, 120)).toBe(false);
  });
});

describe('selectPendingPhotos', () => {
  it('只挑 hasLocation === false 的，顺序不变', () => {
    const photos = [
      photo({ id: 'a', hasLocation: true, lat: 30, lng: 120 }),
      photo({ id: 'b' }),
      photo({ id: 'c', hasLocation: true, lat: 31, lng: 121 }),
      photo({ id: 'd' }),
    ];
    expect(selectPendingPhotos(photos).map((item) => item.id)).toEqual(['b', 'd']);
    expect(pendingCount(photos)).toBe(2);
  });

  it('只有 lat 没有 lng 但 hasLocation 为 false 也算待补（脏数据）', () => {
    const photos = [photo({ id: 'x', lat: 30, lng: null, hasLocation: false })];
    expect(pendingCount(photos)).toBe(1);
  });

  it('全部已定位时返回空数组', () => {
    const photos = [photo({ id: 'a', hasLocation: true, lat: 30, lng: 120 })];
    expect(selectPendingPhotos(photos)).toEqual([]);
    expect(pendingCount(photos)).toBe(0);
  });

  it('空库不抛', () => {
    expect(selectPendingPhotos([])).toEqual([]);
    expect(pendingCount([])).toBe(0);
  });
});

describe('removePhotoById', () => {
  it('移除后待补数量减一，且不改入参', () => {
    const photos = [photo({ id: 'a' }), photo({ id: 'b' }), photo({ id: 'c' })];
    const snapshot = JSON.stringify(photos);
    const next = removePhotoById(photos, 'b');

    expect(next.map((item) => item.id)).toEqual(['a', 'c']);
    expect(JSON.stringify(photos)).toBe(snapshot);
    expect(next).not.toBe(photos);
  });

  it('未知 id 不改变内容', () => {
    const photos = [photo({ id: 'a' })];
    expect(removePhotoById(photos, 'zzz').map((item) => item.id)).toEqual(['a']);
  });
});

describe('toLocationFixItems / displayNameOf', () => {
  it('caption 优先，缺失退回 id', () => {
    expect(displayNameOf(photo({ id: 'p1', caption: '西湖落日' }))).toBe('西湖落日');
    expect(displayNameOf(photo({ id: 'p2', caption: '   ' }))).toBe('p2');
    expect(displayNameOf(photo({ id: 'p3' }))).toBe('p3');
  });

  it('待补项不带半截坐标，缩略图字段留给异步取图', () => {
    const items = toLocationFixItems([
      photo({ id: 'a', hasLocation: true, lat: 30, lng: 120, caption: '已定位' }),
      photo({ id: 'b', caption: '待补' }),
      photo({ id: 'c', lat: 30, lng: null, hasLocation: false }),
    ]);

    expect(items.map((item) => item.photoId)).toEqual(['b', 'c']);
    expect(items[0].name).toBe('待补');
    expect(items[0].thumbnailDataUrl).toBe('');
    expect(items[0].lat).toBeUndefined();
    expect(items[1].lat).toBeUndefined();
  });
});
