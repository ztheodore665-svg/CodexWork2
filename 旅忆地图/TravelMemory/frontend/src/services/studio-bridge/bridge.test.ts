// @vitest-environment jsdom
/** 主应用侧 iframe 桥的单测（Phase 11）。
 *
 *  为什么值得单独一份：这一层是**跨 origin 的信任边界**。两个 iframe 同时存在、
 *  消息来源判断写错一行，就可能让"某个第三方 iframe 的消息"被当成本应用的结果
 *  （比如把别人的 journey 导进用户照片库）。这类错误不会有任何报错，
 *  只能靠测试钉住"source 与 origin 必须同时匹配"。
 *
 *  环境用 jsdom（文件级 docblock 覆盖 `vite.config.ts` 的 node 默认值）：
 *  要真实的 `MessageEvent` / `Blob`。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { collectTransferables, createStudioBridge } from './bridge';
import type { StudioInitPayload } from './types';

/** 小应用的 origin（真实流程里从 `VITE_STUDIO_TRACK_URL` 取）。 */
const TARGET = 'http://localhost:5174';

interface FakeFrame {
  iframe: HTMLIFrameElement;
  /** iframe 里的 window（我们只用到 `postMessage`）。 */
  contentWindow: Window;
  postMessage: ReturnType<typeof vi.fn>;
}

function makeFrame(): FakeFrame {
  const postMessage = vi.fn();
  const contentWindow = { postMessage } as unknown as Window;
  const iframe = { contentWindow } as unknown as HTMLIFrameElement;
  return { iframe, contentWindow, postMessage };
}

/** 造一条消息事件并派发到 window（`source` 决定"谁发的"）。 */
function dispatch(data: unknown, origin: string, source: Window | null): void {
  window.dispatchEvent(
    new MessageEvent('message', {
      data,
      origin,
      source: source as unknown as MessageEventSource | null,
    }),
  );
}

/** `ready` 之后要等 `onReady()` 的微任务链跑完。 */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function photoPayload(id: string): StudioInitPayload {
  return {
    photos: [
      {
        id,
        bytes: new Uint8Array([1, 2, 3, 4]).buffer,
        name: `${id}.jpg`,
        thumbnailDataUrl: 'data:image/jpeg;base64,AQIDBA==',
      },
    ],
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('createStudioBridge —— 来源校验', () => {
  it('origin 不匹配的消息被忽略（不发 init）', async () => {
    const frame = makeFrame();
    const onReady = vi.fn(() => photoPayload('p1'));
    const bridge = createStudioBridge({ iframe: frame.iframe, targetOrigin: TARGET, handlers: { onReady } });

    dispatch({ type: 'ready' }, 'https://evil.example', frame.contentWindow);
    await flush();

    expect(onReady).not.toHaveBeenCalled();
    expect(frame.postMessage).not.toHaveBeenCalled();
    bridge.dispose();
  });

  it('origin 对但 source 不是这个 iframe 的消息被忽略', async () => {
    const frame = makeFrame();
    const other = makeFrame();
    const onReady = vi.fn(() => photoPayload('p1'));
    const bridge = createStudioBridge({ iframe: frame.iframe, targetOrigin: TARGET, handlers: { onReady } });

    // 同一个 origin 上的另一个 iframe：只判 origin 的实现会错误地接受它
    dispatch({ type: 'ready' }, TARGET, other.contentWindow);
    await flush();

    expect(onReady).not.toHaveBeenCalled();
    expect(frame.postMessage).not.toHaveBeenCalled();
    bridge.dispose();
  });

  it('source 与 origin 都匹配：调 onReady 并用精确 targetOrigin 发 init', async () => {
    const frame = makeFrame();
    const onReady = vi.fn(() => photoPayload('p1'));
    const bridge = createStudioBridge({ iframe: frame.iframe, targetOrigin: TARGET, handlers: { onReady } });

    dispatch({ type: 'ready' }, TARGET, frame.contentWindow);
    await flush();

    expect(onReady).toHaveBeenCalledTimes(1);
    expect(frame.postMessage).toHaveBeenCalledTimes(1);
    const [message, targetOrigin, transfer] = frame.postMessage.mock.calls[0] as [
      { type: string; payload: StudioInitPayload },
      string,
      Transferable[],
    ];
    expect(message.type).toBe('init');
    expect(message.payload.photos?.[0].id).toBe('p1');
    // 绝不 `'*'`
    expect(targetOrigin).toBe(TARGET);
    // 缩略图字节走 transfer（不复制），且每个 buffer 只出现一次
    expect(transfer).toHaveLength(1);
    expect(transfer[0]).toBe(message.payload.photos?.[0].bytes);
    bridge.dispose();
  });

  it('onReady 抛错时报 onError，且不发 init', async () => {
    const frame = makeFrame();
    const onError = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: {
        onReady: () => {
          throw new Error('读缩略图失败');
        },
        onError,
      },
    });

    dispatch({ type: 'ready' }, TARGET, frame.contentWindow);
    await flush();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(String(onError.mock.calls[0][0])).toContain('读缩略图失败');
    expect(frame.postMessage).not.toHaveBeenCalled();
    bridge.dispose();
  });
});

