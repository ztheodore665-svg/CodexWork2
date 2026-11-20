/** `selectStudioPhotos` 的单测（Node 环境，纯函数，不碰 DOM）。
 *
 *  重点钉两件事：share 模式**只**传这趟行程的照片、且**保持行程顺序**。
 *  顺序错了回放会走回头路，而那种 bug 在界面上看不出"是顺序问题"。 */

import { describe, expect, it } from 'vitest';

import type { PhotoListItem } from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import { selectStudioPhotos } from './selectPhotos';

function photo(id: string): PhotoListItem {
  return {
    id,
    hash: `hash-${id}`,
    takenAt: `2026-04-18T0${id.length}:00:00.000Z`,
    takenAtLocal: `2026-04-18 0${id.length}:00`,
    tzName: 'Asia/Shanghai',
    lat: 30.24,
    lng: 120.15,
    coordSystem: 'WGS84',
    locationSource: 'exif',
    hasLocation: true,
    locationConfidence: 1,
    caption: null,
    createdAt: '2026-04-19T00:00:00.000Z',
  };
}

function trip(photoIds: readonly string[]): TripView {
  return {
    id: 'trip-1',
    title: '洛阳两日',
    startedAt: '2026-04-18T00:00:00.000Z',
    endedAt: '2026-04-19T00:00:00.000Z',
    source: 'manual',
    itemCount: photoIds.length,
    items: photoIds.map((photoId, index) => ({
      id: `item-${index}`,
      photoId,
      takenAt: null,
      lat: null,
      lng: null,
      semanticTag: null,
      semanticLabel: null,
    })),
  };
}

describe('selectStudioPhotos', () => {
  it('track 模式：整个照片库，顺序不变', () => {
    const library = [photo('a'), photo('b'), photo('c')];
    const picked = selectStudioPhotos('track', undefined, library);
    expect(picked.map((item) => item.id)).toEqual(['a', 'b', 'c']);
  });

  it('track 模式返回副本：调用方排序不会改到 props', () => {
    const library = [photo('a'), photo('b')];
    const picked = selectStudioPhotos('track', undefined, library);
    picked.reverse();
    expect(library.map((item) => item.id)).toEqual(['a', 'b']);
  });

  it('share 模式：只取行程里的照片，且保持行程顺序（不是照片库顺序）', () => {
    const library = [photo('a'), photo('b'), photo('c')];
    const picked = selectStudioPhotos('share', trip(['c', 'a']), library);
    expect(picked.map((item) => item.id)).toEqual(['c', 'a']);
  });

  it('share 模式：行程里没有的照片不传', () => {
    const library = [photo('a'), photo('b'), photo('c')];
    const picked = selectStudioPhotos('share', trip(['b']), library);
    expect(picked.map((item) => item.id)).toEqual(['b']);
  });

  it('share 模式：行程引用了已删除的照片 → 跳过，不抛错', () => {
    const library = [photo('a')];
    const picked = selectStudioPhotos('share', trip(['a', 'gone']), library);
    expect(picked.map((item) => item.id)).toEqual(['a']);
  });

  it('share 模式：同一张照片在行程里出现两次 → 只传一次', () => {
    const library = [photo('a'), photo('b')];
    const picked = selectStudioPhotos('share', trip(['a', 'b', 'a']), library);
    expect(picked.map((item) => item.id)).toEqual(['a', 'b']);
  });

  it('share 模式没有 trip（异常调用）→ 空数组，不抛错', () => {
    expect(selectStudioPhotos('share', undefined, [photo('a')])).toEqual([]);
  });

  it('空照片库 / 空行程 → 空数组', () => {
    expect(selectStudioPhotos('track', undefined, [])).toEqual([]);
    expect(selectStudioPhotos('share', trip([]), [])).toEqual([]);
  });
});
