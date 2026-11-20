/** `tripViewToTrip` 的单测（Node 环境）。
 *
 * 重点不是"字段有没有搬过去"，而是三条容易出错的语义：
 * 多天要拆成多个 Day、没有坐标的照片也要进包（只是不写 lat/lng）、
 * 标签缺失时退回 `leisure` 而不是自己猜一个。
 */

import { describe, expect, it } from 'vitest';

import type { TripItemView, TripView } from '../../services/api/trips';
import type { ThumbnailStore } from '../../types/native';
import {
  MISSING_SEMANTIC_TAG_HINT,
  UNDATED_DAY,
  countMissingSemanticTags,
  dayKeyOf,
  describeMissingSemanticTags,
  hydrateTripPhotos,
  tripViewToTrip,
} from './tripSource';

function item(overrides: Partial<TripItemView> = {}): TripItemView {
  return {
    id: 'i1',
    photoId: 'ph1',
    takenAt: '2026-10-01T02:00:00Z',
    lat: null,
    lng: null,
    semanticTag: null,
    semanticLabel: null,
    ...overrides,
  };
}

function trip(items: TripItemView[], overrides: Partial<TripView> = {}): TripView {
  return {
    id: 't1',
    title: '洛阳两日',
    startedAt: '2026-10-01T00:00:00Z',
    endedAt: '2026-10-02T00:00:00Z',
    source: 'cluster',
    itemCount: items.length,
    items,
    ...overrides,
  };
}

function fakeThumbs(entries: Record<string, string>): ThumbnailStore {
  return {
    async get(assetId: string) {
      const text = entries[assetId];
      return text === undefined ? null : new TextEncoder().encode(text).buffer;
    },
    async put() {},
    async clear() {},
    sizeHint: Object.keys(entries).length,
  };
}

