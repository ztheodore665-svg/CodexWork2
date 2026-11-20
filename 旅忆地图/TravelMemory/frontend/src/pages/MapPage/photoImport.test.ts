/** 导入状态机与缩略图配对的单测（Node 环境，不碰 DOM）。
 *
 * 这里测的是**唯一跑在浏览器里却必须保证正确**的那部分逻辑：
 * 「上传中 → 成功/失败 → 失败后还能再来一次」。UI 里的 file input 没法在 Node 里点，
 * 所以状态流转必须脱离组件，这也是 `photoImport.ts` 存在的理由。
 */

import { describe, expect, it } from 'vitest';

import type { ImportPhotosResult } from '../../services/api/photos';
import {
  IDLE_IMPORT,
  THUMB_MAX_EDGE,
  assignThumbs,
  describeImportFailure,
  describeImportSuccess,
  filterImageFiles,
  reduceImportState,
} from './photoImport';
import { tripPointsFromTrips } from './tripPoints';
import type { TripView } from '../../services/api/trips';

function result(overrides: Partial<ImportPhotosResult> = {}): ImportPhotosResult {
  return {
    imported: 0,
    duplicates: 0,
    failed: 0,
    photos: [],
    tripIds: [],
    ...overrides,
  };
}

function photo(id: string, hash = `hash-${id}`) {
  return {
    id,
    assetId: `asset-${id}`,
    hash,
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

function bytes(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer;
}

describe('reduceImportState', () => {
  it('初始态是 idle，计数全零、没有提示', () => {
    expect(IDLE_IMPORT).toEqual({
      status: 'idle',
      message: '',
      imported: 0,
      duplicates: 0,
      failed: 0,
      photoIds: [],
    });
  });

  it('start 进入 uploading 并带上「上传中…」', () => {
    const next = reduceImportState(IDLE_IMPORT, { type: 'start' });
    expect(next.status).toBe('uploading');
    expect(next.message).toBe('上传中…');
    expect(next.photoIds).toEqual([]);
  });

  it('success 落到 done，计数与 photoIds 都来自后端返回值', () => {
    const next = reduceImportState(IDLE_IMPORT, {
      type: 'success',
      result: result({
        imported: 3,
        duplicates: 1,
        failed: 0,
        photos: [photo('p1'), photo('p2'), photo('p3')],
        tripIds: ['t1'],
      }),
    });

    expect(next.status).toBe('done');
    expect(next.imported).toBe(3);
    expect(next.duplicates).toBe(1);
    expect(next.failed).toBe(0);
    expect(next.photoIds).toEqual(['p1', 'p2', 'p3']);
    expect(next.message).toContain('已导入 3 张照片');
    expect(next.message).toContain('跳过 1 张重复');
  });

  it('error 落到 error 并带中文前缀，计数清零', () => {
    const dirty = reduceImportState(IDLE_IMPORT, {
      type: 'success',
      result: result({ imported: 2, photos: [photo('p1'), photo('p2')] }),
    });
    const next = reduceImportState(dirty, { type: 'error', message: 'HTTP 500' });

    expect(next.status).toBe('error');
    expect(next.message).toBe('导入失败：HTTP 500');
    expect(next.imported).toBe(0);
    expect(next.photoIds).toEqual([]);
  });

  it('从 error 再 start 能回到 uploading（不会卡死）', () => {
    const errored = reduceImportState(IDLE_IMPORT, { type: 'error', message: '网络断了' });
    expect(errored.status).toBe('error');

    const retried = reduceImportState(errored, { type: 'start' });
    expect(retried.status).toBe('uploading');
    expect(retried.message).toBe('上传中…');

    // 重试成功：证明 error 态没有污染后续流转
    const ok = reduceImportState(retried, {
      type: 'success',
      result: result({ imported: 1, photos: [photo('p9')] }),
    });
    expect(ok.status).toBe('done');
    expect(ok.photoIds).toEqual(['p9']);
    expect(ok.message).toBe('已导入 1 张照片');
  });
});

describe('describeImportFailure', () => {
  it('补上「导入失败：」前缀', () => {
    expect(describeImportFailure('网络请求异常')).toBe('导入失败：网络请求异常');
  });

  it('后端已经带前缀时不重复加', () => {
    expect(describeImportFailure('导入失败：HTTP 404 —— 该能力未启用')).toBe(
      '导入失败：HTTP 404 —— 该能力未启用',
    );
  });

  it('客户端自己的「照片导入失败：…」也不会被再套一层', () => {
    expect(describeImportFailure('照片导入失败：HTTP 404 —— 该能力未启用')).toBe(
      '照片导入失败：HTTP 404 —— 该能力未启用',
    );
  });

  it('空消息也有兜底文案', () => {
    expect(describeImportFailure('   ')).toBe('导入失败：未知错误');
  });
});

describe('describeImportSuccess', () => {
  it('全成功时只报导入张数', () => {
    expect(describeImportSuccess(result({ imported: 5 }))).toBe('已导入 5 张照片');
  });

  it('重复与失败都要报出来', () => {
    expect(describeImportSuccess(result({ imported: 3, duplicates: 1, failed: 2 }))).toBe(
      '已导入 3 张照片，跳过 1 张重复，2 张解析失败',
    );
  });
});

describe('assignThumbs', () => {
  it('用内容指纹配对：重复文件不影响后续照片对上号', () => {
    // 上传 3 个文件，第 2 个是重复（后端不返回它）
    const hashes = ['h1', 'h2', 'h3'];
    const thumbs = [bytes('thumb-1'), bytes('thumb-2'), bytes('thumb-3')];
    const photos = [photo('p1', 'h1'), photo('p3', 'h3')];

    const assigned = assignThumbs(photos, hashes, thumbs);
    expect(assigned).toEqual([
      { photoId: 'p1', bytes: thumbs[0] },
      { photoId: 'p3', bytes: thumbs[2] },
    ]);
  });

  it('拿不到指纹且下标可靠时退回下标配对', () => {
    const thumbs = [bytes('a'), bytes('b')];
    const assigned = assignThumbs([photo('p1', ''), photo('p2', '')], [null, null], thumbs);
    expect(assigned.map((entry) => entry.photoId)).toEqual(['p1', 'p2']);
  });

  it('拿不到指纹且长度不一致时宁可不存，也不张冠李戴', () => {
    const thumbs = [bytes('a'), bytes('b'), bytes('c')];
    const assigned = assignThumbs([photo('p1', ''), photo('p9', '')], [null, null, null], thumbs);
    expect(assigned).toEqual([]);
  });

  it('某一张缩略图读取失败时跳过它，不影响其余', () => {
    const assigned = assignThumbs(
      [photo('p1', 'h1'), photo('p2', 'h2')],
      ['h1', 'h2'],
      [null, bytes('thumb-2')],
    );
    expect(assigned.map((entry) => entry.photoId)).toEqual(['p2']);
  });

  it('缩略图长边是 256（与 ThumbnailStore 的 key 约定一致）', () => {
    expect(THUMB_MAX_EDGE).toBe(256);
  });
});

describe('filterImageFiles', () => {
  it('非 image/* 的文件被剔除，type 为空的一律放行', () => {
    const files = [
      { type: 'image/jpeg', name: 'a.jpg' },
      { type: 'application/pdf', name: 'b.pdf' },
      { type: '', name: 'c.unknown' },
    ] as unknown as File[];

    expect(filterImageFiles(files).map((file) => file.name)).toEqual(['a.jpg', 'c.unknown']);
  });
});

describe('行程 → 地图点', () => {
  const trip: TripView = {
    id: 't1',
    title: '洛阳两日',
    startedAt: '2026-10-01T00:00:00Z',
    endedAt: '2026-10-02T00:00:00Z',
    source: 'cluster',
    itemCount: 3,
    items: [
      { id: 'i1', photoId: 'ph1', takenAt: '2026-10-01T02:00:00Z', lat: 34.56, lng: 112.47, semanticTag: 'heritage', semanticLabel: '龙门石窟' },
      { id: 'i2', photoId: 'ph2', takenAt: '2026-10-01T09:00:00Z', lat: null, lng: null, semanticTag: 'food', semanticLabel: '洛邑古城' },
      { id: 'i3', photoId: 'ph3', takenAt: '2026-10-02T02:00:00Z', lat: 34.68, lng: 112.45, semanticTag: null, semanticLabel: null },
    ],
  };

  it('只有带坐标的 item 变成照片点，且 source 是 photo', () => {
    const points = tripPointsFromTrips([trip]);
    expect(points.map((point) => point.id)).toEqual(['i1', 'i3']);
    expect(points.every((point) => point.source === 'photo')).toBe(true);
    expect(points[0].lat).toBe(34.56);
    expect(points[0].lng).toBe(112.47);
    // 本机照片没有"承诺半径"，不能画模糊圈
    expect(points[0].radius).toBeUndefined();
  });

  it('semanticLabel 缺省时退回行程标题当展示名', () => {
    expect(tripPointsFromTrips([trip])[1].name).toBe('洛阳两日');
  });

  it('同一张照片挂在两条行程里只算一次', () => {
    const shared = { ...trip, id: 't2', items: [trip.items[0]] };
    expect(tripPointsFromTrips([trip, shared])).toHaveLength(2);
  });

  it('朋友导回的行程用 friend 来源，不与本机照片混层', () => {
    const points = tripPointsFromTrips([trip], 'friend');
    expect(points.length).toBeGreaterThan(0);
    expect(points.every((point) => point.source === 'friend')).toBe(true);
  });
});
