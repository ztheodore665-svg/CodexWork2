/** `journey-import` 的单测（Node 环境，纯函数 + 假依赖，不碰网络与 DOM）。
 *
 *  这里钉住的是本阶段最容易"看起来对、其实丢数据"的几条规则：
 *  版本校验、展开顺序、缩略图合规性（>200KB / 非 data URL）、文件名清洗，
 *  以及 `importJourneyToMain` 的调用顺序（先传图 → 再建行程 → 再挂图 → 最后补坐标）。
 */

import { describe, expect, it, vi } from 'vitest';

import type {
  ImportedPhoto,
  ImportPhotosResult,
  PhotoListItem,
} from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import { toBase64 } from '../../utils/base64';
import {
  MAX_THUMBNAIL_BYTES,
  buildJourneyPhotoFilename,
  importJourneyToMain,
  planJourneyImport,
} from './journeyToTrip';

const VERSION = 'journey-2026.1';

/** 造一张合法缩略图 data URL（`bytes` 字节的假 JPEG）。 */
function thumb(bytes = 64): string {
  return `data:image/jpeg;base64,${toBase64(new Uint8Array(bytes))}`;
}

interface RawPhoto {
  id: string;
  thumbnailDataUrl: string;
  caption?: string;
  takenAt?: string;
  lat?: number;
  lng?: number;
}

function rawJourney(days: Array<{ date: string; places: Array<{ name: string; photos: RawPhoto[] }> }>): unknown {
  return {
    version: VERSION,
    title: '洛阳两日',
    exportedAt: '2026-04-20T00:00:00.000Z',
    days: days.map((day, dayIndex) => ({
      id: `d${dayIndex + 1}`,
      date: day.date,
      places: day.places.map((place, placeIndex) => ({
        id: `p${dayIndex + 1}_${placeIndex + 1}`,
        name: place.name,
        tag: 'heritage',
        intensity: 'mid',
        photos: place.photos,
      })),
    })),
  };
}

/** 两天 / 三地点 / 五张照片（验收口径里的那一条）。 */
function fivePhotos(): unknown {
  return rawJourney([
    {
      date: '2026-04-18',
      places: [
        { name: '龙门石窟', photos: [{ id: 'a', thumbnailDataUrl: thumb() }, { id: 'b', thumbnailDataUrl: thumb() }] },
        { name: '洛邑古城', photos: [{ id: 'c', thumbnailDataUrl: thumb(), lat: 34.6, lng: 112.4 }] },
      ],
    },
    {
      date: '2026-04-19',
      places: [
        {
          name: '白马寺',
          photos: [
            { id: 'd', thumbnailDataUrl: thumb(), caption: '  香火  ' },
            { id: 'e', thumbnailDataUrl: thumb() },
          ],
        },
      ],
    },
  ]);
}

function importedPhoto(id: string): ImportedPhoto {
  return {
    id,
    assetId: `asset-${id}`,
    hash: `hash-${id}`,
    takenAt: null,
    takenAtLocal: null,
    tzName: null,
    lat: null,
    lng: null,
    coordSystem: 'WGS84',
    locationSource: null,
    locationConfidence: null,
    semanticTag: null,
    semanticLabel: null,
  };
}

function importResult(ids: readonly string[]): ImportPhotosResult {
  return {
    imported: ids.length,
    duplicates: 0,
    failed: 0,
    photos: ids.map(importedPhoto),
    tripIds: [],
  };
}

function tripView(id: string, photoIds: readonly string[] = []): TripView {
  return {
    id,
    title: '洛阳两日',
    startedAt: null,
    endedAt: null,
    source: 'manual',
    itemCount: photoIds.length,
    items: photoIds.map((photoId, index) => ({
      id: `item-${index}`,
      photoId,
      takenAt: null,
      lat: null,
      lng: null,
      semanticTag: null,
      semanticLabel: null,
    })),
  };
}

function photoListItem(id: string): PhotoListItem {
  return {
    id,
    hash: `hash-${id}`,
    takenAt: null,
    takenAtLocal: null,
    tzName: null,
    lat: 34.6,
    lng: 112.4,
    coordSystem: 'WGS84',
    locationSource: 'manual',
    hasLocation: true,
    locationConfidence: 1,
    caption: null,
    createdAt: '2026-04-20T00:00:00.000Z',
  };
}

