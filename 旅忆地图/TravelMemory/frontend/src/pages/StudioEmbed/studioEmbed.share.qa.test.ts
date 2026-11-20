// @vitest-environment jsdom
/** `StudioEmbed` 的 **share 模式** 载荷测试（阶段 11 QA 补测）。
 *
 *  为什么需要这一份：share 模式是主应用里唯一"会挑照片"的路径 ——
 *  只传该行程的照片、顺序必须是 `trip.items` 的顺序（小应用按数组顺序连轨迹，
 *  顺序错了回放会走回头路），并且必须打到 5175 那个 origin 上。
 *  这些规则以前只有 `selectStudioPhotos` 的纯函数测试，**没有一条测试能证明
 *  组件真的把 `trip` / `photos` 塞进了 init、真的发给了 5175**。
 *
 *  做法：把 iframe 的 `contentWindow.postMessage` 换成 spy（jsdom 不会真的投递消息），
 *  派发一条合法的 `ready`，然后检查父窗口实际发出的那条 `init`。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import React from 'react';

import type { PhotoListItem } from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import { resetNativeBridge, setNativeBridge } from '../../services/native';
import type { NativeBridge } from '../../types/native';
import { StudioEmbed } from './StudioEmbed';

const SHARE_ORIGIN = 'http://localhost:5175';

/** `thumbs.get()` 交出来的**源** ArrayBuffer，用来验"传出去的必须是副本"。 */
const sourceBytes = new Map<string, ArrayBuffer>();

function fakeBridge(): NativeBridge {
  return {
    platform: 'web',
    photos: {
      platform: 'web',
      pickPhotos: async () => [],
      adoptFiles: (files) => files.map((_f, i) => ({ id: `asset-${i}`, width: 4, height: 3 })),
      readThumb: async () => new ArrayBuffer(0),
    },
    permissions: {
      query: async () => 'granted',
      request: async () => 'granted',
    },
    thumbs: {
      get: async (assetId) => {
        const bytes = new Uint8Array([assetId.length, 1, 2, 3]).buffer;
        sourceBytes.set(assetId, bytes);
        return bytes;
      },
      put: async () => {},
      clear: async () => {},
      sizeHint: 0,
    },
  };
}

function photo(id: string, overrides: Partial<PhotoListItem> = {}): PhotoListItem {
  return {
    id,
    hash: `hash-${id}`,
    takenAt: '2026-10-01T02:00:00Z',
    takenAtLocal: '2026-10-01T10:00:00',
    tzName: 'UTC+08:00',
    lat: 30.2,
    lng: 120.1,
    coordSystem: 'WGS84',
    locationSource: 'exif',
    hasLocation: true,
    locationConfidence: 1,
    caption: `照片 ${id}`,
    createdAt: '2026-10-02T00:00:00Z',
    ...overrides,
  };
}

function tripItem(photoId: string): TripView['items'][number] {
  return {
    id: `item-${photoId}`,
    photoId,
    takenAt: '2026-10-01T02:00:00Z',
    lat: 30.2,
    lng: 120.1,
    semanticTag: null,
    semanticLabel: null,
  };
}

const TRIP: TripView = {
  id: 'trip-1',
  title: '黄山三日',
  startedAt: '2026-10-01',
  endedAt: '2026-10-03',
  source: 'manual',
  itemCount: 2,
  // 行程顺序是 p2 → p1，故意与下面的照片库顺序相反
  items: [tripItem('p2'), tripItem('p1')],
};

/** 照片库顺序被打乱，并且多一张"不属于这趟行程"的 pX。 */
const LIBRARY: PhotoListItem[] = [photo('p1'), photo('pX'), photo('p3'), photo('p2')];

interface InitCall {
  message: {
    type: string;
    payload: {
      identity?: Record<string, string>;
      photos?: Array<{ id: string; bytes: ArrayBuffer; takenAt?: string }>;
      trips?: Array<{ id: string }>;
    };
  };
  targetOrigin: string;
  transfer?: Transferable[];
}

