/** iframe 桥（§9）：与父窗口（主应用）收发消息。
 *
 * 与 Track Studio 的同名文件同构，差别只在注释里的例子（这里传的是 `download`
 * 与 `import-to-main`）。三条不变式：
 *
 * 1. **没被嵌入时什么都不做**（`initBridge` 返回空清理函数，不发 `ready`）；
 * 2. 只接受白名单 origin（`PARENT_ORIGIN` 与自身 origin，后者给同源 harness）；
 * 3. 收到的照片走 `transfer` 的 `ArrayBuffer` 直接接管，非法元素静默丢弃、绝不抛错。
 */

import { FALLBACK_THUMBNAIL_DATA_URL, type SystemPhoto } from '@travelmemory/photo-source';

import { PARENT_ORIGIN } from '../csp';
import { secretHexValues } from '../core/identity';

// 单一来源：CSP 的 frame-ancestors 与这里的 targetOrigin 必须是同一个域名。
export { PARENT_ORIGIN };

export interface BridgeInitPayload {
  identity?: unknown;
  photos?: unknown;
  trips?: unknown;
}

export interface BridgeHandlers {
  onInit: (payload: BridgeInitPayload) => void;
  onPhotosUpdate: (photos: SystemPhoto[]) => void;
}

/** 是否被主应用嵌入（`?embedded=1`）。 */
export function isEmbedded(): boolean {
  if (typeof location === 'undefined') return false;
  try {
    return new URLSearchParams(location.search).get('embedded') === '1';
  } catch {
    return false;
  }
}

/** 父窗口 origin 的 URL 参数名（阶段 11：主应用 iframe 嵌入时用它精确指定目标）。 */
const PARENT_OVERRIDE_PARAM = 'parentOrigin';

/**
 * 读 `?parentOrigin=` 覆盖。
 *
 * 为什么需要它：部署后白名单里只有 `PARENT_ORIGIN`（占位域名）与自身 origin，
 * 而主应用 dev（`http://localhost:5173`）两者都不是 —— 没有这个参数，
 * 本地联调时小应用会把 `ready` 发给自己，父窗口永远收不到握手。
 *
 * 安全约束（与 `PARENT_ORIGIN` 一样严）：
 * - 只接受 `http:` / `https:`；`file:`、`javascript:`、`data:` 一律判非法；
 * - 只取 `origin`（丢弃 path / query / hash），因此不可能塞进 `'*'`；
 * - 解析失败（`not-a-url`）返回 null，而不是抛错。
 *
 * 向后兼容：没有该参数时返回 null，`allowedOrigins()` / `targetOrigin()`
 * 的行为与阶段 10 完全一致。
 */
function readParentOverride(): string | null {
  if (typeof location === 'undefined') return null;
  const raw = new URLSearchParams(location.search).get(PARENT_OVERRIDE_PARAM);
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.origin;
  } catch {
    return null;
  }
}

/** 模块加载时算一次：`?parentOrigin=` 在页面生命周期内不会变（不响应后续 pushState）。 */
const PARENT_OVERRIDE = readParentOverride();

/** 允许的消息来源：主应用 + `?parentOrigin=` 覆盖 + 自身（同源 harness），已去重。 */
export function allowedOrigins(): string[] {
  const origins: string[] = [];
  const candidates: Array<string | null> = [
    PARENT_ORIGIN,
    PARENT_OVERRIDE,
    typeof location !== 'undefined' ? location.origin : '',
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.length > 0 && !origins.includes(candidate)) {
      origins.push(candidate);
    }
  }
  return origins;
}

function isLoopbackOrigin(origin: string): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(origin);
}

/**
 * 回发消息的 targetOrigin。
 *
 * TODO(歧义): 无法可靠区分"同源 harness"与"被本地主应用嵌入"（都是 loopback），
 * 这里按 loopback 判定用自身 origin，否则用主应用占位域名。绝不使用 `'*'`。
 * 阶段 11 起主应用会用 `?parentOrigin=http://localhost:5173` 显式指定，
 * 有该参数时以它为唯一目标（见 `readParentOverride`），此歧义只在没有参数时存在。
 */
export function targetOrigin(): string {
  if (PARENT_OVERRIDE) return PARENT_OVERRIDE;
  const origin = typeof location === 'undefined' ? '' : location.origin;
  if (origin.length === 0) return PARENT_ORIGIN;
  if (origin === PARENT_ORIGIN) return origin;
  return isLoopbackOrigin(origin) ? origin : PARENT_ORIGIN;
}

