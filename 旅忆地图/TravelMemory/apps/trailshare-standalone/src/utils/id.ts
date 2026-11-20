/** 短 id 生成：`s_xxxxxxxx` / `g1_xxxxxx` / `p_xxxxxxxx`。
 *
 * ## 为什么不用 `crypto.randomUUID()`
 *
 * 一是它给出 36 个字符，塞进 `groupId` 会让包体积和日志都变吵；
 * 二是协议里 id 的**长度本身就是可辨识特征**（`s_` 8 位 = shareId，
 * `g{n}_` 6 位 = groupId），随机 UUID 会把这个约定洗掉。
 *
 * ## 为什么不用 `Math.random()`
 *
 * shareId / groupId 会被拿去算承诺与哈希链，也会写进溯源边 id 做判重。
 * `Math.random()` 不可预测性不足，且在某些老运行时里空间很小，碰撞会让
 * "重复导入同一个文件"被判成两条不同来源。这里统一走 CSPRNG。
 */

import { bytesToHex } from './binary';

/** 用 CSPRNG 取 `bytes` 个随机字节。 */
export function randomBytes(bytes: number): Uint8Array {
  const out = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(out);
  return out;
}

/** 生成 `${prefix}_${hex}`，其中 hex 长度为 `len`（= 2 × 随机字节数，向上取整）。 */
export function newId(prefix: string, len: number): string {
  const byteLen = Math.ceil(len / 2);
  return `${prefix}_${bytesToHex(randomBytes(byteLen)).slice(0, len)}`;
}
