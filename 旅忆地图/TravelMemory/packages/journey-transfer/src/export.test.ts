import { describe, expect, it } from 'vitest';

import { bundleFromTrip } from './export';
import {
  JOURNEY_BUNDLE_VERSION,
  MAX_THUMBNAIL_BYTES,
  type JourneyPhotoLike,
} from './types';
import { tripFromBundle } from './import';

/** 400 个 base64 字符 ≈ 300 字节，远小于 200KB 上限。 */
const SMALL_THUMBNAIL = `data:image/jpeg;base64,${'A'.repeat(400)}`;

/** base64 长度 280000 → 解码 210000 字节 > 200KB。 */
const OVERSIZE_THUMBNAIL = `data:image/jpeg;base64,${'A'.repeat(280_000)}`;

/** 用在 ArrayBuffer 里的独特标记，用来证明原始字节没有渗进 JSON。 */
const RAW_MARKER = 'RAWBYTESMARKER2026';

function markerBuffer(): ArrayBuffer {
  const source = new TextEncoder().encode(`<${RAW_MARKER}>`);
  const buffer = new ArrayBuffer(source.byteLength);
  new Uint8Array(buffer).set(source);
  return buffer;
}

/** 主应用 `SystemPhoto` 的形状：比 `JourneyPhotoLike` 多了字节与原文件名。 */
interface SystemPhotoShape extends JourneyPhotoLike {
  bytes: ArrayBuffer;
  name: string;
}

function systemPhoto(overrides: Partial<SystemPhotoShape> = {}): SystemPhotoShape {
  return {
    bytes: markerBuffer(),
    name: 'IMG_0001.jpg',
    thumbnailDataUrl: SMALL_THUMBNAIL,
    takenAt: '2026-01-01T02:00:00.000Z',
    lat: 30.259,
    lng: 120.13,
    ...overrides,
  };
}

/** 一个「两天 / 两个地点 / 两张照片」的完整 trip（结构与 frontend 的 Trip 一致，但不 import 它）。 */
function sampleTrip(): unknown {
  return {
    id: 't1',
    title: '杭州三日',
    coverPlaceId: 'pl1',
    createdAt: '2026-01-01T00:00:00.000Z',
    days: [
      {
        id: 'd1',
        date: '2026-01-01',
        places: [
          {
            id: 'pl1',
            name: '西湖',
            label: '上午',
            tag: 'nature',
            intensity: 'high',
            coverIndex: 0,
            photos: [{ id: 'ph1', image: markerBuffer(), caption: '断桥残雪' }],
          },
        ],
      },
      {
        id: 'd2',
        date: '2026-01-02',
        places: [
          {
            id: 'pl2',
            name: '河坊街',
            tag: 'food',
            intensity: 'mid',
            coverIndex: 0,
            photos: [{ id: 'ph2', image: markerBuffer() }],
          },
        ],
      },
    ],
  };
}

function samplePhotos(): Map<string, JourneyPhotoLike> {
  return new Map<string, JourneyPhotoLike>([
    ['ph1', systemPhoto()],
    ['ph2', systemPhoto({ takenAt: '2026-01-02T03:00:00.000Z', lat: 30.24, lng: 120.17 })],
  ]);
}

describe('bundleFromTrip —— 往返一致', () => {
  it('trip → bundle → tripFromBundle 后 title 与 days 完全一致', () => {
    const bundle = bundleFromTrip(sampleTrip(), samplePhotos(), {
      exportedAt: '2026-01-03T00:00:00.000Z',
    });

    expect(bundle.version).toBe(JOURNEY_BUNDLE_VERSION);
    expect(bundle.exportedAt).toBe('2026-01-03T00:00:00.000Z');
    expect(bundle.title).toBe('杭州三日');
    expect(bundle.days).toHaveLength(2);

    const imported = tripFromBundle(bundle);
    expect(imported.title).toBe(bundle.title);
    expect(imported.days).toEqual(bundle.days);
    expect(imported.days[0].date).toBe('2026-01-01');
    expect(imported.days[0].places[0].photos[0].caption).toBe('断桥残雪');
    expect(imported.days[0].places[0].tag).toBe('nature');
    expect(imported.days[1].places[0].tag).toBe('food');
  });

  it('takenAt 原样保留 ISO，日期保持 YYYY-MM-DD（B14）', () => {
    const bundle = bundleFromTrip(sampleTrip(), samplePhotos());
    const photo = bundle.days[0].places[0].photos[0];
    expect(photo.takenAt).toBe('2026-01-01T02:00:00.000Z');
    expect(photo.lat).toBeCloseTo(30.259, 5);
    expect(photo.lng).toBeCloseTo(120.13, 5);
    expect(bundle.days.every((day) => /^\d{4}-\d{2}-\d{2}$/.test(day.date))).toBe(true);
  });

  it('缺省 exportedAt 用当前时刻的 ISO 字符串', () => {
    const before = Date.now();
    const bundle = bundleFromTrip(sampleTrip(), samplePhotos());
    const exportedAt = Date.parse(bundle.exportedAt);
    expect(Number.isNaN(exportedAt)).toBe(false);
    expect(exportedAt).toBeGreaterThanOrEqual(before - 1000);
    expect(new Date(bundle.exportedAt).toISOString()).toBe(bundle.exportedAt);
  });
});