/** 把父窗口传来的 `unknown` 数组洗成 `SystemPhoto[]`。非法元素静默丢弃。 */
export function parseBridgePhotos(raw: unknown): SystemPhoto[] {
  if (!Array.isArray(raw)) return [];
  const out: SystemPhoto[] = [];
  raw.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const record = item as Record<string, unknown>;
    const bytes = toArrayBuffer(record.bytes);
    if (!bytes) return;
    const thumbnail =
      typeof record.thumbnailDataUrl === 'string' && record.thumbnailDataUrl.startsWith('data:')
        ? record.thumbnailDataUrl
        : FALLBACK_THUMBNAIL_DATA_URL;
    const photo: SystemPhoto = {
      id: typeof record.id === 'string' && record.id.length > 0 ? record.id : `p_bridge${index}`,
      bytes,
      name:
        typeof record.name === 'string' && record.name.length > 0 ? record.name : `照片${index + 1}`,
      thumbnailDataUrl: thumbnail,
    };
    if (typeof record.takenAt === 'string') photo.takenAt = record.takenAt;
    if (typeof record.lat === 'number' && Number.isFinite(record.lat)) photo.lat = record.lat;
    if (typeof record.lng === 'number' && Number.isFinite(record.lng)) photo.lng = record.lng;
    out.push(photo);
  });
  return out;
}

function toArrayBuffer(value: unknown): ArrayBuffer | null {
  if (value instanceof ArrayBuffer) return value;
  if (ArrayBuffer.isView(value)) {
    const view = value as ArrayBufferView;
    return view.buffer instanceof ArrayBuffer
      ? view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength)
      : null;
  }
  return null;
}

/**
 * 启动桥。**只在嵌入模式下做事**。
 *
 * @returns 清理函数（未嵌入时是空函数，可以无条件调用）
 */
export function initBridge(handlers: BridgeHandlers): () => void {
  if (typeof window === 'undefined' || !isEmbedded()) {
    return () => {
      /* 未嵌入：无事可做 */
    };
  }

  const origins = allowedOrigins();
  const onMessage = (event: MessageEvent): void => {
    if (!origins.includes(event.origin)) return;
    const data: unknown = event.data;
    if (!data || typeof data !== 'object') return;
    const message = data as { type?: unknown; payload?: unknown; photos?: unknown };
    if (message.type === 'init') {
      const payload =
        message.payload && typeof message.payload === 'object'
          ? (message.payload as BridgeInitPayload)
          : {};
      handlers.onInit(payload);
      return;
    }
    if (message.type === 'photos-update') {
      handlers.onPhotosUpdate(parseBridgePhotos(message.photos));
    }
  };

  window.addEventListener('message', onMessage);
  sendToParent({ type: 'ready' });

  return () => {
    window.removeEventListener('message', onMessage);
  };
}

/**
 * 外发前的**运行时兜底**：载荷里出现私钥字段名或已知种子值 → 判定为泄漏。
 *
 * 对这个应用尤其关键：`pack()` 会用注入进来的种子签名，而签名结果要经
 * `sendToParent({type:'download'})` 交回父窗口。一旦有人顺手把整个 identity 对象
 * 塞进消息里，主应用的种子就会被"回声"回父页（甚至留在父页日志里）。
 * 类型系统拦不住普通的对象字面量，所以这里用值检查兜底。
 *
 * 序列化失败（循环引用）也判为泄漏 —— 宁可丢一条消息。
 */
export function containsSecretPayload(message: unknown): boolean {
  let text: string;
  try {
    text = JSON.stringify(message) ?? '';
  } catch {
    return true;
  }
  if (text.length === 0) return false;
  if (/"(privHex|seedHex|seed)"/i.test(text)) return true;
  const lower = text.toLowerCase();
  return secretHexValues().some(
    (secret) => secret.length >= 32 && lower.includes(secret.toLowerCase()),
  );
}

/** 给父窗口发消息。未嵌入时静默丢弃。 */
export function sendToParent(message: unknown, transfer?: Transferable[]): void {
  if (typeof window === 'undefined' || !isEmbedded()) return;
  if (containsSecretPayload(message)) {
    console.warn('[bridge] 消息里含私钥/种子字段，已拒绝外发');
    return;
  }
  try {
    if (transfer && transfer.length > 0) {
      window.parent.postMessage(message, targetOrigin(), transfer);
    } else {
      window.parent.postMessage(message, targetOrigin());
    }
  } catch {
    // 父窗口可能已经消失（iframe 被移除），忽略
  }
}