function mountShare(): HTMLIFrameElement {
  return mountShareWith(TRIP, LIBRARY);
}

/** 同 `mountShare`，但允许换一组行程 / 照片（W15-1 的日期用例要换照片元数据）。 */
function mountShareWith(trip: TripView, photos: PhotoListItem[]): HTMLIFrameElement {
  render(
    React.createElement(StudioEmbed, {
      open: true,
      mode: 'share',
      trip,
      photos,
      onClose: () => {},
    }),
  );
  const iframe = document.querySelector('iframe.studio-embed-frame') as HTMLIFrameElement | null;
  if (!iframe) throw new Error('iframe 未渲染');
  return iframe;
}

/** 等桥就绪（iframe 由 Portal 二次渲染才出现），派发 ready，取回它发出的 init。 */
async function handshake(iframe: HTMLIFrameElement): Promise<InitCall> {
  const contentWindow = iframe.contentWindow;
  if (!contentWindow) throw new Error('contentWindow 不可用');

  const postMessage = vi.fn();
  Object.defineProperty(contentWindow, 'postMessage', {
    value: postMessage,
    configurable: true,
    writable: true,
  });

  window.dispatchEvent(
    new MessageEvent('message', {
      data: { type: 'ready' },
      origin: SHARE_ORIGIN,
      source: contentWindow as unknown as MessageEventSource,
    }),
  );

  await waitFor(() => expect(postMessage).toHaveBeenCalled(), { timeout: 4000 });

  const [message, targetOrigin, transfer] = postMessage.mock.calls[0] as [
    InitCall['message'],
    string,
    Transferable[],
  ];
  return { message, targetOrigin, transfer };
}

beforeEach(() => {
  sourceBytes.clear();
  setNativeBridge(fakeBridge());
});

afterEach(() => {
  cleanup();
  resetNativeBridge();
  vi.restoreAllMocks();
});

describe('StudioEmbed —— share 模式的 init 载荷', () => {
  it('iframe 指向 5175，且带上 embedded=1 与 parentOrigin', async () => {
    const iframe = mountShare();
    const src = iframe.src;

    expect(new URL(src).origin).toBe(SHARE_ORIGIN);
    // 阶段 13 / W13-5：iframe 必须指向 `embed.html`（浅色地图版），不是 index.html。
    expect(new URL(src).pathname).toBe('/embed.html');
    expect(src).toContain('embedded=1');
    expect(src).toContain(`parentOrigin=${encodeURIComponent(window.location.origin)}`);
  });

  it('ready 之后发出 init：targetOrigin 精确是 5175，trips[0] 就是这一趟行程', async () => {
    const iframe = mountShare();
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);

    expect(call.message.type).toBe('init');
    // 绝不 '*'
    expect(call.targetOrigin).toBe(SHARE_ORIGIN);
    expect(call.message.payload.trips).toHaveLength(1);
    expect(call.message.payload.trips?.[0].id).toBe(TRIP.id);
  });

  it('photos 只含该行程的照片，且顺序 = trip.items 顺序（不是照片库顺序）', async () => {
    const iframe = mountShare();
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const ids = (call.message.payload.photos ?? []).map((p) => p.id);

    // 照片库是 p1,pX,p3,p2；行程是 p2 → p1；pX/p3 不在行程里
    expect(ids).toEqual(['p2', 'p1']);
  });

  it('identity 四个键名齐全（privHex / pubkey / ed25519Priv / ed25519Pub）', async () => {
    const iframe = mountShare();
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const identity = call.message.payload.identity;

    expect(identity).toBeTruthy();
    for (const key of ['privHex', 'pubkey', 'ed25519Priv', 'ed25519Pub']) {
      expect(typeof identity?.[key]).toBe('string');
      expect(identity?.[key]).toMatch(/[0-9a-f]{32,}/i);
    }
    // 两对同值（阶段 10 的小应用认前一对，任务书写的是后一对）
    expect(identity?.ed25519Priv).toBe(identity?.privHex);
    expect(identity?.ed25519Pub).toBe(identity?.pubkey);
  });

  it('传出去的 bytes 是副本：与 thumbs.get 交出的 ArrayBuffer 不是同一个对象', async () => {
    const iframe = mountShare();
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const photos = call.message.payload.photos ?? [];

    expect(photos.length).toBeGreaterThan(0);
    for (const item of photos) {
      const source = sourceBytes.get(item.id);
      expect(source).toBeTruthy();
      // transfer 会 detach 源 buffer —— 所以绝不能把缓存里那个实例交出去
      expect(item.bytes).not.toBe(source);
      expect(item.bytes.byteLength).toBe(source!.byteLength);
    }
    // 每个 buffer 在 transfer 列表里只出现一次
    const transfer = call.transfer ?? [];
    expect(new Set(transfer).size).toBe(transfer.length);
  });

  it('share 模式 5 秒收不到 ready → 超时文案必须给出 trailshare 的启动命令', async () => {
    mountShare();

    // 不派发 ready：只能靠组件自己的握手计时器
    await waitFor(() => expect(document.querySelector('.studio-embed-error')).not.toBeNull(), {
      timeout: 8000,
    });
    const text = document.querySelector('.studio-embed-error')!.textContent ?? '';

    expect(text).toContain('握手超时');
    // 「小应用未启动」这类失败必须让用户能照着修 —— 命令要指向 5175 那个应用
    expect(text).toContain('npm run dev -w @travelmemory/trailshare-standalone');
    // 别串到 track-studio 的命令上（模式判定错了就会串）
    expect(text).not.toContain('@travelmemory/track-studio');
  }, 12000);
});

