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

const VALID_THUMB = `data:image/jpeg;base64,${'A'.repeat(64)}`;

function setEmbedded(value: boolean): void {
  window.history.replaceState(null, '', value ? '/?embedded=1' : '/');
}

function photosPayload(): unknown {
  return [
    {
      id: 'p_1',
      bytes: new ArrayBuffer(16),
      name: 'a.jpg',
      thumbnailDataUrl: VALID_THUMB,
      takenAt: '2026-04-18T01:00:00.000Z',
      lat: 30.24,
      lng: 120.15,
    },
  ];
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

describe('isEmbedded / allowedOrigins / targetOrigin', () => {
  it('只有 ?embedded=1 才算嵌入', () => {
    expect(isEmbedded()).toBe(false);
    setEmbedded(true);
    expect(isEmbedded()).toBe(true);
    window.history.replaceState(null, '', '/?embedded=0');
    expect(isEmbedded()).toBe(false);
  });

  it('白名单同时包含主应用与自身 origin（后者给同源 harness）', () => {
    const origins = allowedOrigins();
    expect(origins).toContain(PARENT_ORIGIN);
    expect(origins).toContain(window.location.origin);
  });

  it('localhost 下 targetOrigin 用自身 origin（否则浏览器会丢弃消息）', () => {
    expect(targetOrigin()).toBe(window.location.origin);
  });
});

describe('initBridge —— 未嵌入', () => {
  it('不发 ready，返回的清理函数可调用', () => {
    const postMessage = vi.spyOn(window, 'postMessage');
    const cleanup = initBridge({ onInit: () => {}, onPhotosUpdate: () => {} });

    expect(postMessage).not.toHaveBeenCalled();
    expect(() => cleanup()).not.toThrow();
  });

  it('sendToParent 未嵌入时静默丢弃', () => {
    const postMessage = vi.spyOn(window, 'postMessage');
    sendToParent({ type: 'import-to-main', journey: {} });
    expect(postMessage).not.toHaveBeenCalled();
  });
});

describe('initBridge —— 嵌入模式', () => {
  it('发 ready，并接收同源 init', () => {
    setEmbedded(true);
    const postMessage = vi.spyOn(window, 'postMessage');
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });

    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][0]).toEqual({ type: 'ready' });

    dispatch({ type: 'init', payload: { photos: photosPayload() } }, window.location.origin);
    expect(onInit).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('来自未知 origin 的消息被忽略', () => {
    setEmbedded(true);
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });

    dispatch({ type: 'init', payload: {} }, 'https://evil.example');
    expect(onInit).not.toHaveBeenCalled();

    dispatch({ type: 'init', payload: {} }, window.location.origin);
    expect(onInit).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('photos-update 会回调（照片已洗成 SystemPhoto）', () => {    setEmbedded(true);
    const onPhotosUpdate = vi.fn();
    const cleanup = initBridge({ onInit: () => {}, onPhotosUpdate });

    dispatch({ type: 'photos-update', photos: photosPayload() }, window.location.origin);
    expect(onPhotosUpdate).toHaveBeenCalledTimes(1);
    const photos = onPhotosUpdate.mock.calls[0][0] as Array<{ id: string; name: string }>;
    expect(photos).toHaveLength(1);
    expect(photos[0].id).toBe('p_1');
    cleanup();
  });

  it('init 里的 identity 原样交给 onInit（桥不吞字段、不做形状假设）', () => {
    setEmbedded(true);
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });
    const identity = { privHex: '11'.repeat(32), pubkey: 'ed25519:deadbeef' };

    dispatch(
      { type: 'init', payload: { identity, photos: photosPayload() } },
      window.location.origin,
    );

    expect(onInit).toHaveBeenCalledTimes(1);
    const payload = onInit.mock.calls[0][0] as { identity?: unknown; photos?: unknown };
    expect(payload.identity).toEqual(identity);
    // photos 与 identity 互不影响
    expect(Array.isArray(payload.photos)).toBe(true);
    expect((payload.photos as unknown[]).length).toBe(1);

    cleanup();
  });

  it('清理函数移除监听', () => {
    setEmbedded(true);
    const onInit = vi.fn();
    const cleanup = initBridge({ onInit, onPhotosUpdate: () => {} });
    cleanup();
    dispatch({ type: 'init', payload: {} }, window.location.origin);
    expect(onInit).not.toHaveBeenCalled();
  });
});

describe('parseBridgePhotos —— 脏数据容错', () => {
  it('缺 thumbnailDataUrl 用 1×1 PNG 兜底；缺 id/name 补默认值', () => {
    const photos = parseBridgePhotos([
      { bytes: new ArrayBuffer(4) },
      { bytes: 'not-a-buffer' },
      null,
      'garbage',
    ]);
    expect(photos).toHaveLength(1);
    expect(photos[0].thumbnailDataUrl).toBe(FALLBACK_THUMBNAIL_DATA_URL);
    expect(photos[0].id).toBe('p_bridge0');
    expect(photos[0].name).toBe('照片1');
  });

  it('接受 ArrayBuffer 视图（transfer 后的形态不确定）', () => {
    const view = new Uint8Array([1, 2, 3, 4]);
    const photos = parseBridgePhotos([{ bytes: view, id: 'p_9', name: 'x.jpg' }]);
    expect(photos).toHaveLength(1);
    expect(photos[0].bytes.byteLength).toBe(4);
  });

  it('非数组返回空数组', () => {
    expect(parseBridgePhotos(undefined)).toEqual([]);
    expect(parseBridgePhotos({})).toEqual([]);
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
    // 原来的两个来源都还在（向后兼容）
    expect(bridge.allowedOrigins()).toContain(PARENT_ORIGIN);
    expect(bridge.allowedOrigins()).toContain(window.location.origin);
    // 有参数时它才是回发目标：否则父窗口永远收不到 ready
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
      // 没有合法覆盖 → 仍是 loopback 用自身 origin 的老行为
      expect(bridge.targetOrigin()).toBe(window.location.origin);
    }
  });
});
