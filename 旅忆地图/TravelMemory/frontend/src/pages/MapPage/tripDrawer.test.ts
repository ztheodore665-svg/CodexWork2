/** `TripDrawer` 里两个纯函数的测试（Node 环境，无 skip）。
 *
 *  为什么值得单独一份：`formatRange` / `dayOf` 是行程卡片上唯一的时间来源，
 *  而"这趟旅行是什么时候"是用户认出一趟行程的主要依据。
 *  它不碰 DOM，但住在 .tsx 里 —— 抽出来测比在组件测试里断言文本更直接。 */

import { describe, expect, it } from 'vitest';

import { dayOf, formatRange } from './TripDrawer';

describe('dayOf', () => {
  it('取 ISO 串的日期部分', () => {
    expect(dayOf('2026-10-01T02:00:00.000Z')).toBe('2026-10-01');
  });

  it('只有日期时原样返回', () => {
    expect(dayOf('2026-10-01')).toBe('2026-10-01');
  });

  it('null / 空串 / 太短都返回 null', () => {
    expect(dayOf(null)).toBeNull();
    expect(dayOf('')).toBeNull();
    expect(dayOf('2026')).toBeNull();
  });

  it('形状不像日期的返回 null（不放一个脏字符串进 UI）', () => {
    expect(dayOf('昨天/明天')).toBeNull();
    expect(dayOf('2026-13-99T00:00:00Z')).toBeNull();
  });

  it('越界但形状正确的日期也返回 null', () => {
    // 月份 13、日 99：正则放过它，但 new Date 过不去
    expect(dayOf('2026-13-01T00:00:00Z')).toBeNull();
    expect(dayOf('2026-01-32T00:00:00Z')).toBeNull();
  });

  it('2 月 31 日这种"会被 JS 滚动"的也返回 null', () => {
    // 只查 isNaN 抓不到它：new Date('2026-02-31') 会静默变成 3 月 3 日
    expect(dayOf('2026-02-31T00:00:00Z')).toBeNull();
  });

  it('2 月 29 日在非闰年返回 null，闰年放行', () => {
    expect(dayOf('2027-02-29T00:00:00Z')).toBeNull();
    expect(dayOf('2028-02-29T00:00:00Z')).toBe('2028-02-29');
  });
});

describe('formatRange', () => {
  it('起止相同 → 只显示一天', () => {
    expect(formatRange('2026-10-01T00:00:00Z', '2026-10-01T23:00:00Z')).toBe('2026-10-01');
  });

  it('起止不同 → 区间', () => {
    expect(formatRange('2026-10-01T00:00:00Z', '2026-10-03T00:00:00Z')).toBe(
      '2026-10-01 ~ 2026-10-03',
    );
  });

  it('只有起点', () => {
    expect(formatRange('2026-10-01T00:00:00Z', null)).toBe('2026-10-01');
  });

  it('只有终点', () => {
    expect(formatRange(null, '2026-10-03T00:00:00Z')).toBe('2026-10-03');
  });

  it('两端都缺 → 「时间未知」而不是空串', () => {
    expect(formatRange(null, null)).toBe('时间未知');
  });

  it('脏数据也走「时间未知」，不把乱码显示给用户', () => {
    expect(formatRange('乱码', '更乱')).toBe('时间未知');
  });
});
