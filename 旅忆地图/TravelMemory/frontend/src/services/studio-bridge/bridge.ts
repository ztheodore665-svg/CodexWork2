/** 主应用这一侧的 iframe 桥（阶段 11）：与小应用收发 postMessage。
 *
 * ## 与阶段 10 的分工
 *
 * 小应用侧（`apps/*\/src/bridge.ts`）已经实现好了：挂载时发 `{type:'ready'}`、
 * 只接受白名单 origin 的 `init` / `photos-update`、回发时用精确 origin。
 * 这里补上父窗口那一半：收到 `ready` → 取数据 → 发 `init`，
 * 并把 `import-to-main` / `download` / `error` 转发给 UI 层。
 *
 * ## 两道校验，缺一不可
 *
 * ```ts
 * if (event.source !== iframe.contentWindow) return;  // 只认这一个 iframe
 * if (event.origin !== targetOrigin) return;          // 只认这一个 origin
 * ```
 *
 * 只判 origin 不够：同一个页面里可能嵌着**多个**第三方 iframe（地图插件、客服控件），
 * 它们若与小应用同源（比如都是 localhost 的不同端口 —— 端口不同就是不同 origin，
 * 但同一 host 上的另一个 iframe 完全可能与它同源），仅判 origin 会让"另一个 iframe 的
 * 消息"被当成本应用的消息。反过来只判 source 也不够：`contentWindow` 在导航后仍是
 * 同一个对象，但文档已经换成了别人。两个条件同时成立才处理。
 *
 * ## transfer 为什么要去重
 *
 * `postMessage(msg, origin, transfer)` 会**移交**（detach）这些 `ArrayBuffer`。
 * 同一个 `ArrayBuffer` 出现在 transfer 列表里两次是 `DataCloneError`（整条消息发不出去）；
 * 而一张缩略图被两个 photo 引用是可能发生的（同一照片进了两个地点）。
 * 所以先按对象身份去重，见 `collectTransferables`。
 *
 * ## 不引第三方库
 *
 * 这个模块的全部逻辑就是 `addEventListener` + `postMessage` + 一次 `Set` 去重，
 * 引一个 "iframe 通信库" 只会多一层看不见的 origin 判定。
 */

import type { StudioChildMessage, StudioInitPayload } from './types';

/** 桥要回调给 UI 的四件事。除 `onReady` 外都可选：不关心就传 undefined。 */
export interface StudioBridgeHandlers {
  /** 收到 `ready` 时调用：**返回**要注入的载荷（可以是异步的，比如读缩略图）。 */
  onReady: () => StudioInitPayload | Promise<StudioInitPayload>;
  /** 小应用要求把一份 journey 导回主应用（`{ type: 'import-to-main' }`）。 */
  onImportToMain?: (journey: unknown) => void | Promise<void>;
  /** 小应用要求下载一个文件（`{ type: 'download' }`）。 */
  onDownload?: (blob: Blob, filename: string) => void;
  /** 小应用报告错误，或桥自己出问题（`{ type: 'error' }` / 序列化失败）。 */
  onError?: (message: string) => void;
}

export interface StudioBridgeOptions {
  /** 已经挂载到文档里的 iframe 元素。**必须在 effect 里调用本函数**，否则 `contentWindow` 为 null。 */
  iframe: HTMLIFrameElement;
  /** 小应用的 origin（从它的 URL 里取，不含 path）。回发只用这个，绝不 `'*'`。 */
  targetOrigin: string;
  handlers: StudioBridgeHandlers;
}

export interface StudioBridge {
  /** 移除监听。**幂等**：重复调用不会抛错，也不会重复移除。 */
  dispose: () => void;
  /** 主动发一次 `init`（正常流程由 `ready` 触发；调试或重试时才需要手动调）。 */
  sendInit: (payload: StudioInitPayload) => void;
}

/** 从错误的初值里取一句能看的文案。 */
function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * 收集需要 transfer 的 `ArrayBuffer`（按对象身份去重）。
 *
 * 只收 `photos[].bytes`：身份是字符串、`thumbnailDataUrl` 是字符串，
 * Blob 不在 `init` 里出现。`byteLength === 0` 的（transfer 过的残留）跳过 ——
 * 移交一个空 buffer 会把小应用那张照片变成 0 字节，而它那边只会静默丢弃。
 */
