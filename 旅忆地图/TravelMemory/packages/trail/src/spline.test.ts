import { describe, expect, it } from 'vitest';
import { catmullRomCentripetal, resampleByArcLength, type Pt } from './spline';

describe('spline', () => {
  const line: Pt[] = [
    { x: 0, y: 0 },
    { x: 1, y: 1 },
    { x: 2, y: 2 },
  ];

  it('三点直线的样条结果仍然共线', () => {
    const smoothed = catmullRomCentripetal(line, 24);
    expect(smoothed.length).toBeGreaterThan(3);
    for (const point of smoothed) {
      expect(Math.abs(point.y - point.x)).toBeLessThan(1e-9);
    }
  });

  it('重采样点数符合约定，弧长单调不减', () => {
    const smoothed = catmullRomCentripetal(line, 24);
    const sampled = resampleByArcLength(smoothed, 500);
    expect(sampled.length).toBe(500);
    for (let i = 1; i < sampled.length; i += 1) {
      expect(sampled[i].s).toBeGreaterThanOrEqual(sampled[i - 1].s);
    }
    expect(sampled[0].s).toBeCloseTo(0, 10);
    expect(sampled[sampled.length - 1].s).toBeGreaterThan(0);
  });

  it('重采样结果仍在直线上', () => {
    const smoothed = catmullRomCentipetalGuard(line);
    const sampled = resampleByArcLength(smoothed, 200);
    for (const point of sampled) {
      expect(Math.abs(point.y - point.x)).toBeLessThan(1e-9);
    }
  });

  it('端点保持不变，空输入安全', () => {
    const smoothed = catmullRomCentripetal(line, 8);
    expect(smoothed[0].x).toBeCloseTo(0, 12);
    expect(smoothed[0].y).toBeCloseTo(0, 12);
    const last = smoothed[smoothed.length - 1];
    expect(last.x).toBeCloseTo(2, 12);
    expect(last.y).toBeCloseTo(2, 12);

    expect(catmullRomCentripetal([], 8)).toHaveLength(0);
    expect(resampleByArcLength([], 10)).toHaveLength(0);
    expect(resampleByArcLength([{ x: 1, y: 1 }], 10)).toHaveLength(1);
  });

  it('重合点不产生 NaN', () => {
    const dup: Pt[] = [
      { x: 0, y: 0 },
      { x: 0, y: 0 },
      { x: 1, y: 0 },
    ];
    const smoothed = catmullRomCentipetalGuard(dup);
    for (const point of smoothed) {
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
    }
  });
});

/** 小包装，避免测试里重复写 24 */
function catmullRomCentipetalGuard(points: Pt[]): Pt[] {
  return catmullRomCentripetal(points, 24);
}

/* ══════════════════════════════════════════════════════════════
 * QA 补充用例：退化控制点数 / 重复点 / 重采样纯净度
 * ══════════════════════════════════════════════════════════════ */

/** 断言一组点里没有 NaN / Infinity */
function expectAllFinite(points: Pt[]): void {
  for (const point of points) {
    expect(Number.isFinite(point.x)).toBe(true);
    expect(Number.isFinite(point.y)).toBe(true);
  }
}

describe('spline 退化情形', () => {
  it('2 个控制点（退化情形）能正常返回，不崩', () => {
    const two: Pt[] = [
      { x: 0.25, y: 0.5 },
      { x: 0.75, y: 0.5 },
    ];
    const smoothed = catmullRomCentripetal(two, 24);
    expect(smoothed.length).toBeGreaterThanOrEqual(2);
    expectAllFinite(smoothed);
    // 端点必须被保留
    expect(smoothed[0].x).toBeCloseTo(0.25, 12);
    expect(smoothed[0].y).toBeCloseTo(0.5, 12);
    const last = smoothed[smoothed.length - 1];
    expect(last.x).toBeCloseTo(0.75, 12);
    expect(last.y).toBeCloseTo(0.5, 12);

    const sampled = resampleByArcLength(smoothed, 100);
    expect(sampled).toHaveLength(100);
    for (const point of sampled) {
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
      expect(Number.isFinite(point.s)).toBe(true);
    }
    expect(sampled[0].s).toBeCloseTo(0, 12);
    expect(sampled[sampled.length - 1].s).toBeGreaterThan(0);
  });

  it('输入含重复点（A, A, B）不产生 NaN', () => {
    const A: Pt = { x: 0.3, y: 0.3 };
    const B: Pt = { x: 0.31, y: 0.31 };
    const smoothed = catmullRomCentripetal([A, A, B], 24);
    expect(smoothed.length).toBeGreaterThan(0);
    expectAllFinite(smoothed);

    const sampled = resampleByArcLength(smoothed, 200);
    expect(sampled).toHaveLength(200);
    for (const point of sampled) {
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
      expect(Number.isFinite(point.s)).toBe(true);
    }
  });

  it('全部控制点重合：重采样退化为同一点，但仍是有限值', () => {
    const same: Pt[] = [
      { x: 0.5, y: 0.5 },
      { x: 0.5, y: 0.5 },
      { x: 0.5, y: 0.5 },
    ];
    const smoothed = catmullRomCentripetal(same, 24);
    expectAllFinite(smoothed);

    const sampled = resampleByArcLength(smoothed, 64);
    expect(sampled).toHaveLength(64);
    for (const point of sampled) {
      expect(point.x).toBe(0.5);
      expect(point.y).toBe(0.5);
      expect(point.s).toBe(0);
      expect(Number.isFinite(point.s)).toBe(true);
    }
  });

  it('重采样点数下界为 2（count 传 0 / 1 不崩）', () => {
    const line: Pt[] = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
    ];
    const smoothed = catmullRomCentripetal(line, 8);
    for (const count of [0, 1, 2]) {
      const sampled = resampleByArcLength(smoothed, count);
      expect(sampled.length).toBeGreaterThanOrEqual(2);
      for (const point of sampled) {
        expect(Number.isFinite(point.x)).toBe(true);
        expect(Number.isFinite(point.y)).toBe(true);
      }
    }
  });
});
