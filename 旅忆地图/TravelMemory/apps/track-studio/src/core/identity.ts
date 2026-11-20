/** 身份：主应用注入（内存）或本机临时身份（§10.1 / §10.2）。
 *
 * ## 三种来源，一条铁律
 *
 * | `identitySource()` | 什么情况 | 存哪 |
 * |---|---|---|
 * | `'injected'` | 被主应用 iframe 嵌入，且 `init.payload.identity` 合法 | **只在模块内存里** |
 * | `'local'` | 单独打开，IndexedDB 可用 | 本 origin 的 IndexedDB |
 * | `'memory'` | 单独打开，但 IndexedDB 不可用（隐私模式 / 配额满） | 只在内存，刷新即变 |
 *
 * **铁律：注入身份绝不落盘。** 主应用的种子如果被写进另一个 origin 的 IndexedDB，
 * 就等于"为了传一次而永久复制一份"—— 泄漏面比"传一次"大得多，而且用户清掉主应用的
 * 数据也带不走这份副本。所以 `setInjectedIdentity()` 只赋值内存变量，一行 IDB 代码都不碰。
 *
 * 另一半铁律：**种子永不外发**——不发 postMessage、不写 URL、不上传（§10.3）。
 * `sendToParent` 里还有一道运行时兜底（见 `bridge.ts`），双重保险。
 *
 * ## 为什么 IndexedDB 失败也要能用
 *
 * 隐私模式 / 禁用第三方存储 / 配额满都会让 `indexedDB.open` 抛错或永远不回调。
 * 这时候抛错等于把整个应用打死；退化成"内存身份"（刷新即变）是唯一合理选择，
 * 并且用 `persistent: false` 让 UI 能说实话。
 */

import { ed25519 } from '@noble/curves/ed25519.js';

export const IDENTITY_DB = 'track-studio-identity';
export const IDENTITY_STORE = 'keypair';
export const IDENTITY_KEY = 'me';
/** 公钥前缀。与主应用 / TrailShare 协议一致，便于将来互换。 */
export const PUBKEY_PREFIX = 'ed25519:';

/** 身份来源。UI 据此决定显示哪句文案（不显示注入身份的公钥，见 §10.2）。 */
export type IdentitySource = 'injected' | 'local' | 'memory';

/** 存进 IndexedDB 的记录。**只有种子**，公钥每次现算（派生是确定性的，存了反而多一份泄露面）。 */
export interface StoredKeypair {
  seed: string;
  createdAt: string;
}

export interface LocalIdentity {
  /** 32 字节种子的 hex。**绝不外发**。 */
  seedHex: string;
  /** `seedHex` 的别名：`@travelmemory` 协议里私钥就叫 `privHex`（Ed25519 种子即私钥）。 */
  privHex: string;
  /** Ed25519 公钥 bare hex（64 字符）。 */
  pubHex: string;
  /** `ed25519:` + bare hex。可以给用户看 / 复制。 */
  pubkey: string;
  createdAt: string;
  /** true = 存进了 IndexedDB；false = 内存身份（含注入身份），刷新即变。 */
  persistent: boolean;
}

/** 模块级缓存：一次页面生命周期内身份不变（也避免每次渲染都开 DB）。 */
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

/** 生成 32 字节随机种子（hex，64 字符）。 */
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

/**
 * 种子 → Ed25519 公钥 bare hex。
 *
 * 与主应用 `frontend/src/services/crypto/keys.ts` 的 `publicKeyFromSeed` 同一写法：
 * `ed25519.getPublicKey(seedBytes)`。入参是**种子本身**（noble 内部做 clamp），
 * 不要预先哈希 —— 传错一层会导致同一个种子在两处派生出不同公钥。
 */
export function publicKeyFromSeed(seedHex: string): string {
  const bytes = hexToBytes(seedHex.trim());
  if (bytes.length !== 32) {
    throw new Error(`种子必须是 32 字节（64 hex），收到 ${bytes.length} 字节`);
  }
  return bytesToHex(ed25519.getPublicKey(bytes));
}

/**
 * 用 Ed25519 种子对任意字节签名，返回 hex。
 *
 * ## 为什么单独开一个函数而不是复用 `publicKeyFromSeed` 的调用点
 *
 * `publicKeyFromSeed` 只做派生；签名需要**种子本身**作为私钥传给 noble。
 * 这两件事的入参形状相同（64 hex），把签名塞进别处会让"哪个函数会碰私钥"
 * 变得不可审计 —— 本文件里碰种子的只有 `identityFromSeedRecord` / 这里 /
 * `secretHexValues`（后者只是把值交给外发兜底做比对）。
 *
 * ## 契约
 *
 * - `seedHex`：32 字节种子（64 hex），允许带 `ed25519:` 前缀 —— 主应用注入的
 *   `privHex` 与 IndexedDB 里存的都能直接喂进来；
 * - `message`：**待签名的原始字节**。调用方负责定好"字节从哪来"（本应用是
 *   `stableStringify` 出的 UTF-8），签名层不猜、不做 JSON 规范化 ——
 *   签名与验签两端只要用同一段字节就能对上；
 * - 返回 **bare hex**（128 字符，无前缀）：`ed25519:` 前缀是协议层的装饰，
 *   由封装者（`theme-dark/exportJourney.ts`）自己加。
 *
 * 种子长度不对时**抛错**（不是返回空签名）：调用方拿到空字符串会当成"签过了"，
 * 那是最坏的一种失败 —— 一个自称已签名、其实谁也验不过的文件。
 *
 * @param seedHex 32 字节种子的 hex
 * @param message 待签名字节
 * @returns 64 字节签名的 hex（128 字符）
 */
