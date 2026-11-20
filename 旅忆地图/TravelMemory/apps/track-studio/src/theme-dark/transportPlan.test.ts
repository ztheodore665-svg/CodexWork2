// 纯函数单测：不碰 DOM、不碰 canvas。W14-3a 的"相邻照片对 → 每段模式"。
import { describe, expect, it } from 'vitest';

import type { TransportMode } from '@travelmemory/trail';
import type { SystemPhoto } from '@travelmemory/photo-source';

import { buildTransportSegments, locatedPhotosInOrder } from './transportPlan';

/** 造一张照片。`takenAt` 用递增的分钟数，保证顺序可预期。 */
function photo(
  id: string,
  minute: number,
  coords: { lat?: number; lng?: number } | null = { lat: 30.1, lng: 120.1 },
): SystemPhoto {
  return {
    id,
    bytes: new ArrayBuffer(0),
    name: `${id}.jpg`,
    takenAt: new Date(Date.UTC(2026, 3, 18, 1, minute)).toISOString(),
    lat: coords?.lat,
    lng: coords?.lng,
    thumbnailDataUrl: 'data:image/png;base64,AA==',
  };
}

describe('transportPlan —— 相邻照片对 → 每段交通工具', () => {
  it('n 张带坐标的照片 → n-1 段，索引与首尾对齐', () => {
    const photos = [photo('a', 0), photo('b', 10), photo('c', 20)];
    const segments = buildTransportSegments(photos);

    expect(segments).toHaveLength(2);
    expect(segments[0]).toMatchObject({ fromId: 'a', toId: 'b', index: 0, key: 'a' });
    expect(segments[1]).toMatchObject({ fromId: 'b', toId: 'c', index: 1, key: 'b' });
  });

  it('没有选过 → 全部是默认自驾', () => {
    const segments = buildTransportSegments([photo('a', 0), photo('b', 10)]);
    expect(segments.map((segment) => segment.mode)).toEqual(['drive']);
  });

  it('选过的段生效，未选过的段不受影响（不会串段）', () => {
    const photos = [photo('a', 0), photo('b', 10), photo('c', 20), photo('d', 30)];
    const transports: Record<string, TransportMode> = { a: 'train', c: 'flight' };
    const segments = buildTransportSegments(photos, transports);

    // 关键断言：模式挂在"出发照片"上 —— 改 b 不该影响 a→b 这一段
    expect(segments.map((segment) => segment.mode)).toEqual(['train', 'drive', 'flight']);
  });

  it('没有坐标的照片不产生段（它不画在轨迹上，配交通工具无意义）', () => {
    const photos = [
      photo('a', 0),
      photo('b', 10),
      photo('gap', 15, null),
      photo('c', 20),
    ];
    const segments = buildTransportSegments(photos, { c: 'walk' });

    expect(segments.map((segment) => `${segment.fromId}->${segment.toId}`)).toEqual([
      'a->b',
      'b->c',
    ]);
    // 'gap' 没坐标，所以 b→c 才是第二段；给 c 配的模式落在不存在的"c→下一张"上
    expect(segments.map((segment) => segment.mode)).toEqual(['drive', 'drive']);
  });

  it('按拍摄时间升序，与入参数组顺序无关（轨迹也是这个顺序）', () => {
    const photos = [photo('c', 20), photo('a', 0), photo('b', 10)];
    const ordered = locatedPhotosInOrder(photos);
    expect(ordered.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(buildTransportSegments(photos).map((segment) => segment.key)).toEqual(['a', 'b']);
  });

  it('只有 1 张（或 0 张）带坐标的照片 → 没有段，不抛错', () => {
    expect(buildTransportSegments([])).toEqual([]);
    expect(buildTransportSegments([photo('a', 0)])).toEqual([]);
    expect(buildTransportSegments([photo('a', 0), photo('b', 10, null)])).toEqual([]);
  });

  it('展示名去掉扩展名；空名退回「未命名」', () => {
    const photos = [photo('a', 0), { ...photo('b', 10), name: '   ' }];
    const segments = buildTransportSegments(photos);
    expect(segments[0].fromName).toBe('a');
    expect(segments[0].toName).toBe('未命名');
  });
});
