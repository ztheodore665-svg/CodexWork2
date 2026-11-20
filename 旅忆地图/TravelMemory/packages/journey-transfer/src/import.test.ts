import { describe, expect, it } from 'vitest';

import { bundleFromTrip } from './export';
import { tripFromBundle } from './import';
import {
  JOURNEY_BUNDLE_VERSION,
  MAX_THUMBNAIL_BYTES,
  type JourneyBundle,
  type JourneyPhotoLike,
} from './types';

const SMALL_THUMBNAIL = `data:image/jpeg;base64,${'A'.repeat(400)}`;

function sampleBundle(): JourneyBundle {
  const photos = new Map<string, JourneyPhotoLike>([['ph1', { thumbnailDataUrl: SMALL_THUMBNAIL }]]);
  return bundleFromTrip(
    {
      title: '苏州一日',
      days: [
        {
          id: 'd1',
          date: '2026-02-01',
          places: [
            {
              id: 'pl1',
              name: '拙政园',
              tag: 'heritage',
              intensity: 'high',
              photos: [{ id: 'ph1', caption: '雨后' }],
            },
          ],
        },
      ],
    },
    photos,
    { exportedAt: '2026-02-02T00:00:00.000Z' },
  );
}

describe('tripFromBundle —— 版本校验', () => {
  it('版本不符时抛中文错误，且错误里带出两个版本号', () => {
    const outdated = { ...sampleBundle(), version: 'journey-2025.9' } as unknown as JourneyBundle;
    expect(() => tripFromBundle(outdated)).toThrow(/不支持的 journey 版本/);
    expect(() => tripFromBundle(outdated)).toThrow(/journey-2025\.9/);
    expect(() => tripFromBundle(outdated)).toThrow(new RegExp(JOURNEY_BUNDLE_VERSION));
  });

  it('版本字段缺失同样抛错', () => {
    const noVersion = { title: 'X', days: [] } as unknown as JourneyBundle;
    expect(() => tripFromBundle(noVersion)).toThrow(/不支持的 journey 版本/);
  });

  it('bundle 不是对象时抛中文错误', () => {
    for (const bad of [null, undefined, 'str', 7]) {
      expect(() => tripFromBundle(bad as unknown as JourneyBundle)).toThrow(/不是一个有效的 journey 包/);
    }
  });
});

describe('tripFromBundle —— 归一化与降级', () => {
  it('days / places / photos 缺失时返回空数组而不是抛错', () => {
    const bundle = { version: JOURNEY_BUNDLE_VERSION } as unknown as JourneyBundle;
    const imported = tripFromBundle(bundle);
    expect(imported.title).toBe('未命名旅程');
    expect(imported.days).toEqual([]);
  });

  it('缺 id 的天 / 地点 / 照片会补出稳定 id', () => {
    const bundle = {
      version: JOURNEY_BUNDLE_VERSION,
      title: 'X',
      exportedAt: '2026-02-02T00:00:00.000Z',
      days: [
        {
          date: '2026-02-01',
          places: [
            {
              name: '某地',
              tag: 'nature',
              intensity: 'mid',
              photos: [{ thumbnailDataUrl: SMALL_THUMBNAIL }],
            },
          ],
        },
      ],
    } as unknown as JourneyBundle;
    const imported = tripFromBundle(bundle);
    expect(imported.days[0].id).toBe('day-1');
    expect(imported.days[0].places[0].id).toBe('place-1');
    expect(imported.days[0].places[0].photos[0].id).toBe('photo-1');
  });

  it('非法 tag 降级为 leisure，非法 intensity 降级为 mid', () => {
    const bundle = {
      version: JOURNEY_BUNDLE_VERSION,
      title: 'X',
      exportedAt: '2026-02-02T00:00:00.000Z',
      days: [
        {
          id: 'd1',
          date: '2026-02-01',
          places: [{ id: 'pl1', name: '某地', tag: 'nope', intensity: 'nope', photos: [] }],
        },
      ],
    } as unknown as JourneyBundle;
    const imported = tripFromBundle(bundle);
    expect(imported.days[0].places[0].tag).toBe('leisure');
    expect(imported.days[0].places[0].intensity).toBe('mid');
  });

  it('超过 200KB 的缩略图在导入侧也会被丢弃（防手改文件撑爆内存）', () => {
    const oversize = `data:image/jpeg;base64,${'A'.repeat(280_000)}`;
    const bundle = {
      version: JOURNEY_BUNDLE_VERSION,
      title: 'X',
      exportedAt: '2026-02-02T00:00:00.000Z',
      days: [
        {
          id: 'd1',
          date: '2026-02-01',
          places: [
            {
              id: 'pl1',
              name: '某地',
              tag: 'nature',
              intensity: 'mid',
              photos: [{ id: 'ph1', thumbnailDataUrl: oversize }],
            },
          ],
        },
      ],
    } as unknown as JourneyBundle;
    const imported = tripFromBundle(bundle);
    expect(imported.days[0].places[0].photos).toEqual([]);
    expect(MAX_THUMBNAIL_BYTES).toBe(200_000);
  });
});
