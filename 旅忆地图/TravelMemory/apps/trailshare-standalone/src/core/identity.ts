/** 身份：主应用注入（内存）或本机临时身份（§10.1 / §10.2）。
 *
 * 与 Track Studio 同一套做法（两个 app 独立部署，各持一份，不共包），只是库名不同。
 *
 * | `identitySource()` | 什么情况 | 存哪 |
 * |---|---|---|
 * | `'injected'` | 被主应用 iframe 嵌入，且 `init.payload.identity` 合法 | **只在模块内存里** |
 * | `'local'` | 单独打开，IndexedDB 可用 | 本 origin 的 IndexedDB |
 * | `'memory'` | 单独打开，但 IndexedDB 不可用（隐私模式 / 配额满） | 只在内存，刷新即变 |
 *
 * **铁律：注入身份绝不落盘。** 主应用的种子被写进另一个 origin 的 IndexedDB，
 * 等于"为了传一次而永久复制一份"：泄漏面更大，而且用户清主应用数据时带不走这份副本。
 *
 * **另一半铁律：种子永不外发** —— 这里尤其重要，因为 `pack()` 会用这把种子签名，
 * 而签名结果会经 `sendToParent({type:'download'})` 交给父窗口。所以 `bridge.sendToParent`
 * 里有一道值检查兜底，只放行不含私钥字段/种子值的消息。
 *
 * ## 为什么 IndexedDB 失败也要能用
 *
 * 隐私模式 / 禁用第三方存储 / 配额满都会让 `indexedDB.open` 抛错或永远不回调。
 * 这时候抛错等于把整个应用打死；退化成"内存身份"（刷新即变）是唯一合理选择。
 */

import { ed25519 } from '@noble/curves/ed25519.js';

export const IDENTITY_DB = 'trailshare-standalone';
export const IDENTITY_STORE = 'keypair';
export const IDENTITY_KEY = 'me';
/** 公钥前缀。与主应用、TrailShare 协议一致。 */
export const PUBKEY_PREFIX = 'ed25519:';

/** 身份来源。UI 据此决定显示哪句文案（不显示注入身份的公钥）。 */
export type IdentitySource = 'injected' | 'local' | 'memory';

export interface StoredKeypair {
  seed: string;
  createdAt: string;
}

export interface LocalIdentity {
  /** 32 字节种子的 hex。**绝不外发**。 */
  seedHex: string;
  /** `seedHex` 的别名：协议里私钥就叫 `privHex`（Ed25519 种子即私钥）。 */
  privHex: string;
  pubHex: string;
  pubkey: string;
  createdAt: string;
  persistent: boolean;
}

let cached: LocalIdentity | null = null;
/** `cached` 是不是真的落盘了（决定 `identitySource()` 是 'local' 还是 'memory'）。 */
let cachedPersistent = false;
/** 主应用注入的身份。**只在内存**，一旦设置就优先于本机身份。 */
let injected: LocalIdentity | null = null;

export function bytesToHex(bytes: Uint8Array): string {
  let hex = '';
  for (const byte of bytes) hex += byte.toString(16).padStart(2, '0');
  return hex;
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex;
  const length = clean.length >> 1;
  const out = new Uint8Array(length);
  for (let index = 0; index < length; index += 1) {
    out[index] = Number.parseInt(clean.slice(index * 2, index * 2 + 2), 16);
  }
  return out;
}

