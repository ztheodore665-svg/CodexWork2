/** 变速曲线（W12-1）的单测。
 *
 * 锁死的是四条不变量（见 `speedCurve.ts` 文件头）：
 *  ① 三种距离档得到三种不同时长；
 *  ② `q=0 → 0`、`q=1 → journey.totalMeters`；
 *  ③ 采样 100 个点严格单调不减；
 *  ④ 照片不足 2 张不抛、越界 `q` 被夹住。
 */

import { describe, expect, it } from 'vitest';
import { buildJourney, type Journey, type Photo } from '@travelmemory/trail';

import {
  DEFAULT_SPEED_CURVE,
  computeSegmentDurations,
  qToDistance,
  totalDurationSeconds,
  type SpeedCurveConfig,
} from './speedCurve';

/** 造一张最小可用照片（只填内核真的会读的字段）。 */
function photo(id: string, lat: number | null, lng: number | null, takenAt: number): Photo {
  return {
    id,
    lat,
    lng,
    takenAt,
    locationSource: lat === null || lng === null ? 'none' : 'exif',
    placeName: id,
  };
}

/** 由若干坐标点造一条旅程（时间戳按序号递增，保证排序稳定）。 */
function journeyOf(points: readonly { lat: number; lng: number }[]): Journey {
  const photos = points.map((point, index) =>
    photo(`p${index}`, point.lat, point.lng, Date.UTC(2026, 3, 18, 1, index)),
  );
  return buildJourney({ id: 'w12-1', title: '测试旅程', subtitle: '变速', photos });
}

/** 杭州西湖一带的真实点，方便人肉核对距离量级。 */
const WEST_LAKE = { lat: 30.2431, lng: 120.1459 };

describe('computeSegmentDurations —— 三种距离档', () => {
  it('短段（<2km）/ 中段（2~50km）/ 长段（>=50km）得到三个不同时长', () => {
    // 经度差 0.005° 在北纬 30° 约 0.48km → 短段
    const short = journeyOf([WEST_LAKE, { lat: 30.2431, lng: 120.1509 }]);
    // 灵隐寺：与西湖直线约 4.5km → 中段
    const mid = journeyOf([WEST_LAKE, { lat: 30.2408, lng: 120.0985 }]);
    // 纬度差 1° 约 111km → 长段
    const long = journeyOf([WEST_LAKE, { lat: 31.2431, lng: 120.1459 }]);

    const shortDurations = computeSegmentDurations(short);
    const midDurations = computeSegmentDurations(mid);
    const longDurations = computeSegmentDurations(long);

    expect(shortDurations).toEqual([DEFAULT_SPEED_CURVE.shortSegmentSeconds]);
    expect(midDurations).toEqual([DEFAULT_SPEED_CURVE.midSegmentSeconds]);
    expect(longDurations).toEqual([DEFAULT_SPEED_CURVE.longSegmentSeconds]);

    // 三档必须真的不同，否则"分档"等于没做
    expect(new Set([shortDurations[0], midDurations[0], longDurations[0]]).size).toBe(3);
    expect(shortDurations[0]).toBeLessThan(midDurations[0]);
    expect(midDurations[0]).toBeLessThan(longDurations[0]);
  });

  it('段数 = 照片数 − 1，总时长是各段之和', () => {
    const journey = journeyOf([
      WEST_LAKE,
      { lat: 30.2408, lng: 120.0985 },
      { lat: 30.2213, lng: 120.1147 },
      { lat: 30.1728, lng: 120.1009 },
      { lat: 30.1878, lng: 120.144 },
    ]);
    const durations = computeSegmentDurations(journey);

    expect(journey.photos).toHaveLength(5);
    expect(durations).toHaveLength(4);
    const expectedTotal = durations.reduce((sum, item) => sum + item, 0);
    expect(totalDurationSeconds(durations)).toBeCloseTo(expectedTotal, 6);
    // 演示路线四段都在 2~50km（中段），总时长 4s
    expect(totalDurationSeconds(durations)).toBe(4);
  });

  it('自定义配置生效（同样距离、不同分档参数 → 不同时长）', () => {
    const journey = journeyOf([WEST_LAKE, { lat: 30.2408, lng: 120.0985 }]);
    const custom: SpeedCurveConfig = {
      ...DEFAULT_SPEED_CURVE,
      shortSegmentSeconds: 3,
      midSegmentSeconds: 3,
      longSegmentSeconds: 3,
    };
    expect(computeSegmentDurations(journey, custom)).toEqual([3]);
  });

  it('非法配置回落到默认值，不抛', () => {
    const journey = journeyOf([WEST_LAKE, { lat: 30.2408, lng: 120.0985 }]);
    const broken = {
      shortSegmentSeconds: Number.NaN,
      midSegmentSeconds: -1,
      longSegmentSeconds: Number.POSITIVE_INFINITY,
      shortThresholdKm: Number.NaN,
      midThresholdKm: -5,
    } as SpeedCurveConfig;
    const durations = computeSegmentDurations(journey, broken);
    expect(durations).toHaveLength(1);
    expect(Number.isFinite(durations[0])).toBe(true);
    expect(durations[0]).toBeGreaterThanOrEqual(0);
  });
});

