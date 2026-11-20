import { describe, expect, it } from 'vitest';

import type { PhotoListItem } from '../../../services/api/photos';
import type { TripItemView, TripView } from '../../../services/api/trips';
import { buildReplayJourney, parseWallClock, toLocationSource } from './adapter';

function photo(id: string, over: Partial<PhotoListItem> = {}): PhotoListItem {
  return {
    id,
    hash: `hash-${id}`,
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
    createdAt: '2026-07-01T00:00:00.000Z',
    ...over,
  };
}

function item(photoId: string, over: Partial<TripItemView> = {}): TripItemView {
  return {
    id: `item-${photoId}`,
    photoId,
    takenAt: null,
    lat: 36.6,
    lng: 101.8,
    semanticTag: null,
    semanticLabel: null,
    ...over,
  };
}

function trip(items: TripItemView[], over: Partial<TripView> = {}): TripView {
  return {
    id: 'trip-1',
    title: '青甘大环线',
    startedAt: null,
    endedAt: null,
    source: 'cluster',
    itemCount: items.length,
    items,
    ...over,
  };
}

describe('parseWallClock', () => {
  it('把无时区墙钟当成 UTC 解析，不做时区换算', () => {
    expect(parseWallClock('2026-07-12T08:00:00')).toBe(Date.UTC(2026, 6, 12, 8, 0, 0));
  });

  it('非法/空值返回 null', () => {
    expect(parseWallClock(null)).toBeNull();
    expect(parseWallClock('')).toBeNull();
    expect(parseWallClock('   ')).toBeNull();
    expect(parseWallClock('不是时间')).toBeNull();
  });
});

describe('toLocationSource', () => {
  it('后端四值映射到内核三值', () => {
    expect(toLocationSource('exif')).toBe('exif');
    expect(toLocationSource('user')).toBe('manual');
    expect(toLocationSource('inferred')).toBe('manual');
    expect(toLocationSource('none')).toBe('none');
    expect(toLocationSource(null)).toBe('none');
  });
});

