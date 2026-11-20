/** 打包流程的坐标链路（W12-4 功能缺口修复）。
 *
 * ## 这条链以前断在哪
 *
 * `pack()` **只**通过 `groupInput.geoHints` 把 `meta.lat/lng/radius` 写进密文
 * （它不读 `Place.lat/lng`）。`packJourney` 原来没传 `geoHints`，于是：
 * 生成的 `.trailshare` 里没有坐标 → 收件人侧 `UnpackedPlace.lat/lng` 全是 undefined
 * → W12-4 的地图/散布**一个点都画不出来**。
 *
 * 这里断言整条链：草稿坐标 → `buildTrip` → `buildGeoHints` → `pack` → 密文 → `unpack`。
 */

import { describe, expect, it } from 'vitest';

import { buildGeoHints, buildTrip, type PhotoDraft, type PlaceDraft, type TripDraft } from './buildTrip';
import { packJourney } from './packFlow';
import { identityFromSeed } from '../share/crypto/keys';
import { unpack } from '../share/unpack';

const MY_SEED = '77'.repeat(32);

function bytes(size: number): ArrayBuffer {
  const out = new Uint8Array(size);
  out.fill(0xd8);
  return out.buffer;
}

function photo(id: string, coords: { lat?: number; lng?: number } = {}): PhotoDraft {
  return { id, name: `${id}.jpg`, bytes: bytes(8), ...coords };
}

/** 一天一地点的最小合法草稿。`coords` 为 null 表示照片没有坐标。 */
function draftWith(
  coords: { lat: number; lng: number } | null,
  extraPlace: { withCoords: boolean } | null = null,
): TripDraft {
  const places: PlaceDraft[] = [
    {
      id: 'p1',
      name: '拙政园',
      tag: 'heritage',
      intensity: 'high',
      photos: [photo('ph1', coords ?? {}), photo('ph2')],
    },
  ];
  if (extraPlace) {
    places.push({
      id: 'p2',
      name: '平江路',
      tag: 'food',
      intensity: 'mid',
      photos: [
        photo('ph3', extraPlace.withCoords ? { lat: 31.3251, lng: 120.6301 } : {}),
        photo('ph4'),
      ],
    });
  }
  return { title: '苏州半日', days: [{ id: 'd1', date: '2026-11-03', places }] };
}

describe('firstGeoOf / buildTrip 的坐标携带', () => {
  it('地点坐标取自该地点第一张带合法坐标的照片', () => {
    const trip = buildTrip(draftWith({ lat: 31.3242, lng: 120.6294 }, { withCoords: true }));
    expect(trip.days[0].places[0].lat).toBeCloseTo(31.3242, 6);
    expect(trip.days[0].places[0].lng).toBeCloseTo(120.6294, 6);
    expect(trip.days[0].places[1].lat).toBeCloseTo(31.3251, 6);
  });

  it('没有坐标的地点不写 lat/lng 字段（不伪造）', () => {
    const trip = buildTrip(draftWith(null));
    expect('lat' in trip.days[0].places[0]).toBe(false);
    expect('lng' in trip.days[0].places[0]).toBe(false);
  });

  it('非法坐标（越界 / NaN）被当作"没有坐标"', () => {
    const draft = draftWith(null);
    draft.days[0].places[0].photos[0].lat = 91;
    draft.days[0].places[0].photos[0].lng = 120;
    const trip = buildTrip(draft);
    expect('lat' in trip.days[0].places[0]).toBe(false);
  });
});

describe('buildGeoHints', () => {
  it('有坐标的地点产出 {lat,lng,radius:0}；radius=0 表示"精确点"', () => {
    const trip = buildTrip(draftWith({ lat: 31.3242, lng: 120.6294 }, { withCoords: false }));
    const hints = buildGeoHints(trip);

    const ids = Object.keys(hints);
    expect(ids).toHaveLength(1);
    expect(hints[ids[0]]).toEqual({ lat: 31.3242, lng: 120.6294, radius: 0 });
    // 第二个地点没有坐标 → 不写键
    expect(hints[trip.days[0].places[1].id]).toBeUndefined();
  });

  it('全部无坐标时返回空表（不报错、不瞎猜）', () => {
    expect(buildGeoHints(buildTrip(draftWith(null)))).toEqual({});
  });
});

describe('packJourney：坐标真的进了包（并且能解回来）', () => {
  it('有坐标草稿 → 包内 PlaceMeta 带 lat/lng，radius === 0', async () => {
    const myIdentity = identityFromSeed(MY_SEED);
    const result = await packJourney({
      draft: draftWith({ lat: 31.3242, lng: 120.6294 }, { withCoords: true }),
      myIdentity,
      displayName: '小旅',
      recipientsRaw: '',
      now: new Date('2026-11-13T00:00:00.000Z'),
      shareId: 's_geo1',
    });

    // 密文里 grep 不到坐标（这正是加密的意义），所以只能解回来验：
    const back = await unpack(result.pkg, { privHex: MY_SEED });
    const places = back.groups[0].places;
    expect(places).toHaveLength(2);
    expect(places[0].lat).toBeCloseTo(31.3242, 6);
    expect(places[0].lng).toBeCloseTo(120.6294, 6);
    expect(places[0].radius).toBe(0);
    expect(places[1].lat).toBeCloseTo(31.3251, 6);
    expect(places[1].radius).toBe(0);

    // 自检也过（签名 + 承诺）
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
  });

  it('全部无坐标 → 打包不报错，解出来的地点没有 lat/lng', async () => {
    const myIdentity = identityFromSeed(MY_SEED);
    const result = await packJourney({
      draft: draftWith(null),
      myIdentity,
      displayName: '小旅',
      recipientsRaw: '',
      now: new Date('2026-11-13T00:00:00.000Z'),
      shareId: 's_geo2',
    });

    expect(buildGeoHints(result.trip)).toEqual({});

    const back = await unpack(result.pkg, { privHex: MY_SEED });
    const place = back.groups[0].places[0];
    expect(place.lat).toBeUndefined();
    expect(place.lng).toBeUndefined();
    expect(place.radius).toBeUndefined();
    expect(result.report.signatureOk).toBe(true);
  });
});