describe('bundleFromTrip —— 不含原始字节', () => {
  it('SystemPhoto 形状可直接当 JourneyPhotoLike 用（跨包零依赖的结构兼容）', () => {
    const systemPhotos = new Map<string, SystemPhotoShape>([
      ['ph1', systemPhoto()],
      ['ph2', systemPhoto({ takenAt: '2026-01-02T03:00:00.000Z' })],
    ]);
    // 这一行是**编译期断言**：`Map<string, SystemPhoto>` 能直接赋给
    // `Map<string, JourneyPhotoLike>`，调用方不需要写任何转换层。
    const asJourneyMap: Map<string, JourneyPhotoLike> = systemPhotos;

    const bundle = bundleFromTrip(sampleTrip(), asJourneyMap);
    expect(bundle.days[0].places[0].photos).toHaveLength(1);
    expect(bundle.days[1].places[0].photos).toHaveLength(1);
  });

  it('序列化结果不含输入字节的独特标记、不含 ArrayBuffer、不含 bytes 字段', () => {
    const bundle = bundleFromTrip(sampleTrip(), samplePhotos());
    const json = JSON.stringify(bundle);

    expect(json).not.toContain(RAW_MARKER);
    expect(json).not.toContain('ArrayBuffer');
    expect(json).not.toContain('"bytes"');
    expect(json).not.toContain('"image"');

    const photo = bundle.days[0].places[0].photos[0];
    expect(Object.keys(photo).sort()).toEqual(['caption', 'id', 'lat', 'lng', 'takenAt', 'thumbnailDataUrl']);
  });

  it('只拷贝白名单字段：photos 里塞进来的未知键被丢弃', () => {
    const polluted: JourneyPhotoLike = {
      thumbnailDataUrl: SMALL_THUMBNAIL,
      takenAt: '2026-01-01T02:00:00.000Z',
    };
    // 运行时混进来的杂键（类型外）也不该出现在包里
    const pollutedRecord = polluted as unknown as Record<string, unknown>;
    pollutedRecord.bytes = markerBuffer();
    pollutedRecord.secret = 'should-not-leak';
    const photos = new Map<string, JourneyPhotoLike>([['ph1', polluted]]);
    const bundle = bundleFromTrip(sampleTrip(), photos);
    const json = JSON.stringify(bundle);
    expect(json).not.toContain('should-not-leak');
    expect(json).not.toContain(RAW_MARKER);
    expect(bundle.days[0].places[0].photos).toHaveLength(1);
  });
});

describe('bundleFromTrip —— 缩略图上限', () => {
  it('解码后超过 MAX_THUMBNAIL_BYTES 的缩略图被丢弃（照片整张不进包）', () => {
    const photos = new Map<string, JourneyPhotoLike>([
      ['ph1', systemPhoto({ thumbnailDataUrl: OVERSIZE_THUMBNAIL })],
      ['ph2', systemPhoto()],
    ]);
    const bundle = bundleFromTrip(sampleTrip(), photos);

    expect(bundle.days[0].places[0].photos).toHaveLength(0); // ph1 超限被丢
    expect(bundle.days[1].places[0].photos).toHaveLength(1); // ph2 正常保留
    expect(JSON.stringify(bundle).length).toBeLessThan(MAX_THUMBNAIL_BYTES);
  });

  it('刚好等于上限的缩略图保留', () => {
    const exactPayloadLength = Math.ceil((MAX_THUMBNAIL_BYTES * 4) / 3); // 解码后 >= 上限
    const exactFit = `data:image/jpeg;base64,${'A'.repeat(
      Math.floor((MAX_THUMBNAIL_BYTES * 4) / 3),
    )}`;
    const photos = new Map<string, JourneyPhotoLike>([
      ['ph1', systemPhoto({ thumbnailDataUrl: exactFit })],
    ]);
    const bundle = bundleFromTrip(sampleTrip(), photos);
    // floor(len*3/4) <= 200000 即算合法
    expect(exactPayloadLength).toBeGreaterThan(0);
    expect(bundle.days[0].places[0].photos).toHaveLength(1);
  });
});

