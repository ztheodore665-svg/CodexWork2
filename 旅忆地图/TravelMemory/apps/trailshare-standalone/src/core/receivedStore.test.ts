/** W15-4：收件人决定的本地存储 —— 只测**纯函数** `buildReceivedPlace` / `thumbnailsOf`。
 *
 * IndexedDB 的读写（`saveReceived` / `listReceived` / `markRejected`）不在 Node 里测：
 * 引 `fake-indexeddb` 会打破本应用"零运行时依赖、零测试依赖"的约定，
 * 而真实行为（配额满、被其它标签页占用）在内存实现里也复现不出来 —— 交给 QA 浏览器验。
 */

import { describe, expect, it } from 'vitest';

import type { UnpackedPlace, UnpackView } from '../types/share';
import {
  buildReceivedPlace,
  buildRejectedPlace,
  imageToThumbnailDataUrl,
  thumbnailsOf,
  UNNAMED_SENDER,
} from './receivedStore';

const NOW = '2026-11-13T00:00:00.000Z';

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function unpackedPlace(overrides: Partial<UnpackedPlace> = {}): UnpackedPlace {
  return {
    placeId: 'p1_aaa',
    name: '西湖',
    date: '2026-11-01',
    tag: 'nature',
    intensity: 'mid',
    photos: [{ photoId: 'ph_1', image: bytes([0xff, 0xd8, 0xff]) }],
    ...overrides,
  };
}

function unpackView(overrides: Partial<UnpackView> = {}): UnpackView {
  return {
    ok: true,
    groups: [],
    from: { displayName: '小旅', pubkey: 'aa'.repeat(32) },
    shareId: 's_demo',
    commitment: 'c'.repeat(64),
    signature: 'sig',
    chainSeq: 1,
    chainPrevHash: '0'.repeat(64),
    chainOk: true,
    chainNote: '',
    report: { structureOk: true, signatureOk: true, commitmentOk: true, chainOk: true, chainNote: '' },
    canAccept: true,
    ...overrides,
  };
}

describe('imageToThumbnailDataUrl / thumbnailsOf', () => {
  it('字节 → data URL（内存里转，不落盘）', () => {
    expect(imageToThumbnailDataUrl(bytes([0xff, 0xd8]))).toBe('data:image/jpeg;base64,/9g=');
  });

  it('空字节返回空串（不给 <img> 一个坏 URL）', () => {
    expect(imageToThumbnailDataUrl(new ArrayBuffer(0))).toBe('');
  });

  it('thumbnailsOf 与照片同序同数', () => {
    const urls = thumbnailsOf(
      unpackedPlace({
        photos: [
          { photoId: 'a', image: bytes([1]) },
          { photoId: 'b', image: bytes([2]) },
        ],
      }),
    );
    expect(urls).toHaveLength(2);
    expect(urls.every((url) => url.startsWith('data:image/jpeg;base64,'))).toBe(true);
  });
});

describe('buildReceivedPlace', () => {
  it('id 是 `shareId:placeId`，发送方与地点字段都带过来', () => {
    const record = buildReceivedPlace(unpackView(), unpackedPlace(), ['data:image/jpeg;base64,/9g='], NOW);

    expect(record.id).toBe('s_demo:p1_aaa');
    expect(record.shareId).toBe('s_demo');
    expect(record.fromPubkey).toBe('aa'.repeat(32));
    expect(record.fromName).toBe('小旅');
    expect(record.placeName).toBe('西湖');
    expect(record.date).toBe('2026-11-01');
    expect(record.tag).toBe('nature');
    expect(record.intensity).toBe('mid');
    expect(record.rejected).toBe(false);
    expect(record.acceptedAt).toBe(NOW);
  });

  it('缩略图是 data URL 前缀，配文缺失时不写 caption 键', () => {
    const record = buildReceivedPlace(unpackView(), unpackedPlace(), ['data:image/jpeg;base64,/9g='], NOW);

    expect(record.photos).toHaveLength(1);
    expect(record.photos[0].thumbnailDataUrl.startsWith('data:image/jpeg;base64,')).toBe(true);
    // 不许出现 `caption: undefined` 这种键（落库后 JSON 里会变成 null）
    expect('caption' in record.photos[0]).toBe(false);
  });

  it('有配文时保留（去空白后非空才写）', () => {
    const record = buildReceivedPlace(
      unpackView(),
      unpackedPlace({ photos: [{ photoId: 'ph_1', image: bytes([1]), caption: '  日落  ' }] }),
      ['data:image/jpeg;base64,/9g='],
      NOW,
    );
    expect(record.photos[0].caption).toBe('日落');

    const blank = buildReceivedPlace(
      unpackView(),
      unpackedPlace({ photos: [{ photoId: 'ph_1', image: bytes([1]), caption: '   ' }] }),
      ['data:image/jpeg;base64,/9g='],
      NOW,
    );
    expect('caption' in blank.photos[0]).toBe(false);
  });

  it('没有缩略图的照片不落盘（空 URL 只会在 UI 里渲染成破图）', () => {
    const record = buildReceivedPlace(
      unpackView(),
      unpackedPlace({
        photos: [
          { photoId: 'a', image: bytes([1]) },
          { photoId: 'b', image: bytes([2]) },
        ],
      }),
      ['data:image/jpeg;base64,/9g=', ''],
      NOW,
    );
    expect(record.photos).toHaveLength(1);
  });

  it('label / 坐标只在有值时写；发送方没名字用兜底文案', () => {
    const bare = buildReceivedPlace(
      unpackView({ from: { displayName: '', pubkey: 'bb'.repeat(32) } }),
      unpackedPlace(),
      [],
      NOW,
    );
    expect(bare.fromName).toBe(UNNAMED_SENDER);
    expect('label' in bare).toBe(false);
    expect('lat' in bare).toBe(false);

    const rich = buildReceivedPlace(
      unpackView(),
      unpackedPlace({ label: '上午', lat: 30.24, lng: 120.15 }),
      [],
      NOW,
    );
    expect(rich.label).toBe('上午');
    expect(rich.lat).toBeCloseTo(30.24, 6);
    expect(rich.lng).toBeCloseTo(120.15, 6);
  });
});

describe('buildRejectedPlace（W15-4 P3：拒绝也要带发件人与地点）', () => {
  it('拒绝记录的 fromName / placeName / 缩略图都不为空，rejected=true', () => {
    const record = buildRejectedPlace(
      unpackView(),
      unpackedPlace({ label: '上午', lat: 30.24, lng: 120.15 }),
      ['data:image/jpeg;base64,/9g='],
      NOW,
    );

    expect(record.rejected).toBe(true);
    expect(record.id).toBe('s_demo:p1_aaa');
    // 这三条是关键：空名字会让"按发件人分组"拿到一个无主记录
    expect(record.fromName).toBe('小旅');
    expect(record.fromPubkey).toBe('aa'.repeat(32));
    expect(record.placeName).toBe('西湖');
    expect(record.photos).toHaveLength(1);
    expect(record.date).toBe('2026-11-01');
    expect(record.acceptedAt).toBe(NOW);
  });

  it('与接受记录字段完全一致，只有 rejected 不同（便于"反悔"时直接翻转）', () => {
    const accepted = buildReceivedPlace(unpackView(), unpackedPlace(), [], NOW);
    const rejected = buildRejectedPlace(unpackView(), unpackedPlace(), [], NOW);

    expect({ ...rejected, rejected: false }).toEqual(accepted);
  });
});