export function collectTransferables(payload: StudioInitPayload): Transferable[] {
  const seen = new Set<ArrayBuffer>();
  const out: Transferable[] = [];
  for (const photo of payload.photos ?? []) {
    const bytes = photo.bytes;
    if (!(bytes instanceof ArrayBuffer)) continue;
    if (bytes.byteLength === 0) continue;
    if (seen.has(bytes)) continue;
    seen.add(bytes);
    out.push(bytes);
  }
  return out;
}

/**
 * 建立桥。
 *
 * @returns `dispose()` + `sendInit()`
 *
 * 生命周期：**在 iframe 挂载之后、卸载之前**创建/销毁。
 * 监听器要在 iframe 内的脚本跑起来（发 `ready`）之前就位，
 * 所以调用点必须放在 `useEffect` 里（DOM 已 commit、子文档尚未加载完）。
 */
export function createStudioBridge(options: StudioBridgeOptions): StudioBridge {
  const { iframe, targetOrigin, handlers } = options;

  let disposed = false;

  const post = (message: unknown, transfer?: Transferable[]): boolean => {
    const target = iframe.contentWindow;
    if (!target) return false;
    try {
      if (transfer && transfer.length > 0) {
        target.postMessage(message, targetOrigin, transfer);
      } else {
        target.postMessage(message, targetOrigin);
      }
      return true;
    } catch (err) {
      // 典型原因：transfer 列表里有重复或不可移交的对象。**退化成结构化复制再试一次**
      // —— 慢一点（几十 KB 的缩略图复制一遍可以忽略），但比"点了没反应"好。
      try {
        target.postMessage(message, targetOrigin);
        return true;
      } catch (again) {
        handlers.onError?.(`向小应用发送消息失败：${describeError(again || err)}`);
        return false;
      }
    }
  };

  const sendInit = (payload: StudioInitPayload): void => {
    if (disposed) return;
    post({ type: 'init', payload }, collectTransferables(payload));
  };

  const handleReady = async (): Promise<void> => {
    let payload: StudioInitPayload;
    try {
      payload = await handlers.onReady();
    } catch (err) {
      handlers.onError?.(`准备数据失败：${describeError(err)}`);
      return;
    }
    // await 期间可能已经关闭对话框（iframe 被卸载）：此时再发毫无意义
    if (disposed) return;
    sendInit(payload);
  };

  const onMessage = (event: MessageEvent): void => {
    if (disposed) return;
    // ①只认这一个 iframe（比只判 origin 更严，见文件头）
    if (!iframe.contentWindow || event.source !== iframe.contentWindow) return;
    // ②只认这个小应用的 origin
    if (event.origin !== targetOrigin) return;

    const data: unknown = event.data;
    if (!data || typeof data !== 'object') return;
    const message = data as Partial<StudioChildMessage> & { type?: unknown };

    switch (message.type) {
      case 'ready':
        // 不设"只处理一次"的闸：dev 下 StrictMode 会让子应用挂载两次、发两次 ready，
        // 每次都重建载荷（`onReady` 负责重新读缩略图）才能保证第二次也拿到有效字节。
        void handleReady();
        return;
      case 'import-to-main': {
        const journey = (message as { journey?: unknown }).journey;
        // 只转发不校验：形状校验在 `journey-import` 里做，桥不该知道 journey 长什么样
        void (async () => {
          try {
            await handlers.onImportToMain?.(journey);
          } catch (err) {
            handlers.onError?.(`导回旅程失败：${describeError(err)}`);
          }
        })();
        return;
      }
      case 'download': {
        const { blob, filename } = message as { blob?: unknown; filename?: unknown };
        if (!(blob instanceof Blob)) {
          handlers.onError?.('小应用要求下载一个文件，但传来的不是 Blob');
          return;
        }
        const safeName =
          typeof filename === 'string' && filename.trim().length > 0 ? filename.trim() : 'download';
        handlers.onDownload?.(blob, safeName);
        return;
      }
      case 'error': {
        const raw = (message as { message?: unknown }).message;
        handlers.onError?.(
          typeof raw === 'string' && raw.trim().length > 0 ? raw.trim() : '小应用报告了一个未知错误',
        );
        return;
      }
      default:
        // 未知类型静默忽略：父窗口不该因为小应用升级多发了一种消息就报错
        return;
    }
  };

  window.addEventListener('message', onMessage);

  return {
    dispose: (): void => {
      if (disposed) return;
      disposed = true;
      window.removeEventListener('message', onMessage);
    },
    sendInit,
  };
}
