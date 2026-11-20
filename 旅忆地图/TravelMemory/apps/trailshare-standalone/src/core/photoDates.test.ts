/** W15-1 收尾：用照片日期给「天」定日期。
 *
 * 这一层修的是 QA 判 FAIL 的那条：`takenAt` 已经传到了小应用，
 * 但没有地方拿它去填 `day.date`，于是编辑页日期框永远显示"今天"。
 */

import { describe, expect, it } from 'vitest';

import {
  buildTrip,
  emptyDraft,
  type DayDraft,
  type PhotoDraft,
  type PlaceDraft,
  type TripDraft,
} from './buildTrip';
import { dateOfTakenAt, earliestTakenDate, seedDayDates } from './photoDates';

function photo(takenAt?: string): { takenAt?: string } {
  return takenAt === undefined ? {} : { takenAt };
}

function place(overrides: Partial<PlaceDraft> = {}): PlaceDraft {
  return {
    id: 'pl1',
    name: '西湖',
    tag: 'nature',
    intensity: 'mid',
    photos: [
      {
        id: 'p1',
        name: 'p1.jpg',
        bytes: new Uint8Array([1, 2, 3]).buffer,
        takenAt: '2025-10-04T09:00:00Z',
      },
    ],
    ...overrides,
  };
}

function day(overrides: Partial<DayDraft> = {}): DayDraft {
  return { id: 'd1', date: '2026-11-13', places: [place()], ...overrides };
}

function draft(days: DayDraft[]): TripDraft {
  return { title: '杭州', days };
}

/** 一个"还没放照片"的空天（`emptyDraft()` 的形态）。 */
function emptyDay(id = 'd1'): DayDraft {
  return {
    id,
    date: '2026-11-13',
    places: [{ id: `${id}_p1`, name: '', tag: 'nature', intensity: 'mid', photos: [] }],
  };
}

describe('dateOfTakenAt', () => {
  it('ISO 串取前 10 位（不做时区换算，日历日不漂）', () => {
    // 这条是跨日的关键用例：西五区的 23:30 在东八区已经是次日，
    // 但用户拍的就是 10-04，所以只取前 10 位。
    expect(dateOfTakenAt('2025-10-04T23:30:00-05:00')).toBe('2025-10-04');
    expect(dateOfTakenAt('2025-10-04T12:00:00Z')).toBe('2025-10-04');
    expect(dateOfTakenAt('2025-10-04')).toBe('2025-10-04');
    expect(dateOfTakenAt('  2025-10-04T12:00:00Z  ')).toBe('2025-10-04');
  });

  it('缺失 / 形状不对 / 不存在的日期 → null', () => {
    expect(dateOfTakenAt(undefined)).toBeNull();
    expect(dateOfTakenAt('')).toBeNull();
    expect(dateOfTakenAt('昨天')).toBeNull();
    expect(dateOfTakenAt('2025-1-4')).toBeNull();
    // 形状对但不是真实日期（与 buildTrip.isValidDate 同口径）
    expect(dateOfTakenAt('2025-02-31T00:00:00Z')).toBeNull();
    expect(dateOfTakenAt('2025-13-01T00:00:00Z')).toBeNull();
  });
});

describe('earliestTakenDate', () => {
  it('多张乱序 → 最早那天', () => {
    expect(
      earliestTakenDate([
        photo('2025-10-06T08:00:00Z'),
        photo('2025-10-04T09:00:00Z'),
        photo('2025-10-05T20:00:00Z'),
      ]),
    ).toBe('2025-10-04');
  });

  it('只有一张 / 空数组 / 一张有效都没有', () => {
    expect(earliestTakenDate([photo('2025-10-04T09:00:00Z')])).toBe('2025-10-04');
    expect(earliestTakenDate([])).toBeNull();
    expect(earliestTakenDate([photo(), photo('坏的'), photo('2025-02-31')])).toBeNull();
  });

  it('非法日期被跳过，不污染结果', () => {
    expect(
      earliestTakenDate([photo('2025-02-31T00:00:00Z'), photo('2025-10-09T00:00:00Z')]),
    ).toBe('2025-10-09');
  });
});

describe('seedDayDates', () => {
  it('空天用照片日期回填（这就是 QA FAIL 的场景）', () => {
    const before = draft([emptyDay()]);
    const after = seedDayDates(before, [photo('2025-10-04T09:00:00Z'), photo('2025-10-04T18:00:00Z')]);

    expect(after.days[0].date).toBe('2025-10-04');
    // id 与 places 引用不变（UI 的 key / 选中态不受影响）
    expect(after.days[0].id).toBe('d1');
    expect(after.days[0].places).toBe(before.days[0].places);
    // 原草稿不被改动（纯函数）
    expect(before.days[0].date).toBe('2026-11-13');
  });

  it('已经有照片的天不回填（不覆盖用户已改过的日期）', () => {
    const before = draft([day({ date: '2026-01-01' })]);
    expect(seedDayDates(before, [photo('2025-10-04T09:00:00Z')])).toBe(before);
  });

  it('多天只有空天被填，且只填第一个空天（同一天日期不重复铺开）', () => {
    const before = draft([day(), emptyDay('d2'), emptyDay('d3')]);
    const after = seedDayDates(before, [photo('2025-10-04T09:00:00Z')]);

    // 第 1 天有照片 → 不动；第 2 天（第一个空天）→ 回填；第 3 天 → 原样
    expect(after.days.map((item) => item.date)).toEqual(['2026-11-13', '2025-10-04', '2026-11-13']);
  });

  it('没有可用照片日期 / 已经有目标日期 → 返回原对象（不制造无意义的新引用）', () => {
    const noSeed = draft([emptyDay()]);
    expect(seedDayDates(noSeed, [])).toBe(noSeed);
    expect(seedDayDates(noSeed, [photo('坏的')])).toBe(noSeed);

    const already = draft([{ ...emptyDay(), date: '2025-10-04' }]);
    expect(seedDayDates(already, [photo('2025-10-04T09:00:00Z')])).toBe(already);
  });

  it('对 emptyDraft() 直接可用（首屏导入的真实形态）', () => {
    const after = seedDayDates(emptyDraft(), [photo('2025-10-04T09:00:00Z')]);
    expect(after.days[0].date).toBe('2025-10-04');
    expect(after.days[0].places[0].photos).toEqual([]);
  });

  it('接进导入链路后：6 张 2025-10-04 的照片 → 生成包里那天的 date 就是 2025-10-04', () => {
    // 复刻 App 的顺序：先 seedDayDates，再往第一天的第一个地点塞照片
    const picked: PhotoDraft[] = Array.from({ length: 6 }, (_, index) => ({
      id: `p${index}`,
      name: `p${index}.jpg`,
      bytes: new Uint8Array([index + 1]).buffer,
      takenAt: '2025-10-04T09:00:00Z',
    }));
    const seeded = seedDayDates(emptyDraft(), picked);
    const firstDay = seeded.days[0];
    const filled: TripDraft = {
      ...seeded,
      title: '杭州一日',
      days: [
        { ...firstDay, places: [{ ...firstDay.places[0], name: '西湖', photos: picked }] },
      ],
    };

    const trip = buildTrip(filled);
    // 日期来自照片，不是 todayIso()
    expect(trip.days[0].date).toBe('2025-10-04');
    // 6 张同一地点在生成时被拆成 5+1（W15-2），日期跟着那天走
    expect(trip.days).toHaveLength(1);
    expect(trip.days[0].places.map((item) => item.photos.length)).toEqual([5, 1]);
  });
});
