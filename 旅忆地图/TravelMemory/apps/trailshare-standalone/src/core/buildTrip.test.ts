import { describe, expect, it } from 'vitest';

import {
  buildTrip,
  emptyDraft,
  isValidDate,
  todayIso,
  totalPhotoBytes,
  validateDraft,
  validateDraftStrict,
  TAG_OPTIONS,
  INTENSITY_OPTIONS,
  type DayDraft,
  type PlaceDraft,
  type TripDraft,
} from './buildTrip';

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function photo(id = 'p_a', size = 4): { id: string; name: string; bytes: ArrayBuffer } {
  return { id, name: `${id}.jpg`, bytes: bytes(new Array(size).fill(0xff)) };
}

function place(overrides: Partial<PlaceDraft> = {}): PlaceDraft {
  return {
    id: 'pl1',
    name: '西湖',
    tag: 'nature',
    intensity: 'mid',
    photos: [photo()],
    ...overrides,
  };
}

function day(overrides: Partial<DayDraft> = {}): DayDraft {
  return { id: 'd1', date: '2026-11-01', places: [place()], ...overrides };
}

function draft(overrides: Partial<TripDraft> = {}): TripDraft {
  return { title: '杭州两日', days: [day()], ...overrides };
}

describe('选项表', () => {
  it('标签是六类中文名，值就是协议值', () => {
    expect(TAG_OPTIONS.map((item) => item.value)).toEqual([
      'heritage',
      'nature',
      'watertown',
      'metropolis',
      'food',
      'leisure',
    ]);
    expect(TAG_OPTIONS[0].label).toBe('人文历史');
  });

  it('强度是三档，带星星', () => {
    expect(INTENSITY_OPTIONS.map((item) => item.value)).toEqual(['low', 'mid', 'high']);
    expect(INTENSITY_OPTIONS[2].stars).toBe('★★★');
  });
});

