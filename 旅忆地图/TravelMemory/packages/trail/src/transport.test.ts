/** 路段交通工具的单测。
 *
 * 这块最容易错的是**继承规则**：用户只设"景点之间"的那一段，
 * 景点内部连拍的那几张不该单独成段，也不该把上一段的颜色打断。
 */

import { describe, expect, it } from 'vitest';
import { buildJourney } from './journey';
import { DEFAULT_TRANSPORT, normalizeTransport, TRANSPORT_STYLE, TRANSPORT_MODES } from './transport';
import type { Photo } from './types';

const T0 = Date.UTC(2026, 0, 1, 8, 0, 0);
const HOUR = 3600 * 1000;

function photo(
  id: string,
  lng: number,
  lat: number,
  hours: number,
  transportToNext?: Photo['transportToNext'],
): Photo {
  return {
    id,
    lng,
    lat,
    takenAt: T0 + hours * HOUR,
    placeName: id,
    locationSource: 'exif',
    ...(transportToNext ? { transportToNext } : {}),
  };
}

function build(photos: Photo[]) {
  return buildJourney({ id: 'j', title: 't', subtitle: 's', photos });
}

describe('normalizeTransport', () => {
  it('认得四种合法值', () => {
    for (const mode of TRANSPORT_MODES) {
      expect(normalizeTransport(mode)).toBe(mode);
    }
  });

  it('脏数据一律退回默认，不抛错', () => {
    expect(normalizeTransport(undefined)).toBe(DEFAULT_TRANSPORT);
    expect(normalizeTransport(null)).toBe(DEFAULT_TRANSPORT);
    expect(normalizeTransport('teleport')).toBe(DEFAULT_TRANSPORT);
    expect(normalizeTransport(42)).toBe(DEFAULT_TRANSPORT);
  });
});

describe('TRANSPORT_STYLE', () => {
  it('四种交通工具的线型互不相同（否则演示里看不出区别）', () => {
    const signatures = TRANSPORT_MODES.map((m) => {
      const s = TRANSPORT_STYLE[m];
      return `${s.color}|${s.dash.join(',')}|${s.width}`;
    });
    expect(new Set(signatures).size).toBe(TRANSPORT_MODES.length);
  });

  it('每种都有中文名与提示', () => {
    for (const mode of TRANSPORT_MODES) {
      expect(TRANSPORT_STYLE[mode].label.length).toBeGreaterThan(0);
      expect(TRANSPORT_STYLE[mode].hint.length).toBeGreaterThan(0);
    }
  });
});

describe('路段切分', () => {
  it('按相邻照片切段，mode 取自「出发的那一站」', () => {
    const journey = build([
      photo('a', 100, 36, 0, 'drive'),
      photo('b', 101, 36, 5, 'train'),
      photo('c', 102, 36, 10, 'walk'),
    ]);
    expect(journey.segments).toHaveLength(2);
    expect(journey.segments[0].mode).toBe('drive');
    expect(journey.segments[1].mode).toBe('train');
  });

  it('没有显式设置的段继承上一段', () => {
    const journey = build([
      photo('a', 100, 36, 0, 'train'),
      photo('b', 101, 36, 5),
      photo('c', 102, 36, 10),
    ]);
    expect(journey.segments).toHaveLength(2);
    expect(journey.segments[0].mode).toBe('train');
    expect(journey.segments[1].mode).toBe('train');
  });

  it('首段没有设置时用默认值，不会变成 undefined', () => {
    const journey = build([photo('a', 100, 36, 0), photo('b', 101, 36, 5)]);
    expect(journey.segments[0].mode).toBe(DEFAULT_TRANSPORT);
  });

  it('段首尾相接，且覆盖整条轨迹', () => {
    const journey = build([
      photo('a', 100, 36, 0, 'drive'),
      photo('b', 101, 36, 5, 'train'),
      photo('c', 102, 36, 10, 'flight'),
      photo('d', 103, 36, 15, 'walk'),
    ]);
    const segs = journey.segments;
    expect(segs.length).toBe(3);
    expect(segs[0].startS).toBe(0);
    for (let i = 1; i < segs.length; i += 1) {
      expect(segs[i].startS).toBeCloseTo(segs[i - 1].endS, 6);
    }
    expect(segs[segs.length - 1].endS).toBeGreaterThanOrEqual(journey.totalMeters - 1e-6);
  });

  it('同一地点的连拍（相距 < 1m）不单独成段', () => {
    // 三张几乎重合的照片 + 一张远处的
    const journey = build([
      photo('a1', 100.0, 36.0, 0, 'train'),
      photo('a2', 100.0, 36.0, 1, 'walk'),
      photo('a3', 100.0, 36.0, 2, 'flight'),
      photo('b', 101.0, 36.0, 5, 'drive'),
    ]);
    // 前三个点重合 → 只剩一条真正有长度的段
    expect(journey.segments).toHaveLength(1);
    expect(journey.segments[0].mode).toBe('flight'); // 最后一个重合点的设置生效
  });

  it('只有一张已定位照片时没有路段（不崩）', () => {
    const journey = build([photo('a', 100, 36, 0, 'train')]);
    expect(journey.segments).toHaveLength(0);
  });
});