/** W15-1：分享包里的照片日期。
 *
 *  线上症状：EXIF 解析失败的照片 `takenAt` 是 null，但 `takenAtLocal`（本地时间字符串）
 *  是有的，于是 `init` 里那张照片**完全没有日期** —— 小应用按日期排地点会把它排到最后。
 */
describe('StudioEmbed —— W15-1 分享包照片日期（takenAt / takenAtLocal 兜底）', () => {
  const TRIP_2: TripView = { ...TRIP, items: [tripItem('p2'), tripItem('p1')] };

  it('没有 takenAt 但有 takenAtLocal → init 里的照片带这个日期', async () => {
    const onlyLocal = photo('p1', {
      takenAt: null,
      takenAtLocal: '2026-10-01T10:00:00',
    });
    const iframe = mountShareWith(TRIP_2, [onlyLocal, photo('p2')]);
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const byId = new Map((call.message.payload.photos ?? []).map((p) => [p.id, p]));

    expect(byId.get('p1')?.takenAt).toBe('2026-10-01T10:00:00');
  });

  it('takenAt 与 takenAtLocal 都在 → takenAt 优先（不改变原有语义）', async () => {
    const both = photo('p1', {
      takenAt: '2026-10-01T02:00:00Z',
      takenAtLocal: '2026-10-01T10:00:00',
    });
    const iframe = mountShareWith(TRIP_2, [both, photo('p2')]);
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const byId = new Map((call.message.payload.photos ?? []).map((p) => [p.id, p]));

    expect(byId.get('p1')?.takenAt).toBe('2026-10-01T02:00:00Z');
    // 另一张两张都有：仍然是 takenAt
    expect(byId.get('p2')?.takenAt).toBe('2026-10-01T02:00:00Z');
  });

  it('两个都没有 → 不带 takenAt 字段（不伪造日期）', async () => {
    const none = photo('p1', { takenAt: null, takenAtLocal: null });
    const iframe = mountShareWith(TRIP_2, [none, photo('p2')]);
    await waitFor(() => expect(iframe.contentWindow).not.toBeNull());

    const call = await handshake(iframe);
    const byId = new Map((call.message.payload.photos ?? []).map((p) => [p.id, p]));

    expect(byId.get('p1')).toBeTruthy();
    expect('takenAt' in (byId.get('p1') ?? {})).toBe(false);
  });
});