/** 32 字节随机种子（hex，64 字符）。 */
export function newSeedHex(): string {
  const bytes = new Uint8Array(32);
  const crypto_ = globalThis.crypto;
  if (crypto_ && typeof crypto_.getRandomValues === 'function') {
    crypto_.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return bytesToHex(bytes);
}

/** 种子 → Ed25519 公钥 bare hex（与主应用 `publicKeyFromSeed` 同一写法）。 */
export function publicKeyFromSeed(seedHex: string): string {
  const bytes = hexToBytes(seedHex.trim());
  if (bytes.length !== 32) {
    throw new Error(`种子必须是 32 字节（64 hex），收到 ${bytes.length} 字节`);
  }
  return bytesToHex(ed25519.getPublicKey(bytes));
}

/** 展示用短公钥：bare hex 的前 12 位。 */
export function shortPubkey(pubkey: string): string {
  const bare = pubkey.startsWith(PUBKEY_PREFIX) ? pubkey.slice(PUBKEY_PREFIX.length) : pubkey;
  return bare.slice(0, 12);
}

export function identityFromSeedRecord(record: StoredKeypair, persistent: boolean): LocalIdentity {
  const pubHex = publicKeyFromSeed(record.seed);
  return {
    seedHex: record.seed,
    privHex: record.seed,
    pubHex,
    pubkey: `${PUBKEY_PREFIX}${pubHex}`,
    createdAt: record.createdAt,
    persistent,
  };
}

/* ── 注入身份（§10.2） ─────────────────────────────────────── */

/** `privHex` 的合法形状：64 位 hex，可带 `ed25519:` 前缀。 */
const PRIV_HEX_RE = /^[0-9a-f]{64}$/i;

function normalizePrivHex(value: string): string {
  const trimmed = value.trim();
  const bare = trimmed.startsWith(PUBKEY_PREFIX) ? trimmed.slice(PUBKEY_PREFIX.length) : trimmed;
  return bare.toLowerCase();
}

/**
 * 接受主应用注入的身份（`init.payload.identity`，形状未知）。
 *
 * @returns 校验通过 `true`；不合格 `false` 且状态不变（**绝不抛错**）
 *
 * 只认 `{ privHex: string, pubkey?: string }`；公钥由种子现算（种子是真源），
 * 主应用给的 `pubkey` 只做一致性提示。
 *
 * ⚠️ **不碰 IndexedDB**：注入身份只活在模块内存，页面关闭即消失。
 */
export function setInjectedIdentity(input: unknown): boolean {
  if (!input || typeof input !== 'object') return false;
  const record = input as Record<string, unknown>;
  const rawPriv = record.privHex;
  if (typeof rawPriv !== 'string') return false;
  const privHex = normalizePrivHex(rawPriv);
  if (!PRIV_HEX_RE.test(privHex)) return false;

  let identity: LocalIdentity;
  try {
    identity = identityFromSeedRecord(
      { seed: privHex, createdAt: new Date().toISOString() },
      false,
    );
  } catch {
    return false;
  }

  const providedPub = record.pubkey;
  if (typeof providedPub === 'string' && providedPub.trim().length > 0) {
    const bare = providedPub.trim().startsWith(PUBKEY_PREFIX)
      ? providedPub.trim().slice(PUBKEY_PREFIX.length)
      : providedPub.trim();
    if (bare.toLowerCase() !== identity.pubHex.toLowerCase()) {
      console.warn('[identity] 主应用给的 pubkey 与种子派生的不一致，以种子为准');
    }
  }

  injected = identity;
  return true;
}

/** 丢掉注入身份，退回本机身份。 */
export function clearInjectedIdentity(): void {
  injected = null;
}

/** 当前身份来源；还没加载也没注入时返回 `'local'`（"会走本机 IDB 路径"）。 */
export function identitySource(): IdentitySource {
  if (injected) return 'injected';
  if (cached) return cachedPersistent ? 'local' : 'memory';
  return 'local';
}

/** 当前身份（同步读）。注入优先；都没准备好时返回 `null`，调用方应 await `loadIdentity()`。 */
export function getIdentity(): LocalIdentity | null {
  return injected ?? cached;
}

/** 当前进程**已知的种子值**。给 `bridge.sendToParent` 做外发兜底（不触发 IDB 读取）。 */
export function secretHexValues(): string[] {
  const values: string[] = [];
  if (injected) values.push(injected.seedHex);
  if (cached) values.push(cached.seedHex);
  return values;
}

function openIdentityDb(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined' || indexedDB === null) {
      reject(new Error('IndexedDB 不可用'));
      return;
    }
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(IDENTITY_DB, 1);
    } catch (error) {
      reject(error instanceof Error ? error : new Error('打开 IndexedDB 失败'));
      return;
    }
    request.onupgradeneeded = (): void => {
      const db = request.result;
      if (!db.objectStoreNames.contains(IDENTITY_STORE)) db.createObjectStore(IDENTITY_STORE);
    };
    request.onsuccess = (): void => resolve(request.result);
    request.onerror = (): void => reject(request.error ?? new Error('打开 IndexedDB 失败'));
    request.onblocked = (): void => reject(new Error('IndexedDB 被其它标签页阻塞'));
  });
}

function readKeypair(db: IDBDatabase): Promise<StoredKeypair | null> {
  return new Promise<StoredKeypair | null>((resolve, reject) => {
    try {
      const tx = db.transaction(IDENTITY_STORE, 'readonly');
      const request = tx.objectStore(IDENTITY_STORE).get(IDENTITY_KEY);
      request.onsuccess = (): void => {
        const value: unknown = request.result;
        if (value && typeof value === 'object') {
          const record = value as Partial<StoredKeypair>;
          if (typeof record.seed === 'string' && record.seed.length > 0) {
            resolve({
              seed: record.seed,
              createdAt:
                typeof record.createdAt === 'string' ? record.createdAt : new Date().toISOString(),
            });
            return;
          }
        }
        resolve(null);
      };
      request.onerror = (): void => reject(request.error ?? new Error('读取身份失败'));
    } catch (error) {
      reject(error instanceof Error ? error : new Error('读取身份失败'));
    }
  });
}

function writeKeypair(db: IDBDatabase, record: StoredKeypair): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    try {
      const tx = db.transaction(IDENTITY_STORE, 'readwrite');
      tx.objectStore(IDENTITY_STORE).put(record, IDENTITY_KEY);
      tx.oncomplete = (): void => resolve();
      tx.onerror = (): void => reject(tx.error ?? new Error('写入身份失败'));
      tx.onabort = (): void => reject(tx.error ?? new Error('写入身份被中止'));
    } catch (error) {
      reject(error instanceof Error ? error : new Error('写入身份失败'));
    }
  });
}

function memoryIdentity(): LocalIdentity {
  const record: StoredKeypair = { seed: newSeedHex(), createdAt: new Date().toISOString() };
  return identityFromSeedRecord(record, false);
}

/**
 * 取（必要时创建）身份。**永不 reject**。
 *
 * 优先级：**注入身份 > 本机 IndexedDB 身份 > 内存身份**（§10.2）。
 * 注入身份不会写进 IDB。
 */
export async function loadIdentity(): Promise<LocalIdentity> {
  if (injected) return injected;
  if (cached) return cached;
  let db: IDBDatabase | null = null;
  try {
    db = await openIdentityDb();
    let record = await readKeypair(db);
    if (!record) {
      record = { seed: newSeedHex(), createdAt: new Date().toISOString() };
      await writeKeypair(db, record);
    }
    cached = identityFromSeedRecord(record, true);
    cachedPersistent = true;
    return cached;
  } catch {
    cached = memoryIdentity();
    cachedPersistent = false;
    return cached;
  } finally {
    db?.close();
  }
}

/** 清掉模块缓存（注入身份不受影响）。 */
export function resetIdentityCache(): void {
  cached = null;
  cachedPersistent = false;
}
