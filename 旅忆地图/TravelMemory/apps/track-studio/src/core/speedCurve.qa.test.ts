/** QA 补测（阶段 12 / W12-1）：把「变速」这件事真正钉死。
 *
 * 为什么另开一个文件：`speedCurve.test.ts` 里唯一的非匀速用例
 * （"时长不等时…"）只断言 `0 < half < totalMeters`，**线性实现同样满足**，
 * 所以它其实没有证明"非线性"。这里补三条更硬的判据：
 *  ① `q=0.5` 的弧长必须**显著偏离**线性值（> 5% 总长）；
 *  ② 时间分配必须与 `durations` 成比例（播完第 1 段那一刻，弧长≈第 1 段边界）；
 *  ③ 零时长段的退化输入下 `q=0` 仍必须回 0（`speedCurve.ts` 的 ②号不变量）。
 */

import { describe, expect, it } from 'vitest';
import { buildJourney, type Journey, type Photo } from '@travelmemory/trail';
import { haversine } from '@travelmemory/geo';

import {
  DEFAULT_SPEED_CURVE,
  computeSegmentDurations,
  qToDistance,
  totalDurationSeconds,
} from './speedCurve';

function photo(id: string, lat: number, lng: number, takenAt: number): Photo {
  return { id, lat, lng, takenAt, locationSource: 'exif', placeName: id };
}

function journeyOf(points: readonly { lat: number; lng: number }[]): Journey {
  const photos = points.map((point, index) =>
    photo(`p${index}`, point.lat, point.lng, Date.UTC(2026, 3, 18, 1, index)),
  );
  return buildJourney({ id: 'qa-w12-1', title: 'QA 变速', subtitle: '非线性', photos });
}

/** 演示路线（与 `src/photo/demoRoute.ts` 的 `DEMO_ROUTE` 同坐标）。 */
const DEMO_POINTS = [
  { lat: 30.2431, lng: 120.1459 },
  { lat: 30.2408, lng: 120.0985 },
  { lat: 30.2213, lng: 120.1147 },
  { lat: 30.1728, lng: 120.1009 },
  { lat: 30.1878, lng: 120.144 },
];

/** 短段(≈0.48km) + 长段(≈111km)：两段时长必然不同档。 */
const SHORT_THEN_LONG = [
  { lat: 30.2431, lng: 120.1459 },
  { lat: 30.2431, lng: 120.1509 },
  { lat: 31.2431, lng: 120.1459 },
];

describe('W12-1 QA：q → 弧长 真的是非线性的', () => {
  it('两段时长 0.5s / 2s 时，q=0.5 的弧长显著偏离线性（> 5% 总长）', () => {
    const journey = journeyOf(SHORT_THEN_LONG);
    const durations = computeSegmentDurations(journey);
    expect(durations).toEqual([0.5, 2]);

    const total = journey.totalMeters;
    const linearHalf = total * 0.5;
    const half = qToDistance(0.5, journey, durations);

    // 线性实现会正好落在 0.5×total；分段实现应该明显偏前
    expect(Math.abs(half - linearHalf)).toBeGreaterThan(total * 0.05);
    // 前半段时间只走了第一段 + 第二段的 37.5% → 应小于线性值
    expect(half).toBeLessThan(linearHalf);
  });

  it('时间分配与 durations 成比例：播完第 1 段那一刻弧长 ≈ 第 1 段边界', () => {
    const journey = journeyOf(SHORT_THEN_LONG);
    const durations = computeSegmentDurations(journey);

    // 第一段 haversine 距离占折线总长的比例 → 归一化后的弧长边界
    const leg1 = haversine(30.2431, 120.1459, 30.2431, 120.1509);
    const leg2 = haversine(30.2431, 120.1509, 31.2431, 120.1459);
    const boundary1 = (journey.totalMeters * leg1) / (leg1 + leg2);

    // 0.5s / 2.5s = 0.2 → 刚好播完第一段
    const atBoundary = qToDistance(0.2, journey, durations);
    expect(Math.abs(atBoundary - boundary1)).toBeLessThan(journey.totalMeters * 0.002);
  });

  it('演示路线（5 张照片）总时长 4s，且 q 单调不减、端点对齐', () => {
    const journey = journeyOf(DEMO_POINTS);
    const durations = computeSegmentDurations(journey);

    expect(durations).toHaveLength(4);
    expect(totalDurationSeconds(durations)).toBe(4);
    // 四段都在 2~50km 档，所以每段都是 midSegmentSeconds —— 演示数据本身看不出三档差异
    expect(new Set(durations)).toEqual(new Set([DEFAULT_SPEED_CURVE.midSegmentSeconds]));

    expect(qToDistance(0, journey, durations)).toBeCloseTo(0, 6);
    expect(qToDistance(1, journey, durations)).toBeCloseTo(journey.totalMeters, 3);

    let previous = -1;
    for (let step = 0; step <= 200; step += 1) {
      const value = qToDistance(step / 200, journey, durations);
      expect(value).toBeGreaterThanOrEqual(previous - 1e-9);
      previous = value;
    }
  });
});

describe('W12-1 QA：零时长段的退化输入', () => {
  it('短段时长配成 0 时，q=0 仍必须回 0（不能跳到第 1 段末尾）', () => {
    const journey = journeyOf(SHORT_THEN_LONG);
    const durations = computeSegmentDurations(journey, {
      ...DEFAULT_SPEED_CURVE,
      shortSegmentSeconds: 0,
    });
    expect(durations[0]).toBe(0);

    expect(qToDistance(0, journey, durations)).toBeCloseTo(0, 6);
  });
});
