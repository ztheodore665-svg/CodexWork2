/** W15-2：单地点 >5 张照片的自动拆分（协议层 1–5 张不可改，只能在应用层拆）。 */

import { describe, expect, it } from 'vitest';

import {
  buildTrip,
  splitOversizedPlaces,
  validateDraft,
  type DayDraft,
  type PhotoDraft,
  type PlaceDraft,
  type TripDraft,
} from './buildTrip';
import { packJourney } from './packFlow';
import { identityFromSeed } from '../share/crypto/keys';
import { unpack } from '../share/unpack';
import { MAX_PHOTOS_PER_PLACE } from '../share/schema';

const MY_SEED = '91'.repeat(32);

function bytes(size = 8): ArrayBuffer {
  const out = new Uint8Array(size);
  out.fill(0xd8);
  return out.buffer;
}

function photo(index: number): PhotoDraft {
  return { id: `ph_${index}`, name: `ph_${index}.jpg`, bytes: bytes() };
}

/** 一个地点塞 `count` 张照片的最小合法草稿。 */
function draftWith(count: number, name = '西湖'): TripDraft {
  return {
    title: '杭州一日',
    days: [
      {
        id: 'd1',
        date: '2026-11-01',
        places: [
          {
            id: 'pl1',
            name,
            tag: 'nature',
            intensity: 'mid',
            photos: Array.from({ length: count }, (_, index) => photo(index)),
          },
        ],
      },
    ],
  };
}

function firstDay(draft: TripDraft): DayDraft {
  const day = draft.days[0];
  if (!day) throw new Error('草稿没有第一天');
  return day;
}

function firstPlace(draft: TripDraft): PlaceDraft {
  const place = firstDay(draft).places[0];
  if (!place) throw new Error('草稿没有第一个地点');
  return place;
}

describe('splitOversizedPlaces', () => {
  it('协议上限是 5（拆分默认值跟着它走）', () => {
    expect(MAX_PHOTOS_PER_PLACE).toBe(5);
  });

  it('6 张 → 2 个地点（5 + 1），名字后缀正确', () => {
    const source = draftWith(6);
    const split = splitOversizedPlaces(source);
    const places = firstDay(split).places;

    expect(places).toHaveLength(2);
    expect(places.map((place) => place.photos.length)).toEqual([5, 1]);
    expect(places.map((place) => place.name)).toEqual(['西湖（1/2）', '西湖（2/2）']);

    // 第 1 块保留原 id，第 2 块派生 id（避免 key 冲突）
    expect(places[0]?.id).toBe('pl1');
    expect(places[1]?.id).toBe('pl1~2');

    // 照片顺序 = 原顺序，且是同一批引用（不复制 ArrayBuffer）
    expect(places[0]?.photos.map((item) => item.id)).toEqual([
      'ph_0',
      'ph_1',
      'ph_2',
      'ph_3',
      'ph_4',
    ]);
    expect(places[1]?.photos[0]).toBe(source.days[0]?.places[0]?.photos[5]);

    // tag / intensity 继承
    expect(places[1]?.tag).toBe('nature');
    expect(places[1]?.intensity).toBe('mid');
  });

  it('10 张 → 2 个地点（5 + 5）', () => {
    const split = splitOversizedPlaces(draftWith(10));
    const places = firstDay(split).places;

    expect(places).toHaveLength(2);
    expect(places.map((place) => place.photos.length)).toEqual([5, 5]);
    expect(places.map((place) => place.name)).toEqual(['西湖（1/2）', '西湖（2/2）']);
    expect(places[1]?.photos.map((item) => item.id)).toEqual([
      'ph_5',
      'ph_6',
      'ph_7',
      'ph_8',
      'ph_9',
    ]);
  });

  it('12 张 → 3 个地点（5 + 5 + 2）', () => {
    const split = splitOversizedPlaces(draftWith(12));
    const places = firstDay(split).places;

    expect(places.map((place) => place.photos.length)).toEqual([5, 5, 2]);
    expect(places.map((place) => place.name)).toEqual([
      '西湖（1/3）',
      '西湖（2/3）',
      '西湖（3/3）',
    ]);
  });

  it('5 张不变（连同对象引用都不变）', () => {
    const source = draftWith(5);
    const split = splitOversizedPlaces(source);

    expect(split).toBe(source);
    expect(firstPlace(split)).toBe(firstPlace(source));
    expect(firstPlace(split).name).toBe('西湖');
    expect(firstPlace(split).photos).toHaveLength(5);
  });

  it('只拆超限地点，其它地点原样', () => {
    const source = draftWith(6);
    source.days[0]?.places.push({
      id: 'pl2',
      name: '灵隐寺',
      tag: 'heritage',
      intensity: 'high',
      photos: [photo(100), photo(101)],
    });

    const places = firstDay(splitOversizedPlaces(source)).places;
    expect(places.map((place) => place.name)).toEqual([
      '西湖（1/2）',
      '西湖（2/2）',
      '灵隐寺',
    ]);
    // 未超限地点是同一个对象
    expect(places[2]).toBe(source.days[0]?.places[1]);
  });

  it('幂等：拆过一次再拆不会变（不会拆出「西湖（1/2）（1/2）」）', () => {
    const once = splitOversizedPlaces(draftWith(10));
    const twice = splitOversizedPlaces(once);

    expect(twice).toBe(once);
    expect(firstDay(twice).places.map((place) => place.name)).toEqual([
      '西湖（1/2）',
      '西湖（2/2）',
    ]);
  });

  it('支持自定义 limit，非法 limit 回落到协议上限', () => {
    expect(firstDay(splitOversizedPlaces(draftWith(6), 3)).places.map((p) => p.photos.length)).toEqual([
      3, 3,
    ]);
    // NaN / 0 / 负数 → 协议上限 5，不会死循环
    expect(firstDay(splitOversizedPlaces(draftWith(6), Number.NaN)).places).toHaveLength(2);
    expect(firstDay(splitOversizedPlaces(draftWith(6), 0)).places).toHaveLength(2);
  });
});