describe('planJourneyImport —— 校验', () => {
  it('版本不符抛中文错误', () => {
    expect(() => planJourneyImport({ ...(fivePhotos() as object), version: 'journey-2025.9' })).toThrow(
      /版本不匹配/,
    );
  });

  it('缺 version 也抛（不把畸形文件当空旅程导入）', () => {
    expect(() => planJourneyImport({ days: [] })).toThrow(/版本不匹配/);
  });

  it('缺 days 数组抛错', () => {
    expect(() => planJourneyImport({ version: VERSION })).toThrow(/缺少 days/);
  });

  it('传 null / 字符串等非对象一律抛错', () => {
    expect(() => planJourneyImport(null)).toThrow(/不是一个对象/);
    expect(() => planJourneyImport('journey.json')).toThrow(/不是一个对象/);
    expect(() => planJourneyImport([])).toThrow(/不是一个对象/);
  });
});

describe('planJourneyImport —— 展开', () => {
  it('两天三地点五张照片：按 days[].places[].photos[] 顺序展开', () => {
    const plan = planJourneyImport(fivePhotos());
    expect(plan.title).toBe('洛阳两日');
    expect(plan.photos).toHaveLength(5);
    expect(plan.droppedPhotos).toBe(0);
    expect(plan.photos.map((photo) => photo.filename)).toEqual([
      'studio-1-a.jpg',
      'studio-2-b.jpg',
      'studio-3-c.jpg',
      'studio-4-d.jpg',
      'studio-5-e.jpg',
    ]);
  });

  it('带坐标的保留坐标，不带的没有该字段（不塞 0）', () => {
    const plan = planJourneyImport(fivePhotos());
    expect(plan.photos[2].lat).toBe(34.6);
    expect(plan.photos[2].lng).toBe(112.4);
    expect(plan.photos[0]).not.toHaveProperty('lat');
    expect(plan.photos[0]).not.toHaveProperty('lng');
  });

  it('只有 lat 没有 lng 的脏数据当作"无坐标"', () => {
    const plan = planJourneyImport(
      rawJourney([
        {
          date: '2026-04-18',
          places: [
            { name: '龙门石窟', photos: [{ id: 'a', thumbnailDataUrl: thumb(), lat: 34.6 }] },
          ],
        },
      ]),
    );
    expect(plan.photos).toHaveLength(1);
    expect(plan.photos[0]).not.toHaveProperty('lat');
  });

  it('caption 去空白；空白标题回落成「未命名旅程」', () => {
    const plan = planJourneyImport({
      version: VERSION,
      title: '   ',
      days: [
        {
          date: '2026-04-18',
          places: [
            {
              name: '龙门石窟',
              photos: [{ id: 'a', thumbnailDataUrl: thumb(), caption: '  香火  ' }],
            },
          ],
        },
      ],
    });
    expect(plan.title).toBe('未命名旅程');
    expect(plan.photos[0].caption).toBe('香火');
  });
});

describe('planJourneyImport —— 行程起始日', () => {
  it('取 days[0].date 作为 startedOn', () => {
    expect(planJourneyImport(fivePhotos()).startedOn).toBe('2026-04-18');
  });

  it('日期畸形时宁可没有，也不猜', () => {
    const journey = rawJourney([
      { date: '10/04/2025', places: [{ name: '某地', photos: [{ id: 'a', thumbnailDataUrl: thumb() }] }] },
    ]);
    expect(planJourneyImport(journey).startedOn).toBeUndefined();
  });
});

