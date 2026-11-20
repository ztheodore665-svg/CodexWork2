// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FALLBACK_THUMBNAIL_DATA_URL } from '@travelmemory/photo-source';

import {
  PARENT_ORIGIN,
  allowedOrigins,
  initBridge,
  isEmbedded,
  parseBridgePhotos,
  sendToParent,
  targetOrigin,
} from './bridge';

function setEmbedded(value: boolean): void {
  window.history.replaceState(null, '', value ? '/?embedded=1' : '/');
}

function dispatch(data: unknown, origin: string): void {
  window.dispatchEvent(new MessageEvent('message', { data, origin }));
}

beforeEach(() => {
  setEmbedded(false);
  vi.restoreAllMocks();
});

afterEach(() => {
  setEmbedded(false);
  vi.restoreAllMocks();
});

describe('isEmbedded / 白名单', () => {
  it('只有 ?embedded=1 算嵌入', () => {
    expect(isEmbedded()).toBe(false);
    setEmbedded(true);
    expect(isEmbedded()).toBe(true);
  });

  it('白名单含主应用与自身 origin', () => {
    expect(allowedOrigins()).toContain(PARENT_ORIGIN);
    expect(allowedOrigins()).toContain(window.location.origin);
  });

  it('localhost 下 targetOrigin 用自身 origin', () => {
    expect(targetOrigin()).toBe(window.location.origin);
  });
});

describe('initBridge —— 未嵌入', () => {
  it('不发 ready，清理函数可调用，sendToParent 静默丢弃', () => {
    const postMessage = vi.spyOn(window, 'postMessage');
    const cleanup = initBridge({ onInit: () => {}, onPhotosUpdate: () => {} });
    sendToParent({ type: 'import-to-main', journey: {} });
    expect(postMessage).not.toHaveBeenCalled();
    expect(() => cleanup()).not.toThrow();
  });
});

describe('initBridge —— 嵌入模式', () => {
  it('发 ready，接收同源 init，忽略未知 origin', () => {
    setEmbedded(true);
    const postMessage = vi.spyOn(window, 'postMessage');
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });

    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][0]).toEqual({ type: 'ready' });

    dispatch({ type: 'init', payload: {} }, 'https://evil.example');
    expect(onInit).not.toHaveBeenCalled();

    dispatch({ type: 'init', payload: { photos: [] } }, window.location.origin);
    expect(onInit).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('photos-update 回调已洗好的 SystemPhoto', () => {
    setEmbedded(true);
    const onPhotosUpdate = vi.fn();
    const cleanup = initBridge({ onInit: () => {}, onPhotosUpdate });

    dispatch(
      {
        type: 'photos-update',
        photos: [{ id: 'p_1', name: 'a.jpg', bytes: new ArrayBuffer(8) }],
      },
      window.location.origin,
    );

    expect(onPhotosUpdate).toHaveBeenCalledTimes(1);
    const photos = onPhotosUpdate.mock.calls[0][0] as Array<{ id: string; thumbnailDataUrl: string }>;
    expect(photos).toHaveLength(1);
    expect(photos[0].thumbnailDataUrl).toBe(FALLBACK_THUMBNAIL_DATA_URL);
    cleanup();
  });

  it('init 里的 identity 原样交给 onInit（桥不吞字段、不做形状假设）', () => {
    setEmbedded(true);
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });
    const identity = { privHex: '11'.repeat(32), pubkey: 'ed25519:deadbeef' };

    dispatch(
      {
        type: 'init',
        payload: { identity, photos: [{ id: 'p_1', name: 'a.jpg', bytes: new ArrayBuffer(8) }] },
      },
      window.location.origin,
    );

    expect(onInit).toHaveBeenCalledTimes(1);
    const payload = onInit.mock.calls[0][0] as { identity?: unknown; photos?: unknown };
    expect(payload.identity).toEqual(identity);
    expect(Array.isArray(payload.photos)).toBe(true);
    expect((payload.photos as unknown[]).length).toBe(1);

    cleanup();
  });

  it('清理后不再回调', () => {
    setEmbedded(true);
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });
    cleanup();
    dispatch({ type: 'init', payload: {} }, window.location.origin);
    expect(onInit).not.toHaveBeenCalled();
  });
});

describe('parseBridgePhotos —— 容错', () => {
  it('丢弃没有 ArrayBuffer 字节的元素，补齐 id/name/缩略图', () => {
    const photos = parseBridgePhotos([
      { bytes: new ArrayBuffer(4) },
      { bytes: 'nope' },
      null,
      'garbage',
    ]);
    expect(photos).toHaveLength(1);
    expect(photos[0].id).toBe('p_bridge0');
    expect(photos[0].name).toBe('照片1');
    expect(photos[0].thumbnailDataUrl).toBe(FALLBACK_THUMBNAIL_DATA_URL);
  });

  it('接受 ArrayBuffer 视图', () => {
    const photos = parseBridgePhotos([{ bytes: new Uint8Array([1, 2, 3]), id: 'p_9', name: 'x.jpg' }]);
    expect(photos[0].bytes.byteLength).toBe(3);
  });

  it('非数组返回空', () => {
    expect(parseBridgePhotos(undefined)).toEqual([]);
    expect(parseBridgePhotos({ length: 1 })).toEqual([]);
  });
});

describe('?parentOrigin 覆盖（阶段 11：主应用 iframe 嵌入）', () => {
  /** `PARENT_OVERRIDE` 在**模块加载时**算好，所以改 URL 之后必须重新 import 一次。 */
  async function loadBridgeWith(search: string): Promise<typeof import('./bridge')> {
    window.history.replaceState(null, '', `/${search}`);
    vi.resetModules();
    return import('./bridge');
  }

  it('合法的 parentOrigin 被加入白名单，并成为 targetOrigin', async () => {
    const bridge = await loadBridgeWith('?embedded=1&parentOrigin=http%3A%2F%2Flocalhost%3A5173');

    expect(bridge.allowedOrigins()).toContain('http://localhost:5173');
    expect(bridge.allowedOrigins()).toContain(PARENT_ORIGIN);
    expect(bridge.allowedOrigins()).toContain(window.location.origin);
    expect(bridge.targetOrigin()).toBe('http://localhost:5173');
  });

  it('只保留 origin：path / query 被丢弃，不参与白名单比对', async () => {
    const bridge = await loadBridgeWith(
      '?embedded=1&parentOrigin=http%3A%2F%2Flocalhost%3A5173%2Fsome%2Fpath%3Fa%3D1',
    );
    expect(bridge.targetOrigin()).toBe('http://localhost:5173');
  });

  it('非法值被忽略，白名单与 targetOrigin 回落到原逻辑', async () => {
    for (const bad of ['javascript:alert(1)', 'not-a-url', 'file:///etc/passwd', 'data:text/html,x']) {
      const bridge = await loadBridgeWith(`?embedded=1&parentOrigin=${encodeURIComponent(bad)}`);
      const origins = bridge.allowedOrigins();

      expect(origins).not.toContain(bad);
      expect(origins).toEqual([PARENT_ORIGIN, window.location.origin]);
      expect(bridge.targetOrigin()).toBe(window.location.origin);
    }
  });
});