describe('拆分之后能校验 / 能生成 / 能打包', () => {
  it('6 张草稿：validateDraft 通过（不再报"最多 5 张"）', () => {
    expect(validateDraft(draftWith(6))).toBeNull();
    expect(validateDraft(draftWith(10))).toBeNull();
  });

  it('原有"0 张报错"仍然报错', () => {
    expect(validateDraft(draftWith(0))).toContain('需要 1–5 张照片，现在有 0 张');
  });

  it('buildTrip 直接吃 6 张草稿：2 个地点，照片一张不丢', () => {
    const trip = buildTrip(draftWith(6), { tripId: 't_split1' });
    const places = trip.days[0]?.places ?? [];

    expect(places).toHaveLength(2);
    expect(places.map((place) => place.name)).toEqual(['西湖（1/2）', '西湖（2/2）']);
    expect(places.map((place) => place.photos.length)).toEqual([5, 1]);
    // 照片 id 重新生成（`ph_` 前缀），总量守恒
    expect(places.flatMap((place) => place.photos)).toHaveLength(6);
  });

  it('pack 真的能过：6 张 → 包内 2 个地点（5 + 1），能解回来', async () => {
    const result = await packJourney({
      draft: draftWith(6),
      myIdentity: identityFromSeed(MY_SEED),
      displayName: '小旅',
      recipientsRaw: '',
      now: new Date('2026-11-13T00:00:00.000Z'),
      shareId: 's_split1',
    });

    const back = await unpack(result.pkg, { privHex: MY_SEED });
    const places = back.groups[0].places;

    expect(places).toHaveLength(2);
    expect(places.map((place) => place.name)).toEqual(['西湖（1/2）', '西湖（2/2）']);
    expect(places.map((place) => place.photos.length)).toEqual([5, 1]);
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
  });
});