describe('isValidDate / todayIso', () => {
  it('接受真实日期，拒绝不存在的日期与错格式', () => {
    expect(isValidDate('2026-11-01')).toBe(true);
    expect(isValidDate('2026-02-30')).toBe(false);
    expect(isValidDate('2026-13-01')).toBe(false);
    expect(isValidDate('2026-1-1')).toBe(false);
    expect(isValidDate('')).toBe(false);
  });

  it('todayIso 是 YYYY-MM-DD', () => {
    expect(todayIso()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('validateDraft', () => {
  it('合法草稿返回 null', () => {
    expect(validateDraft(draft())).toBeNull();
  });

  it('标题为空报错', () => {
    expect(validateDraft(draft({ title: '   ' }))).toContain('标题');
  });

  it('0 张照片报错（协议要求 1–5 张）', () => {
    const bad = draft({ days: [day({ places: [place({ photos: [] })] })] });
    expect(validateDraft(bad)).toContain('需要 1–5 张照片，现在有 0 张');
  });

  it('6 张照片不再报错：先自动拆分（W15-2，协议仍是 1–5 张）', () => {
    const six = Array.from({ length: 6 }, (_item, index) => photo(`p_${index}`));
    const oversized = draft({ days: [day({ places: [place({ photos: six })] })] });
    expect(validateDraft(oversized)).toBeNull();
  });

  it('validateDraftStrict 不吃拆分：超限时给"自动拆分"兜底文案', () => {
    const six = Array.from({ length: 6 }, (_item, index) => photo(`p_${index}`));
    const oversized = draft({ days: [day({ places: [place({ photos: six })] })] });
    expect(validateDraftStrict(oversized)).toContain('已自动拆分');
  });

  it('5 张照片是允许的上限', () => {
    const five = Array.from({ length: 5 }, (_item, index) => photo(`p_${index}`));
    expect(validateDraft(draft({ days: [day({ places: [place({ photos: five })] })] }))).toBeNull();
  });

  it('非法 tag / intensity 报错', () => {
    const badTag = draft({
      days: [day({ places: [place({ tag: 'unknown' as unknown as PlaceDraft['tag'] })] })],
    });
    expect(validateDraft(badTag)).toContain('标签不合法');

    const badIntensity = draft({
      days: [day({ places: [place({ intensity: 'extreme' as unknown as PlaceDraft['intensity'] })] })],
    });
    expect(validateDraft(badIntensity)).toContain('强度不合法');
  });

  it('日期格式不对报错，并指出是第几天', () => {
    const bad = draft({ days: [day({ date: '2026/11/01' })] });
    expect(validateDraft(bad)).toContain('第 1 天的日期不对');
  });

  it('一天没有地点 / 地点没名字报错', () => {
    expect(validateDraft(draft({ days: [day({ places: [] })] }))).toContain('还没有地点');
    expect(
      validateDraft(draft({ days: [day({ places: [place({ name: '  ' })] })] })),
    ).toContain('还没有名字');
  });

  it('空字节照片报错', () => {
    const bad = draft({
      days: [day({ places: [place({ photos: [{ id: 'p_x', name: 'x.jpg', bytes: new ArrayBuffer(0) }] })] })],
    });
    expect(validateDraft(bad)).toContain('字节是空的');
  });
});

describe('buildTrip', () => {
  it('校验失败时抛中文错误', () => {
    expect(() => buildTrip(draft({ title: '' }))).toThrow(/请先填写旅程标题/);
    expect(() =>
      buildTrip(draft({ days: [day({ places: [place({ photos: [] })] })] })),
    ).toThrow(/需要 1–5 张照片/);
  });

  it('id 形状符合协议约定（t_ / d1_ / p1_ / ph_）', () => {
    const trip = buildTrip(draft());
    expect(trip.id).toMatch(/^t_[0-9a-f]{8}$/);
    expect(trip.days[0].id).toMatch(/^d1_[0-9a-f]{6}$/);
    expect(trip.days[0].places[0].id).toMatch(/^p1_[0-9a-f]{6}$/);
    expect(trip.days[0].places[0].photos[0].id).toMatch(/^ph_[0-9a-f]{8}$/);
  });

  it('保留标题/日期/标签/强度/配文，coverIndex 为 0', () => {
    const trip = buildTrip(
      draft({
        days: [
          day({
            date: '2026-11-02',
            places: [
              place({
                name: ' 拙政园 ',
                label: '上午',
                tag: 'heritage',
                intensity: 'high',
                photos: [{ id: 'p_a', name: 'a.jpg', bytes: bytes([1, 2, 3]), caption: ' 廊桥 ' }],
              }),
            ],
          }),
        ],
      }),
    );
    const place0 = trip.days[0].places[0];
    expect(trip.title).toBe('杭州两日');
    expect(trip.days[0].date).toBe('2026-11-02');
    expect(place0.name).toBe('拙政园');
    expect(place0.label).toBe('上午');
    expect(place0.tag).toBe('heritage');
    expect(place0.intensity).toBe('high');
    expect(place0.coverIndex).toBe(0);
    expect(place0.photos[0].caption).toBe('廊桥');
    expect(place0.photos[0].image.byteLength).toBe(3);
  });

  it('没有配文时不写 caption 字段（避免空串进密文）', () => {
    const trip = buildTrip(draft());
    expect('caption' in trip.days[0].places[0].photos[0]).toBe(false);
    expect('label' in trip.days[0].places[0]).toBe(false);
  });

  it('buildTrip 会重新生成照片 id（与草稿 id 无关）', () => {
    const trip = buildTrip(draft());
    expect(trip.days[0].places[0].photos[0].id).not.toBe('p_a');
  });
});

describe('totalPhotoBytes / emptyDraft', () => {
  it('累加所有照片字节', () => {
    expect(totalPhotoBytes(draft())).toBe(4);
    const two = draft({
      days: [day({ places: [place({ photos: [photo('a', 3), photo('b', 7)] })] })],
    });
    expect(totalPhotoBytes(two)).toBe(10);
  });

  it('emptyDraft 是一天一个空地点，日期是今天', () => {
    const empty = emptyDraft();
    expect(empty.title).toBe('');
    expect(empty.days).toHaveLength(1);
    expect(empty.days[0].date).toBe(todayIso());
    expect(empty.days[0].places[0].photos).toEqual([]);
    // 它自己当然过不了校验（标题空、没照片），这是预期的初始态
    expect(validateDraft(empty)).not.toBeNull();
  });
});