describe('createStudioBridge —— 子 → 父消息转发', () => {
  it('import-to-main 把 journey 原样交给上层', async () => {
    const frame = makeFrame();
    const onImportToMain = vi.fn();
    const journey = { version: 'journey-2026.1', title: 'x', days: [] };
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}), onImportToMain },
    });

    dispatch({ type: 'import-to-main', journey }, TARGET, frame.contentWindow);
    await flush();

    expect(onImportToMain).toHaveBeenCalledWith(journey);
    bridge.dispose();
  });

  it('import-to-main 回调抛错时报 onError（不让错误冒到 window）', async () => {
    const frame = makeFrame();
    const onError = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: {
        onReady: () => ({}),
        onImportToMain: () => {
          throw new Error('后端 500');
        },
        onError,
      },
    });

    dispatch({ type: 'import-to-main', journey: {} }, TARGET, frame.contentWindow);
    await flush();

    expect(String(onError.mock.calls[0][0])).toContain('后端 500');
    bridge.dispose();
  });

  it('download 转发 blob 与文件名；文件名空白时回落 download', () => {
    const frame = makeFrame();
    const onDownload = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}), onDownload },
    });
    const blob = new Blob(['hello'], { type: 'text/plain' });

    dispatch({ type: 'download', blob, filename: 'a.trailshare' }, TARGET, frame.contentWindow);
    dispatch({ type: 'download', blob, filename: '   ' }, TARGET, frame.contentWindow);

    expect(onDownload).toHaveBeenNthCalledWith(1, blob, 'a.trailshare');
    expect(onDownload).toHaveBeenNthCalledWith(2, blob, 'download');
    bridge.dispose();
  });

  it('download 的 blob 不是 Blob 时报 onError（另一个 origin 的东西不能信）', () => {
    const frame = makeFrame();
    const onDownload = vi.fn();
    const onError = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}), onDownload, onError },
    });

    dispatch({ type: 'download', blob: 'not-a-blob', filename: 'a.bin' }, TARGET, frame.contentWindow);

    expect(onDownload).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    bridge.dispose();
  });

  it('error 消息转成 onError；空 message 用兜底文案', () => {
    const frame = makeFrame();
    const onError = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}), onError },
    });

    dispatch({ type: 'error', message: '录屏不被支持' }, TARGET, frame.contentWindow);
    dispatch({ type: 'error' }, TARGET, frame.contentWindow);

    expect(onError).toHaveBeenNthCalledWith(1, '录屏不被支持');
    expect(String(onError.mock.calls[1][0])).toContain('未知错误');
    bridge.dispose();
  });

  it('未知 type 静默忽略', () => {
    const frame = makeFrame();
    const onError = vi.fn();
    const onImportToMain = vi.fn();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}), onError, onImportToMain },
    });

    dispatch({ type: 'future-thing', data: 1 }, TARGET, frame.contentWindow);
    dispatch({ notEvenAType: true }, TARGET, frame.contentWindow);

    expect(onError).not.toHaveBeenCalled();
    expect(onImportToMain).not.toHaveBeenCalled();
    bridge.dispose();
  });

  it('dispose 之后不再处理消息，且重复调用不抛错', async () => {
    const frame = makeFrame();
    const onReady = vi.fn(() => photoPayload('p1'));
    const bridge = createStudioBridge({ iframe: frame.iframe, targetOrigin: TARGET, handlers: { onReady } });

    bridge.dispose();
    expect(() => bridge.dispose()).not.toThrow();

    dispatch({ type: 'ready' }, TARGET, frame.contentWindow);
    await flush();

    expect(onReady).not.toHaveBeenCalled();
    expect(frame.postMessage).not.toHaveBeenCalled();
  });

  it('sendInit 可以在握手之外手动调（重试用）', () => {
    const frame = makeFrame();
    const bridge = createStudioBridge({
      iframe: frame.iframe,
      targetOrigin: TARGET,
      handlers: { onReady: () => ({}) },
    });

    bridge.sendInit(photoPayload('manual'));

    expect(frame.postMessage).toHaveBeenCalledTimes(1);
    expect(frame.postMessage.mock.calls[0][1]).toBe(TARGET);
    bridge.dispose();
  });
});

describe('collectTransferables', () => {
  it('只收 photos[].bytes，跳过 0 字节与非 ArrayBuffer', () => {
    const a = new Uint8Array([1]).buffer;
    const b = new Uint8Array([2]).buffer;
    const list = collectTransferables({
      photos: [
        { id: 'a', bytes: a, name: 'a.jpg', thumbnailDataUrl: 'data:image/jpeg;base64,AA==' },
        { id: 'b', bytes: b, name: 'b.jpg', thumbnailDataUrl: 'data:image/jpeg;base64,AA==' },
        { id: 'empty', bytes: new ArrayBuffer(0), name: 'c.jpg', thumbnailDataUrl: 'data:,' },
        {
          id: 'bad',
          bytes: 'nope' as unknown as ArrayBuffer,
          name: 'd.jpg',
          thumbnailDataUrl: 'data:image/jpeg;base64,AA==',
        },
      ],
    });
    expect(list).toEqual([a, b]);
  });

  it('同一个 ArrayBuffer 被两张照片引用时只出现一次（重复 transfer 会 DataCloneError）', () => {
    const shared = new Uint8Array([9]).buffer;
    const list = collectTransferables({
      photos: [
        { id: 'a', bytes: shared, name: 'a.jpg', thumbnailDataUrl: 'data:image/jpeg;base64,AA==' },
        { id: 'b', bytes: shared, name: 'b.jpg', thumbnailDataUrl: 'data:image/jpeg;base64,AA==' },
      ],
    });
    expect(list).toHaveLength(1);
    expect(list[0]).toBe(shared);
  });

  it('没有 photos 时返回空数组', () => {
    expect(collectTransferables({})).toEqual([]);
    expect(collectTransferables({ identity: undefined, trips: [] })).toEqual([]);
  });
});