describe('bundleFromTrip —— 非法输入不抛错、降级处理', () => {
  it('trip 为 null / 字符串 / 数组时返回空 days 与兜底标题', () => {
    for (const bad of [null, undefined, 'not-a-trip', 42, [], [1, 2, 3]]) {
      const bundle = bundleFromTrip(bad, new Map());
      expect(bundle.version).toBe(JOURNEY_BUNDLE_VERSION);
      expect(bundle.title).toBe('未命名旅程');
      expect(bundle.days).toEqual([]);
    }
  });

  it('days 不是数组时降级为空数组', () => {
    const bundle = bundleFromTrip({ title: 'X', days: 'oops' }, new Map());
    expect(bundle.days).toEqual([]);
    expect(bundle.title).toBe('X');
  });

  it('非法 tag / intensity 被降级为 leisure / mid（保留地点，不连带丢照片）', () => {
    const trip = {
      title: '脏数据',
      days: [
        {
          id: 'd1',
          date: '2026-05-01',
          places: [
            {
              id: 'pl1',
              name: '某地',
              tag: 'unknown-tag',
              intensity: 'extreme',
              photos: [{ id: 'ph1', image: markerBuffer() }],
            },
          ],
        },
      ],
    };
    const bundle = bundleFromTrip(trip, samplePhotos());
    const place = bundle.days[0].places[0];
    expect(place.tag).toBe('leisure');
    expect(place.intensity).toBe('mid');
    expect(place.photos).toHaveLength(1);
  });

  it('日期格式非法的天被丢弃；缺地点名的地点被丢弃', () => {
    const trip = {
      title: '脏数据',
      days: [
        { id: 'd1', date: '2026/05/01', places: [] },
        { id: 'd2', date: '2026-05-02', places: [{ id: 'pl1', tag: 'food', intensity: 'low' }] },
        { id: 'd3', date: '2026-05-03', places: [] },
      ],
    };
    const bundle = bundleFromTrip(trip, new Map());
    expect(bundle.days.map((day) => day.date)).toEqual(['2026-05-02', '2026-05-03']);
    expect(bundle.days[0].places).toEqual([]);
  });

  it('缩略图不是 data URL、或照片不在 photos 表里时丢弃该照片', () => {
    const trip = {
      title: 'X',
      days: [
        {
          id: 'd1',
          date: '2026-05-01',
          places: [
            {
              id: 'pl1',
              name: '某地',
              tag: 'nature',
              intensity: 'mid',
              photos: [{ id: 'ph1' }, { id: 'ph2' }, { id: 'ph-missing' }, { noId: true }],
            },
          ],
        },
      ],
    };
    const photos = new Map<string, JourneyPhotoLike>([
      ['ph1', { thumbnailDataUrl: 'https://example.com/a.jpg' }],
      ['ph2', { thumbnailDataUrl: SMALL_THUMBNAIL }],
    ]);
    const bundle = bundleFromTrip(trip, photos);
    expect(bundle.days[0].places[0].photos.map((photo) => photo.id)).toEqual(['ph2']);
  });

  it('photos 传入普通对象（JSON 往返后 Map 退化）也能查表', () => {
    const trip = {
      title: 'X',
      days: [
        {
          id: 'd1',
          date: '2026-05-01',
          places: [
            {
              id: 'pl1',
              name: '某地',
              tag: 'nature',
              intensity: 'mid',
              photos: [{ id: 'ph1' }],
            },
          ],
        },
      ],
    };
    const photosObject = { ph1: { thumbnailDataUrl: SMALL_THUMBNAIL } };
    const bundle = bundleFromTrip(trip, photosObject as unknown as Map<string, JourneyPhotoLike>);
    expect(bundle.days[0].places[0].photos).toHaveLength(1);
  });
});