describe('buildReplayJourney', () => {
  it('优先用 takenAtLocal，且是墙钟 UTC 语义（毫秒精确）', () => {
    const journey = buildReplayJourney(
      trip([
        item('p1'),
        item('p2', { lat: 36.7, lng: 100.5 }),
      ]),
      [
        // item.takenAt 故意给一个不同的时刻，用来证明优先级
        photo('p1', { takenAtLocal: '2026-07-12T08:00:00', takenAt: '2026-07-12T23:00:00.000Z' }),
        photo('p2', { takenAtLocal: '2026-07-12T10:00:00' }),
      ],
    );

    expect(journey).not.toBeNull();
    expect(journey?.photos[0].takenAt).toBe(Date.UTC(2026, 6, 12, 8, 0, 0));
    expect(journey?.photos[1].takenAt).toBe(Date.UTC(2026, 6, 12, 10, 0, 0));
  });

  it('takenAtLocal 缺失时回退到 item.takenAt', () => {
    const journey = buildReplayJourney(
      trip([
        item('p1', { takenAt: '2026-07-12T09:30:00.000Z' }),
        item('p2', { takenAt: '2026-07-12T11:30:00.000Z', lat: 36.7, lng: 100.5 }),
      ]),
      [photo('p1'), photo('p2')],
    );

    expect(journey?.photos[0].takenAt).toBe(Date.UTC(2026, 6, 12, 9, 30, 0));
    expect(journey?.photos[1].takenAt).toBe(Date.UTC(2026, 6, 12, 11, 30, 0));
  });

  it('两者都缺时回退到 photo.createdAt', () => {
    const journey = buildReplayJourney(
      trip([item('p1'), item('p2', { lat: 36.7, lng: 100.5 })]),
      [
        photo('p1', { createdAt: '2026-07-12T12:00:00.000Z' }),
        photo('p2', { createdAt: '2026-07-12T13:00:00.000Z' }),
      ],
    );

    expect(journey?.photos[0].takenAt).toBe(Date.UTC(2026, 6, 12, 12, 0, 0));
    expect(journey?.photos[1].takenAt).toBe(Date.UTC(2026, 6, 12, 13, 0, 0));
  });

  it('三种时间都拿不到的项被丢弃，不会产生 takenAt = 0', () => {
    const journey = buildReplayJourney(
      trip([
        item('p1'),
        // 这条没有墙钟、没有行程时间、createdAt 也是空串
        item('pBad', { lat: 36.65, lng: 101.7 }),
        item('p2', { lat: 36.7, lng: 100.5 }),
      ]),
      [
        photo('p1', { takenAtLocal: '2026-07-12T08:00:00' }),
        photo('pBad', { createdAt: '' }),
        photo('p2', { takenAtLocal: '2026-07-12T10:00:00' }),
      ],
    );

    expect(journey).not.toBeNull();
    expect(journey?.photos).toHaveLength(2);
    expect(journey?.photos.map((p) => p.id)).toEqual(['p1', 'p2']);
    for (const p of journey?.photos ?? []) {
      expect(p.takenAt).toBeGreaterThan(0);
    }
  });

  it('只保留有经纬度的项，不足 2 个点返回 null', () => {
    const onlyOneLocated = buildReplayJourney(
      trip([item('p1'), item('p2', { lat: null, lng: null })]),
      [photo('p1', { takenAtLocal: '2026-07-12T08:00:00' }), photo('p2')],
    );
    expect(onlyOneLocated).toBeNull();

    const bothLocated = buildReplayJourney(
      trip([item('p1'), item('p2', { lat: null, lng: null }), item('p3', { lat: 36.7, lng: 100.5 })]),
      [
        photo('p1', { takenAtLocal: '2026-07-12T08:00:00' }),
        photo('p2'),
        photo('p3', { takenAtLocal: '2026-07-12T10:00:00' }),
      ],
    );
    expect(bothLocated?.photos.map((p) => p.id)).toEqual(['p1', 'p3']);
  });

  it('locationSource 按后端取值映射，不带时间也不影响映射', () => {
    const journey = buildReplayJourney(
      trip([item('p1'), item('p2', { lat: 36.7, lng: 100.5 })]),
      [
        photo('p1', { takenAtLocal: '2026-07-12T08:00:00', locationSource: 'user' }),
        photo('p2', { takenAtLocal: '2026-07-12T10:00:00', locationSource: 'exif' }),
      ],
    );

    expect(journey?.photos.map((p) => p.locationSource)).toEqual(['manual', 'exif']);
  });

  it('照片库缺这张照片时：时间用行程快照、来源落 none', () => {
    const journey = buildReplayJourney(
      trip([
        item('p1', { takenAt: '2026-07-12T08:00:00.000Z' }),
        item('p2', { takenAt: '2026-07-12T10:00:00.000Z', lat: 36.7, lng: 100.5 }),
      ]),
      [],
    );

    expect(journey?.photos.map((p) => p.locationSource)).toEqual(['none', 'none']);
    expect(journey?.photos[0].takenAt).toBe(Date.UTC(2026, 6, 12, 8, 0, 0));
  });

  it('segments 非空且首尾相接、覆盖整条轨迹', () => {
    const journey = buildReplayJourney(
      trip([
        item('p1'),
        item('p2', { lat: 36.5333, lng: 100.5 }),
        item('p3', { lat: 36.7, lng: 99.0833 }),
      ]),
      [
        photo('p1', { takenAtLocal: '2026-07-12T08:00:00' }),
        photo('p2', { takenAtLocal: '2026-07-12T10:00:00' }),
        photo('p3', { takenAtLocal: '2026-07-12T12:00:00' }),
      ],
    );

    expect(journey).not.toBeNull();
    const segments = journey?.segments ?? [];
    expect(segments.length).toBeGreaterThan(0);
    expect(segments[0].startS).toBe(0);
    expect(segments[segments.length - 1].endS).toBeCloseTo(journey?.totalMeters ?? 0, 6);
    for (let i = 1; i < segments.length; i += 1) {
      expect(segments[i].startS).toBeCloseTo(segments[i - 1].endS, 6);
    }
  });

  it('照片按时间升序（乱序输入也要排好）', () => {
    const journey = buildReplayJourney(
      trip([
        item('pLate', { takenAt: '2026-07-12T18:00:00.000Z', lat: 36.7, lng: 100.5 }),
        item('pEarly', { takenAt: '2026-07-12T06:00:00.000Z' }),
      ]),
      [photo('pLate'), photo('pEarly')],
    );

    expect(journey?.photos.map((p) => p.id)).toEqual(['pEarly', 'pLate']);
  });
});
