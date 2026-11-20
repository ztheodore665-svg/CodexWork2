/** 本机身份：一份种子，两套密钥（Ed25519 签名 + X25519 KEM）。
 *
 * ## 私钥红线（PROJECT.md §二，阶段 6 新增约束）
 *
 * **解密私钥永远只在用户设备本地生成，绝不上传服务端。**
 * 本模块是这个红线的唯一落点：种子只写 `localStorage`，没有任何网络调用。
 * 换设备 = 换身份（种子不导出），这一点与 TrailShare 的已知局限一致 ——
 * 生产环境的种子备份（助记词 / 二维码）列为 Future Work。
 *
 * ## 为什么 localStorage 而不是 IndexedDB
 *
 * 种子只有 64 个 hex 字符，`localStorage` 同步 API 足够，也不用引 idb 依赖。
 * 隐私模式下 `localStorage` 可能抛异常 —— 所有读写都 try/catch，
 * 失败时退化成"内存身份"（刷新丢失，但功能不中断）。
 */

import { identityFromSeed, newSeed } from '../../services/crypto/keys';
import type { Identity } from '../../types/share';

const STORAGE_KEY = 'travelmemory.identity.v1';

/** 测试注入点：替换持久化实现（默认 localStorage）。 */
let storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null = null;

function defaultStorage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    // 隐私模式下 getItem 本身就可能抛 —— 探一次比每次 try 便宜
    localStorage.getItem(STORAGE_KEY);
    return localStorage;
  } catch {
    return null;
  }
}

/** 注入自定义存储（单测用；传 `null` 回到默认）。 */
export function setIdentityStorage(next: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null): void {
  storage = next;
}

/** 生成一个全新身份（32 字节随机种子）。 */
export function createIdentity(): Identity {
  return identityFromSeed(newSeed());
}

/** 读取已存身份；没有则创建并落库。**永不触网**。 */
export function loadOrCreateIdentity(): Identity {
  const store = storage ?? defaultStorage();
  if (store) {
    try {
      const raw = store.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { privHex?: unknown };
        if (typeof parsed.privHex === 'string' && parsed.privHex.length === 64) {
          return identityFromSeed(parsed.privHex);
        }
      }
    } catch {
      // 存储损坏：当没有处理，重新生成（下面会覆盖写）
    }
  }
  const identity = createIdentity();
  saveIdentity(identity);
  return identity;
}

/** 覆盖写身份。存储不可用（隐私模式）时静默跳过 —— 功能不中断。 */
export function saveIdentity(identity: Identity): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify({ privHex: identity.privHex, pubHex: identity.pubHex }));
  } catch {
    // 配额满 / 隐私模式：忽略。刷新后会换身份，已在模块 docstring 声明。
  }
}

/** 清除本机身份（"重新开始"用）。 */
export function clearIdentity(): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.removeItem(STORAGE_KEY);
  } catch {
    // 同上
  }
}
