/** 可见性四层层叠过滤：屏蔽 = 不打包。 */

import { describe, expect, it } from 'vitest';

import {
  applyVisibility,
  countVisibleCaptions,
  countVisiblePhotos,
  countVisiblePlaces,
  emptyVisibility,
} from './filter';
import { makeTrip, makeVisibility } from './fixtures';

describe('applyVisibility', () => {
  it('全可见时输出全部 3 个地点、4 张照片、3 条配文', () => {
    const result = applyVisibility(makeTrip(), makeVisibility());
    expect(result.places.map((p) => p.place.id)).toEqual(['p_1', 'p_2', 'p_3']);
    expect(countVisiblePlaces(result)).toBe(3);
    expect(countVisiblePhotos(result)).toBe(4);
    expect(countVisibleCaptions(result)).toBe(3);
  });

  it('Day 层：屏蔽一天 → 那天的地点全部消失', () => {
    const result = applyVisibility(makeTrip(), makeVisibility({ hiddenDayIds: ['d_1'] }));
    expect(result.places.map((p) => p.place.id)).toEqual(['p_3']);
  });

  it('Place 层：屏蔽一个地点 → 只有它消失', () => {
    const result = applyVisibility(makeTrip(), makeVisibility({ hiddenPlaceIds: ['p_2'] }));
    expect(result.places.map((p) => p.place.id)).toEqual(['p_1', 'p_3']);
  });

  it('Photo 层：屏蔽一张照片 → 该地点的 photos 变少', () => {
    const result = applyVisibility(makeTrip(), makeVisibility({ hiddenPhotoIds: ['ph_1'] }));
    const p1 = result.places.find((p) => p.place.id === 'p_1');
    expect(p1?.photos.map((f) => f.photo.id)).toEqual(['ph_2']);
  });

  it('Caption 层：屏蔽配文 → 照片还在，配文不打包', () => {
    const result = applyVisibility(makeTrip(), makeVisibility({ hiddenCaptionPhotoIds: ['ph_1'] }));
    const p1 = result.places.find((p) => p.place.id === 'p_1');
    expect(p1?.photos.length).toBe(2);
    expect(p1?.photos[0].includeCaption).toBe(false);
    expect(p1?.photos[1].includeCaption).toBe(true);
    expect(countVisibleCaptions(result)).toBe(2);
  });

  it('一个地点的照片全被屏蔽 → 整条不打包（不允许出现空地点卡）', () => {
    const result = applyVisibility(makeTrip(), makeVisibility({ hiddenPhotoIds: ['ph_1', 'ph_2'] }));
    expect(result.places.map((p) => p.place.id)).toEqual(['p_2', 'p_3']);
  });

  it('屏蔽状态是每组独立的：改一组不影响另一组', () => {
    const trip = makeTrip();
    const g1 = applyVisibility(trip, makeVisibility({ groupId: 'g1', hiddenDayIds: ['d_2'] }));
    const g2 = applyVisibility(trip, makeVisibility({ groupId: 'g2' }));
    expect(countVisiblePlaces(g1)).toBe(2);
    expect(countVisiblePlaces(g2)).toBe(3);
  });

  it('没有屏蔽状态时视为全可见（不写库也能预览）', () => {
    const result = applyVisibility(makeTrip(), undefined);
    expect(countVisiblePlaces(result)).toBe(3);
  });

  it('emptyVisibility 给出一份全可见的空状态，id 为 tripId::groupId', () => {
    const state = emptyVisibility('t_1', 'g2');
    expect(state.id).toBe('t_1::g2');
    expect(applyVisibility(makeTrip(), state).places.length).toBe(3);
  });

  it('过滤结果带上 Day 的日期（打包时写进 PlaceMeta.date）', () => {
    const result = applyVisibility(makeTrip(), makeVisibility());
    expect(result.places[0].date).toBe('2026-10-01');
    expect(result.places[2].date).toBe('2026-10-02');
  });
});