export function signWithSeed(seedHex: string, message: Uint8Array): string {
  const trimmed = seedHex.trim();
  const bare = trimmed.startsWith(PUBKEY_PREFIX) ? trimmed.slice(PUBKEY_PREFIX.length) : trimmed;
  const bytes = hexToBytes(bare);
  if (bytes.length !== 32) {
    throw new Error(`签名种子必须是 32 字节（64 hex），收到 ${bytes.length} 字节`);
  }
  return bytesToHex(ed25519.sign(message, bytes));
}

/** 展示用短公钥：`ed25519:` 之后取前 12 位。 */
export function shortPubkey(pubkey: string): string {
  const bare = pubkey.startsWith(PUBKEY_PREFIX) ? pubkey.slice(PUBKEY_PREFIX.length) : pubkey;
  return bare.slice(0, 12);
}

/** 把种子 + 创建时间组装成对外身份对象。 */
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

/** 剥掉可能存在的 `ed25519:` 前缀并转小写。 */
function normalizePrivHex(value: string): string {
  const trimmed = value.trim();
  const bare = trimmed.startsWith(PUBKEY_PREFIX) ? trimmed.slice(PUBKEY_PREFIX.length) : trimmed;
  return bare.toLowerCase();
}

/**
 * 接受主应用注入的身份。
 *
 * @param input `init.payload.identity`（`unknown`：那是另一个 origin 的另一份代码，不能假设形状）
 * @returns 校验通过返回 `true`；不合格返回 `false` 并**保持原状态不变**（绝不抛错 ——
 *          嵌入模式里抛错会白屏，而用户完全不知道发生了什么）
 *
 * 只认 `{ privHex: string, pubkey?: string }`。公钥一律由种子**现算**：
 * 种子才是真源，主应用给的 `pubkey` 只做一致性提示（不一致时 `console.warn`，仍以派生值为准）。
 *
 * ⚠️ **本函数不碰 IndexedDB**。注入身份只活在模块内存里，页面关闭即消失。
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

/**
 * 当前身份来源。
 *
 * 还没加载本机身份、也没注入时返回 `'local'`（"会走本机 IDB 路径"），
 * 这样 UI 在加载期间不会先闪一句"仅内存"再改口。
 */
export function identitySource(): IdentitySource {
  if (injected) return 'injected';
  if (cached) return cachedPersistent ? 'local' : 'memory';
  return 'local';
}

/** 当前身份（同步读）。注入优先；都没准备好时返回 `null`，调用方应 await `loadIdentity()`。 */
export function getIdentity(): LocalIdentity | null {
  return injected ?? cached;
}

/**
 * 当前进程**已知的种子值**（本机 + 注入）。给 `bridge.sendToParent` 做"外发兜底"用。
 *
 * 只返回内存里已有的值，不触发 IDB 读取 —— 它可能被每次发消息调用。
 */
export function secretHexValues(): string[] {
  const values: string[] = [];
  if (injected) values.push(injected.seedHex);
  if (cached) values.push(cached.seedHex);
  return values;
}

/** 打开（必要时建库）。失败 reject，由调用方退化成内存身份。 */
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

/** 读一条记录。 */
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
              createdAt: typeof record.createdAt === 'string' ? record.createdAt : new Date().toISOString(),
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

/** 写一条记录。 */
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

/** IndexedDB 不可用时的内存身份（刷新即变）。 */
function memoryIdentity(): LocalIdentity {
  const record: StoredKeypair = { seed: newSeedHex(), createdAt: new Date().toISOString() };
  return identityFromSeedRecord(record, false);
}

/**
 * 取（必要时创建）身份。**永不 reject**。
 *
 * 优先级：**注入身份 > 本机 IndexedDB 身份 > 内存身份**（§10.2）。
 * 注入身份不会写进 IDB —— 主应用的种子不该在另一个 origin 里留一份副本。
 *
 * @returns 身份对象；`persistent` 说明它是否真的落盘（注入与内存都是 `false`）
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

/** 清掉模块缓存（单测 / 用户点"换一个身份"时用）。注入身份不受影响。 */
export function resetIdentityCache(): void {
  cached = null;
  cachedPersistent = false;
}