describe('planJourneyImport —— 缩略图合规性', () => {
  it('非 data URL 被丢掉并计数', () => {
    const plan = planJourneyImport({
      version: VERSION,
      title: 'x',
      days: [
        {
          date: '2026-04-18',
          places: [
            {
              name: '某地',
              photos: [
                { id: 'ok', thumbnailDataUrl: thumb() },
                { id: 'http', thumbnailDataUrl: 'https://example.com/a.jpg' },
                { id: 'empty', thumbnailDataUrl: 'data:image/jpeg;base64,' },
                { id: 'text', thumbnailDataUrl: 'data:text/plain;base64,aGk=' },
                { id: 'b64', thumbnailDataUrl: 'data:image/jpeg,aGk=' },
                { id: 'bad', thumbnailDataUrl: 'data:image/jpeg;base64,!!!!' },
              ],
            },
          ],
        },
      ],
    });
    expect(plan.photos).toHaveLength(1);
    expect(plan.photos[0].filename).toContain('ok');
    expect(plan.droppedPhotos).toBe(5);
  });

  it('超过 200KB 的被丢掉', () => {
    const tooBig = MAX_THUMBNAIL_BYTES + 1;
    const plan = planJourneyImport({
      version: VERSION,
      title: 'x',
      days: [
        {
          date: '2026-04-18',
          places: [
            {
              name: '某地',
              photos: [
                { id: 'big', thumbnailDataUrl: thumb(tooBig) },
                { id: 'limit', thumbnailDataUrl: thumb(MAX_THUMBNAIL_BYTES) },
              ],
            },
          ],
        },
      ],
    });
    expect(plan.photos).toHaveLength(1);
    expect(plan.photos[0].filename).toContain('limit');
    expect(plan.droppedPhotos).toBe(1);
  });

  it('photos 不是数组 / 元素不是对象都不抛错，只计数', () => {
    const plan = planJourneyImport({
      version: VERSION,
      title: 'x',
      days: [
        { date: '2026-04-18', places: [{ name: 'A', photos: 'nope' }] },
        { date: '2026-04-19', places: [{ name: 'B', photos: [null, 'garbage'] }] },
        { date: '2026-04-20' },
        null,
      ],
    });
    expect(plan.photos).toHaveLength(0);
    expect(plan.droppedPhotos).toBe(2);
  });
});

describe('文件名清洗', () => {
  it('只保留 [a-zA-Z0-9_-]，扩展名固定 .jpg', () => {
    expect(buildJourneyPhotoFilename(1, 'p_a-1')).toBe('studio-1-p_a-1.jpg');
    expect(buildJourneyPhotoFilename(2, '../../etc/passwd')).toBe('studio-2-etcpasswd.jpg');
    expect(buildJourneyPhotoFilename(3, '照片 1.jpg')).toBe('studio-3-1jpg.jpg');
  });

  it('id 缺失或清洗后为空时用 photo 兜底', () => {
    expect(buildJourneyPhotoFilename(4, '')).toBe('studio-4-photo.jpg');
    expect(buildJourneyPhotoFilename(5, '...')).toBe('studio-5-photo.jpg');
    expect(buildJourneyPhotoFilename(6, undefined)).toBe('studio-6-photo.jpg');
    expect(buildJourneyPhotoFilename(7, 42)).toBe('studio-7-photo.jpg');
  });
});