describe('tripViewToTrip', () => {
  it('多天聚成多个 Day，并按日期升序', () => {
    const view = trip([
      item({ id: 'i2', photoId: 'ph2', takenAt: '2026-10-02T03:00:00Z' }),
      item({ id: 'i1', photoId: 'ph1', takenAt: '2026-10-01T02:00:00Z' }),
      item({ id: 'i3', photoId: 'ph3', takenAt: '2026-10-01T09:00:00Z' }),
    ]);

    const result = tripViewToTrip(view);

    expect(result.days.map((day) => day.date)).toEqual(['2026-10-01', '2026-10-02']);
    expect(result.days[0].places).toHaveLength(2);
    // 同一天里按拍摄时间升序，不是按后端返回顺序
    expect(result.days[0].places.map((place) => place.id)).toEqual(['t1:i1', 't1:i3']);
    expect(result.days[1].places).toHaveLength(1);
    expect(result.id).toBe('t1');
    expect(result.title).toBe('洛阳两日');
    expect(result.createdAt).toBe('2026-10-01T00:00:00Z');
  });

  it('没有坐标的 item 也会进包，只是不写 lat/lng', () => {
    const view = trip([
      item({ id: 'i1', photoId: 'ph1', lat: 34.56, lng: 112.47, semanticLabel: '龙门石窟' }),
      item({ id: 'i2', photoId: 'ph2', lat: null, lng: null, semanticLabel: '洛邑古城' }),
    ]);

    const result = tripViewToTrip(view);
    const [withCoords, withoutCoords] = result.days[0].places;

    expect(withCoords.lat).toBe(34.56);
    expect(withCoords.lng).toBe(112.47);
    // 「不写」而不是写 0/NaN —— prepareShare 的 hasCoords 会据此跳过地理模糊旁路
    expect('lat' in withoutCoords).toBe(false);
    expect('lng' in withoutCoords).toBe(false);
    expect(withoutCoords.name).toBe('洛邑古城');
  });

  it('semanticTag 缺失或非六类时退回 leisure，不做猜测式分类', () => {
    const view = trip([
      item({ id: 'i1', photoId: 'ph1', semanticTag: null }),
      item({ id: 'i2', photoId: 'ph2', semanticTag: 'heritage' }),
      item({ id: 'i3', photoId: 'ph3', semanticTag: '海底捞' }), // 后端给了个新词
    ]);

    const tags = tripViewToTrip(view).days[0].places.map((place) => place.tag);
    expect(tags).toEqual(['leisure', 'heritage', 'leisure']);
  });

  it('每个 Place 至少 1 张照片，id 用后端 photoId', () => {
    const result = tripViewToTrip(trip([item()]));
    const [place] = result.days[0].places;

    expect(place.photos).toHaveLength(1);
    expect(place.photos[0].id).toBe('ph1');
    expect(place.photos[0].image.byteLength).toBe(0); // 占位，由 hydrateTripPhotos 填
  });

  it('semanticLabel 缺失时用「未命名地点」', () => {
    expect(tripViewToTrip(trip([item()])).days[0].places[0].name).toBe('未命名地点');
  });

  it('后端标题为空时给「未命名旅程」', () => {
    expect(tripViewToTrip(trip([item()], { title: '' })).title).toBe('未命名旅程');
  });

  it('没有拍摄时间的照片归到行程起始日，而不是被丢掉', () => {
    const result = tripViewToTrip(
      trip([
        item({ id: 'i1', photoId: 'ph1', takenAt: '2026-10-01T02:00:00Z' }),
        item({ id: 'i2', photoId: 'ph2', takenAt: null }),
      ]),
    );
    expect(result.days).toHaveLength(1);
    expect(result.days[0].date).toBe('2026-10-01');
    expect(result.days[0].places).toHaveLength(2);
  });

  it('连行程起始时间都没有时用哨兵日期，Day.date 仍是 YYYY-MM-DD', () => {
    const result = tripViewToTrip(
      trip([item({ takenAt: null })], { startedAt: null, endedAt: null }),
    );
    expect(result.days[0].date).toBe(UNDATED_DAY);
    expect(result.days[0].date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('dayKeyOf', () => {
  it('取前 10 位；两个都没有时返回 null', () => {
    expect(dayKeyOf('2026-10-01T02:00:00Z', '2026-10-01T10:00:00')).toBe('2026-10-01');
    expect(dayKeyOf(null, '2026-10-02T10:00:00')).toBe('2026-10-02');
    expect(dayKeyOf(null, null)).toBeNull();
    expect(dayKeyOf('short', null)).toBeNull();
  });
});

describe('行程 → 打包（接入层不破坏协议层）', () => {
  it('tripViewToTrip 的产物能直接喂给 prepareShare', async () => {
    const { prepareShare } = await import('../../services/share/prepare');
    const { loadOrCreateIdentity } = await import('./identity');

    const view = trip([
      item({
        id: 'i1',
        photoId: 'ph1',
        lat: 34.5636,
        lng: 112.4699,
        semanticTag: 'heritage',
        semanticLabel: '龙门石窟',
      }),
    ]);
    const built = (await hydrateTripPhotos(tripViewToTrip(view), fakeThumbs({ ph1: 'jpeg' }))).trip;
    const identity = loadOrCreateIdentity();

    const result = await prepareShare(
      {
        groups: [
          {
            trip: built,
            visibility: {
              id: `${built.id}::g1`,
              tripId: built.id,
              groupId: 'g1',
              hiddenDayIds: [],
              hiddenPlaceIds: [],
              hiddenPhotoIds: [],
              hiddenCaptionPhotoIds: [],
            },
            recipientPubkeys: [identity.pubkey],
          },
        ],
        senderIdentity: identity,
        displayName: '我',
        chainEnabled: false,
      },
      {
        // 假模糊函数：`share/` 本身不碰网络，注入点与生产一致
        obfuscate: async (lat, lng) => ({
          lat: lat + 0.01,
          lng: lng + 0.01,
          radius: 800,
          mode: 'approx',
          epsilon: 1,
          coord_system: 'WGS84',
        }),
      },
    );

    const [group] = result.pkg.groups;
    expect(group.payload.places).toHaveLength(1);
    // tag 是明文且参与承诺 —— 后端给的 heritage 必须原样带出去
    expect(group.payload.places[0].tag).toBe('heritage');
    expect(group.wrappedKeys).toHaveLength(1);
    expect(result.pkg.commitment.hash).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(result.warnings).toEqual([]);
  });

  it('没有坐标的行程打包出来不带地理提示，也不产生 warning', async () => {
    const { prepareShare } = await import('../../services/share/prepare');
    const { loadOrCreateIdentity } = await import('./identity');

    const source = tripViewToTrip(trip([item({ id: 'i1', photoId: 'ph1' })]));
    const built = (await hydrateTripPhotos(source, fakeThumbs({ ph1: 'jpeg' }))).trip;
    const identity = loadOrCreateIdentity();
    let obfuscateCalls = 0;

    const result = await prepareShare(
      {
        groups: [
          {
            trip: built,
            visibility: {
              id: `${built.id}::g1`,
              tripId: built.id,
              groupId: 'g1',
              hiddenDayIds: [],
              hiddenPlaceIds: [],
              hiddenPhotoIds: [],
              hiddenCaptionPhotoIds: [],
            },
            recipientPubkeys: [identity.pubkey],
          },
        ],
        senderIdentity: identity,
        displayName: '我',
        chainEnabled: false,
      },
      {
        obfuscate: async (lat, lng) => {
          obfuscateCalls += 1;
          return { lat, lng, radius: 500, mode: 'approx', epsilon: 1, coord_system: 'WGS84' };
        },
      },
    );

    // 没坐标的 place 不该去调地理模糊旁路（那等于向服务端暴露"用户其实去了哪"）
    expect(obfuscateCalls).toBe(0);
    expect(result.pkg.groups[0].payload.places).toHaveLength(1);
  });
});

describe('hydrateTripPhotos', () => {
  it('把空字节换成缩略图缓存里的真实字节', async () => {
    const source = tripViewToTrip(trip([item({ photoId: 'ph1' })]));
    const { trip: filled, droppedPlaces } = await hydrateTripPhotos(
      source,
      fakeThumbs({ ph1: 'jpeg-bytes' }),
    );

    const bytes = filled.days[0].places[0].photos[0].image;
    expect(new TextDecoder().decode(bytes)).toBe('jpeg-bytes');
    expect(droppedPlaces).toBe(0);
    // 不改动原对象
    expect(source.days[0].places[0].photos[0].image.byteLength).toBe(0);
  });

  it('缓存里没有时丢掉该地点并计数 —— pack 会拒绝 0 字节照片', async () => {
    const source = tripViewToTrip(trip([item({ photoId: 'missing' })]));
    const { trip: filled, droppedPlaces } = await hydrateTripPhotos(source, fakeThumbs({}));

    expect(filled.days).toHaveLength(0);
    expect(droppedPlaces).toBe(1);
  });

  it('只丢缺图的那几个地点，其余照常打包', async () => {
    const source = tripViewToTrip(
      trip([
        item({ id: 'i1', photoId: 'ph1', takenAt: '2026-10-01T02:00:00Z' }),
        item({ id: 'i2', photoId: 'ph2', takenAt: '2026-10-02T02:00:00Z' }),
      ]),
    );
    const { trip: filled, droppedPlaces } = await hydrateTripPhotos(
      source,
      fakeThumbs({ ph2: 'only-second' }),
    );

    expect(droppedPlaces).toBe(1);
    expect(filled.days).toHaveLength(1);
    expect(new TextDecoder().decode(filled.days[0].places[0].photos[0].image)).toBe('only-second');
  });

  it('缩略图库读失败（IndexedDB 挂了）按"取不到"处理，不抛错', async () => {
    const broken: ThumbnailStore = {
      async get() {
        throw new Error('IndexedDB 不可用');
      },
      async put() {},
      async clear() {},
      sizeHint: 0,
    };
    const source = tripViewToTrip(trip([item({ photoId: 'ph1' })]));
    const { trip: filled, droppedPlaces } = await hydrateTripPhotos(source, broken);

    expect(filled.days).toHaveLength(0);
    expect(droppedPlaces).toBe(1);
  });
});

describe('语义标签缺失的兜底提示', () => {
  it('数出 semanticTag 为 null 的地点', () => {
    const view = trip([
      item({ id: 'i1', semanticTag: null }),
      item({ id: 'i2', semanticTag: 'heritage' }),
      item({ id: 'i3', semanticTag: null }),
    ]);

    expect(countMissingSemanticTags(view)).toBe(2);
  });

  it('非法标签不算"缺失"——那是后端给了新词，是另一回事', () => {
    const view = trip([item({ semanticTag: 'not-a-tag' })]);
    expect(countMissingSemanticTags(view)).toBe(0);
  });

  it('没有缺失时提示为 null（UI 不必判空字符串）', () => {
    const view = trip([item({ semanticTag: 'heritage' })]);
    expect(countMissingSemanticTags(view)).toBe(0);
    expect(describeMissingSemanticTags(0)).toBeNull();
  });

  it('有缺失时把"暂缺"与默认标签都说清楚', () => {
    const notice = describeMissingSemanticTags(3);
    expect(notice).toContain(MISSING_SEMANTIC_TAG_HINT);
    // 兜底标签的中文名必须出现：否则用户不知道"默认标签"到底是哪个
    expect(notice).toContain('休闲度假');
    expect(notice).toContain('3');
  });

  it('缺失的地点在打包时被静默退回 leisure —— 这正是必须提示的原因', () => {
    const result = tripViewToTrip(trip([item({ id: 'i1', semanticTag: null })]));
    expect(result.days[0].places[0].tag).toBe('leisure');
  });
});