describe('qToDistance —— 端点与单调性', () => {
  const journey = journeyOf([
    WEST_LAKE,
    { lat: 30.2408, lng: 120.0985 },
    { lat: 30.1728, lng: 120.1009 },
  ]);
  const durations = computeSegmentDurations(journey);

  it('q=0 → 0，q=1 → journey.totalMeters', () => {
    expect(qToDistance(0, journey, durations)).toBeCloseTo(0, 6);
    expect(qToDistance(1, journey, durations)).toBeCloseTo(journey.totalMeters, 3);
    expect(journey.totalMeters).toBeGreaterThan(0);
  });

  it('采样 100 个点严格单调不减', () => {
    let previous = Number.NEGATIVE_INFINITY;
    for (let step = 0; step <= 100; step += 1) {
      const distance = qToDistance(step / 100, journey, durations);
      expect(distance).toBeGreaterThanOrEqual(previous - 1e-9);
      expect(Number.isFinite(distance)).toBe(true);
      previous = distance;
    }
    expect(previous).toBeCloseTo(journey.totalMeters, 3);
  });

  it('越界 q 被夹到 [0, 1]', () => {
    expect(qToDistance(-3.5, journey, durations)).toBeCloseTo(0, 6);
    expect(qToDistance(9, journey, durations)).toBeCloseTo(journey.totalMeters, 3);
    expect(qToDistance(Number.NaN, journey, durations)).toBeCloseTo(0, 6);
  });

  it('durations 为空时退回线性 q × totalMeters', () => {
    expect(qToDistance(0.5, journey, [])).toBeCloseTo(journey.totalMeters * 0.5, 6);
    expect(qToDistance(1, journey, [])).toBeCloseTo(journey.totalMeters, 6);
  });

  it('时长不等时，长段在时间上占到更多秒（前 50% 时间走不到一半距离判据不适用时仍是有限数）', () => {
    // 第一段短、第二段长：总时长 0.5 + 2 = 2.5s，q=0.5 时刚好播完第一段
    const mixed = journeyOf([
      WEST_LAKE,
      { lat: 30.2431, lng: 120.1509 },
      { lat: 31.2431, lng: 120.1459 },
    ]);
    const mixedDurations = computeSegmentDurations(mixed);
    expect(mixedDurations).toEqual([0.5, 2]);
    const halfTime = qToDistance(0.5, mixed, mixedDurations);
    expect(halfTime).toBeGreaterThan(0);
    expect(halfTime).toBeLessThan(mixed.totalMeters);
  });
});

describe('退化输入', () => {
  it('单张照片：没有段，qToDistance 不抛且恒为 0（totalMeters 为 0）', () => {
    const journey = journeyOf([WEST_LAKE]);
    expect(computeSegmentDurations(journey)).toEqual([]);
    expect(totalDurationSeconds([])).toBe(0);
    expect(() => qToDistance(0.42, journey, [])).not.toThrow();
    expect(qToDistance(0.42, journey, [])).toBe(0);
  });

  it('零照片：不抛', () => {
    const journey = journeyOf([]);
    expect(computeSegmentDurations(journey)).toEqual([]);
    expect(qToDistance(0.7, journey, [])).toBe(0);
    expect(totalDurationSeconds([])).toBe(0);
  });

  it('所有照片同一位置：边界仍单调，q=1 能到终点', () => {
    const journey = journeyOf([WEST_LAKE, WEST_LAKE, WEST_LAKE]);
    const durations = computeSegmentDurations(journey);
    expect(durations).toHaveLength(2);
    let previous = -1;
    for (let step = 0; step <= 20; step += 1) {
      const distance = qToDistance(step / 20, journey, durations);
      expect(distance).toBeGreaterThanOrEqual(previous - 1e-9);
      previous = distance;
    }
  });
});