describe('importJourneyToMain —— 调用顺序与参数', () => {
  it('先逐张上传 → 建行程 → 补坐标 → 再挂照片（顺序影响 trip_item 快照）', async () => {
    const calls: string[] = [];
    const seq = ['photo-1', 'photo-2', 'photo-3', 'photo-4', 'photo-5'];

    const importPhotosFake = async (files: readonly File[]): Promise<ImportPhotosResult> => {
      expect(files).toHaveLength(1); // 一次一张
      calls.push(`import:${files[0].name}:${files[0].type}`);
      return importResult([seq[calls.filter((c) => c.startsWith('import:')).length - 1]]);
    };
    const createTripFake = async (
      title: string,
      options?: { startedOn?: string },
    ): Promise<TripView> => {
      calls.push(`createTrip:${title}:${options?.startedOn ?? '-'}`);
      return tripView('trip-9');
    };
    const assignFake = async (tripId: string, photoIds: readonly string[]): Promise<TripView> => {
      calls.push(`assign:${tripId}:${photoIds.join(',')}`);
      return tripView(tripId, photoIds);
    };
    const patchFake = async (photoId: string, lat: number, lng: number): Promise<PhotoListItem> => {
      calls.push(`patch:${photoId}:${lat},${lng}`);
      return photoListItem(photoId);
    };

    const result = await importJourneyToMain(fivePhotos(), {
      importPhotos: importPhotosFake,
      createTrip: createTripFake,
      assignTripPhotos: assignFake,
      patchPhotoLocation: patchFake,
    });

    expect(result).toEqual({ tripId: 'trip-9', photoCount: 5, skipped: 0 });
    expect(calls).toEqual([
      'import:studio-1-a.jpg:image/jpeg',
      'import:studio-2-b.jpg:image/jpeg',
      'import:studio-3-c.jpg:image/jpeg',
      'import:studio-4-d.jpg:image/jpeg',
      'import:studio-5-e.jpg:image/jpeg',
      // 行程起始日来自 `days[0].date`（阶段 16 P1 修复）
      'createTrip:洛阳两日:2026-04-18',
      // **补标发生在挂照片之前**：`assign_photos_to_trip` 会把 photo.lat/lng
      // 快照进 trip_item，反序会让这 5 张里唯一带坐标的那张在地图上变成"未定位"。
      'patch:photo-3:34.6,112.4',
      'assign:trip-9:photo-1,photo-2,photo-3,photo-4,photo-5',
    ]);
  });

  it('单张上传失败：跳过它，其余照常，skipped 计入', async () => {
    let n = 0;
    const result = await importJourneyToMain(
      rawJourney([
        {
          date: '2026-04-18',
          places: [
            {
              name: 'A',
              photos: [
                { id: 'a', thumbnailDataUrl: thumb() },
                { id: 'b', thumbnailDataUrl: thumb() },
                { id: 'c', thumbnailDataUrl: thumb() },
              ],
            },
          ],
        },
      ]),
      {
        importPhotos: async () => {
          n += 1;
          if (n === 2) throw new Error('HTTP 500');
          return importResult([`photo-${n}`]);
        },
        createTrip: async () => tripView('trip-1'),
        assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
        patchPhotoLocation: async (photoId) => photoListItem(photoId),
      },
    );

    expect(result.tripId).toBe('trip-1');
    expect(result.photoCount).toBe(2);
    expect(result.skipped).toBe(1);
  });

  it('后端把它当重复（photos 为空）：不计入行程，计入 skipped', async () => {
    const result = await importJourneyToMain(
      rawJourney([
        {
          date: '2026-04-18',
          places: [
            {
              name: 'A',
              photos: [
                { id: 'a', thumbnailDataUrl: thumb() },
                { id: 'b', thumbnailDataUrl: thumb() },
              ],
            },
          ],
        },
      ]),
      {
        importPhotos: async (files) =>
          files[0].name.includes('-a') ? importResult(['photo-a']) : importResult([]),
        createTrip: async () => tripView('trip-2'),
        assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
        patchPhotoLocation: async (photoId) => photoListItem(photoId),
      },
    );
    expect(result.photoCount).toBe(1);
    expect(result.skipped).toBe(1);
  });

  it('一张都没成功：抛「没有可导入的照片」，且不建行程', async () => {
    const createTripFake = vi.fn(async () => tripView('trip-x'));
    await expect(
      importJourneyToMain(fivePhotos(), {
        importPhotos: async () => {
          throw new Error('网络断了');
        },
        createTrip: createTripFake,
        assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
        patchPhotoLocation: async (photoId) => photoListItem(photoId),
      }),
    ).rejects.toThrow(/没有可导入的照片/);
    expect(createTripFake).not.toHaveBeenCalled();
  });

  it('计划里一张照片都没有：抛「没有可导入的照片」', async () => {
    await expect(
      importJourneyToMain(
        { version: VERSION, title: '空旅程', days: [{ date: '2026-04-18', places: [] }] },
        {
          importPhotos: async () => importResult([]),
          createTrip: async () => tripView('trip-y'),
          assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
          patchPhotoLocation: async (photoId) => photoListItem(photoId),
        },
      ),
    ).rejects.toThrow(/没有可导入的照片/);
  });

  it('补坐标失败不影响整体成功（best-effort）', async () => {
    let n = 0;
    const result = await importJourneyToMain(fivePhotos(), {
      importPhotos: async () => {
        n += 1;
        return importResult([`photo-${n}`]);
      },
      createTrip: async () => tripView('trip-3'),
      assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
      patchPhotoLocation: async () => {
        throw new Error('PATCH 失败');
      },
    });
    expect(result.photoCount).toBe(5);
    expect(result.skipped).toBe(0);
  });

  it('版本不符时不发生任何写操作', async () => {
    const importPhotosFake = vi.fn(async () => importResult([]));
    await expect(
      importJourneyToMain(
        { version: 'journey-1999.1', days: [] },
        {
          importPhotos: importPhotosFake,
          createTrip: async () => tripView('trip-z'),
          assignTripPhotos: async (tripId, ids) => tripView(tripId, ids),
          patchPhotoLocation: async (photoId) => photoListItem(photoId),
        },
      ),
    ).rejects.toThrow(/版本不匹配/);
    expect(importPhotosFake).not.toHaveBeenCalled();
  });
});
