import { describe, expect, it } from 'vitest';

import type { SystemPhoto } from '@travelmemory/photo-source';

import { buildJourneyBundle, bundleToJson, isoDateFromMs } from './journeyBuild';

const THUMB = `data:image/jpeg;base64,${'A'.repeat(200)}`;
const RAW_MARKER = 'RAWBYTESMARKER';

function markerBytes(): ArrayBuffer {
  const source = new TextEncoder().encode(`<${RAW_MARKER}>`);
  const buffer = new ArrayBuffer(source.byteLength);
  new Uint8Array(buffer).set(source);
  return buffer;
}

function photo(overrides: Partial<SystemPhoto> & { id: string }): SystemPhoto {
  return {
    bytes: markerBytes(),
    name: '西湖.jpg',
    thumbnailDataUrl: THUMB,
    takenAt: '2026-04-18T01:00:00.000Z',
    lat: 30.2431,
    lng: 120.1459,
    ...overrides,
  };
}

describe('isoDateFromMs', () => {
  it('毫秒 → UTC 的 YYYY-MM-DD', () => {
    expect(isoDateFromMs(Date.UTC(2026, 3, 18, 1, 0, 0))).toBe('2026-04-18');
  });
});

describe('buildJourneyBundle', () => {
  it('journey.json 不含原图字节（只有缩略图与元数据）', () => {
    const bundle = buildJourneyBundle({
      title: '我的旅程',
      photos: [photo({ id: 'p1' })],
      exportedAt: '2026-04-19T00:00:00.000Z',
    });
    const json = bundleToJson(bundle);

    expect(json).not.toContain(RAW_MARKER);
    expect(json).not.toContain('ArrayBuffer');
    expect(json).not.toContain('"bytes"');
    expect(json).toContain('thumbnailDataUrl');
    expect(bundle.version).toBe('journey-2026.1');
    expect(bundle.exportedAt).toBe('2026-04-19T00:00:00.000Z');
  });

  it('同一天按地点分组，连续同名合并成一个 place', () => {
    const bundle = buildJourneyBundle({
      title: 'X',
      photos: [
        photo({ id: 'p1', name: '西湖.jpg' }),
        photo({ id: 'p2', name: '西湖.jpg', takenAt: '2026-04-18T01:30:00.000Z' }),
        photo({ id: 'p3', name: '灵隐寺.jpg', takenAt: '2026-04-18T02:30:00.000Z' }),
        photo({ id: 'p4', name: '龙井村.jpg', takenAt: '2026-04-19T02:30:00.000Z' }),
      ],
    });

    expect(bundle.days).toHaveLength(2);
    expect(bundle.days[0].date).toBe('2026-04-18');
    expect(bundle.days[0].places.map((place) => place.name)).toEqual(['西湖', '灵隐寺']);
    expect(bundle.days[0].places[0].photos).toHaveLength(2);
    expect(bundle.days[1].date).toBe('2026-04-19');
    expect(bundle.days[1].places.map((place) => place.name)).toEqual(['龙井村']);
  });

  it('照片坐标与 takenAt 都进入包（照片级字段）', () => {
    const bundle = buildJourneyBundle({ title: 'X', photos: [photo({ id: 'p1' })] });
    const exported = bundle.days[0].places[0].photos[0];
    expect(exported.takenAt).toBe('2026-04-18T01:00:00.000Z');
    expect(exported.lat).toBeCloseTo(30.2431, 6);
    expect(exported.lng).toBeCloseTo(120.1459, 6);
  });

  it('没有照片时 days 为空但包仍合法', () => {
    const bundle = buildJourneyBundle({ title: '空旅程', photos: [] });
    expect(bundle.days).toEqual([]);
    expect(bundle.title).toBe('空旅程');
  });
});
