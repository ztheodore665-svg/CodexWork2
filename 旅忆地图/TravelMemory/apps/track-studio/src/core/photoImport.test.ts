import { describe, expect, it } from 'vitest';

import type { SystemPhoto } from '@travelmemory/photo-source';

import {
  photosWithoutLocation,
  placeNameFromFileName,
  toTrailPhotos,
} from './photoImport';

const THUMB = `data:image/jpeg;base64,${'A'.repeat(64)}`;

function photo(overrides: Partial<SystemPhoto> & { id: string }): SystemPhoto {
  return {
    bytes: new ArrayBuffer(4),
    name: 'IMG_0001.jpg',
    thumbnailDataUrl: THUMB,
    ...overrides,
  };
}

describe('placeNameFromFileName', () => {
  it('去掉扩展名', () => {
    expect(placeNameFromFileName('IMG_0001.jpg')).toBe('IMG_0001');
    expect(placeNameFromFileName('西湖.png')).toBe('西湖');
    expect(placeNameFromFileName('a.b.c.jpeg')).toBe('a.b.c');
  });

  it('无扩展名 / 空名 / 只有扩展名时给可用文案', () => {
    expect(placeNameFromFileName('西湖')).toBe('西湖');
    expect(placeNameFromFileName('   ')).toBe('未命名');
    expect(placeNameFromFileName('.jpg')).toBe('.jpg');
  });
});

describe('toTrailPhotos', () => {
  it('ISO takenAt → 毫秒', () => {
    const [result] = toTrailPhotos([photo({ id: 'p1', takenAt: '2026-04-18T01:00:00.000Z' })]);
    expect(result.takenAt).toBe(Date.UTC(2026, 3, 18, 1, 0, 0));
  });

  it('没有坐标的照片被标为 none 且 lat/lng 为 null（绝不伪造坐标）', () => {
    const [result] = toTrailPhotos([photo({ id: 'p1', takenAt: '2026-04-18T01:00:00.000Z' })]);
    expect(result.lat).toBeNull();
    expect(result.lng).toBeNull();
    expect(result.locationSource).toBe('none');
  });

  it('有坐标时 locationSource 为 exif，坐标原样保留', () => {
    const [result] = toTrailPhotos([
      photo({ id: 'p1', takenAt: '2026-04-18T01:00:00.000Z', lat: 30.2431, lng: 120.1459 }),
    ]);
    expect(result.lat).toBeCloseTo(30.2431, 6);
    expect(result.lng).toBeCloseTo(120.1459, 6);
    expect(result.locationSource).toBe('exif');
  });

  it('按 takenAt 升序（输入乱序也能排好）', () => {
    const result = toTrailPhotos([
      photo({ id: 'c', takenAt: '2026-04-18T03:00:00.000Z' }),
      photo({ id: 'a', takenAt: '2026-04-18T01:00:00.000Z' }),
      photo({ id: 'b', takenAt: '2026-04-18T02:00:00.000Z' }),
    ]);
    expect(result.map((item) => item.id)).toEqual(['a', 'b', 'c']);
  });

  it('thumb 用缩略图 data URL，placeName 用文件名去扩展名', () => {
    const [result] = toTrailPhotos([photo({ id: 'p1', name: '西湖 · 断桥.jpg' })]);
    expect(result.thumb).toBe(THUMB);
    expect(result.placeName).toBe('西湖 · 断桥');
  });

  it('缺 takenAt 的照片给可排序的伪时间戳（不产生 NaN）', () => {
    const result = toTrailPhotos([
      photo({ id: 'a', takenAt: '2026-04-18T01:00:00.000Z' }),
      photo({ id: 'b' }),
      photo({ id: 'c' }),
    ]);
    expect(result.every((item) => Number.isFinite(item.takenAt))).toBe(true);
    const ids = result.map((item) => item.id);
    expect(ids).toContain('a');
    expect(ids).toContain('b');
    expect(ids).toContain('c');
  });

  it('交通工具按 photoId 透传为 transportToNext', () => {
    const transports = new Map([['p1', 'train' as const]]);
    const result = toTrailPhotos(
      [photo({ id: 'p1', takenAt: '2026-04-18T01:00:00.000Z' }), photo({ id: 'p2' })],
      { transports },
    );
    expect(result[0].transportToNext).toBe('train');
    expect(result[1].transportToNext).toBeUndefined();
  });

  it('空输入返回空数组', () => {
    expect(toTrailPhotos([])).toEqual([]);
  });
});

describe('photosWithoutLocation', () => {
  it('只挑出缺坐标的照片，坐标非法（NaN）也算缺', () => {
    const list = [
      photo({ id: 'p1', lat: 30.2, lng: 120.1 }),
      photo({ id: 'p2' }),
      photo({ id: 'p3', lat: Number.NaN, lng: 120.1 }),
    ];
    expect(photosWithoutLocation(list).map((item) => item.id)).toEqual(['p2', 'p3']);
  });
});
