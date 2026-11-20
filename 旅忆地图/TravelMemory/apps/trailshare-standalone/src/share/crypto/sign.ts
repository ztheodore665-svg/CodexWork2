/** Ed25519 签名：规范化 JSON → 字节 → 签名 / 验签。
 *
 * ## 为什么签名对象是"规范化 JSON 字节"而不是对象
 *
 * 信物是一个 JSON 文件，会被 `JSON.stringify` → 传输 → `JSON.parse` 走一圈。
 * 如果直接对 JS 对象做签名，就必须要求两端序列化出的**字节**完全一致；
 * 而 `JSON.stringify` 的 key 顺序取决于对象是怎么被构造出来的（插入顺序），
 * 这在两台机器上没有保证。于是签名值会"随机"验不过 —— 而且只在特定路径下复现。
 *
 * 解决：签名前先把对象过一遍 `canonicalJson`（递归按 key 字典序、过滤 undefined、
 * 数组保序），对**这个字符串**签名。验签方同样先规范化再验。
 *
 * ## 为什么 `SignedBody` 里没有 `from.sig`
 *
 * 签名值不可能把自己签进去。所以被签名的是"包去掉 sig"，解包侧用 `bodyOf(pkg)` 复算。
 * 手写一遍漏掉 `sig` 字段，验签会 100% 失败。
 */

import { ed25519 } from '@noble/curves/ed25519.js';
import type { SharePackage, SignedBody } from '../../types/share';
import { bytesToHex, hexToBytes, utf8 } from '../../utils/binary';

/** 签名前缀。存进 `from.sig` 与溯源边里，便于人/日志一眼区分算法。 */
export const SIG_PREFIX = 'ed25519:';

/** `ed25519:` + 128 hex（64 字节签名）。 */
export const SIG_LENGTH = 64;

/**
 * 规范化 JSON：递归按 key 字典序、数组保持顺序、过滤 `undefined`、字符串用 `JSON.stringify`。
 *
 * 三条容易被改坏的规则：
 *  1. **对象按 key 排序** —— 否则两端字节不同（见模块 docstring）；
 *  2. **数组不排序** —— 照片顺序、组顺序都是语义，排序会毁掉时间线；
 *  3. **过滤 `undefined`** —— `{ label: undefined }` 与 `{}` 在 `JSON.stringify` 下
 *     一个产出 `{}` 一个产出 `{}`，但**手写序列化**可能产出 `{"label":null}`；
 *     统一在规范化这一步就丢掉，避免歧义。
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') {
    // number / string / boolean / null：交给 JSON.stringify 处理转义与数字格式
    return value;
  }
  if (Array.isArray(value)) {
    // 数组保序：只递归，不重排，也不丢元素（丢元素会让下标语义漂移）
    return value.map((item) => canonicalize(item));
  }
  const record = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(record).sort()) {
    const item = record[key];
    if (item === undefined) continue; // 过滤 undefined：不写进序列化结果
    out[key] = canonicalize(item);
  }
  return out;
}

/** 规范化后取 UTF-8 字节 —— 真正被签名的东西。 */
export function canonicalBytes(value: unknown): Uint8Array {
  return utf8(canonicalJson(value));
}

/** 对任意可序列化对象签名，返回 `ed25519:<128hex>`。
 *
 * `privHex` 是 32 字节**种子**（bare hex 或带 `ed25519:` 前缀都行）。 */
export function signCanonical(body: unknown, privHex: string): string {
  const seed = hexToBytes(stripPrefix(privHex));
  return SIG_PREFIX + bytesToHex(ed25519.sign(canonicalBytes(body), seed));
}

/** 验签。任何前缀/长度错误都按"验不过"返回 `false`，而不是抛：
 * 验签是**判定**，调用方要的是布尔值；异常留给版本闸门与结构校验。 */
export function verifyCanonical(body: unknown, sig: string, pubHex: string): boolean {
  try {
    const sigBytes = hexToBytes(stripPrefix(sig));
    // 长度必须 64 字节：短签名会让 noble 抛错，长签名则可能只是格式不对
    if (sigBytes.length !== SIG_LENGTH) return false;
    const pubBytes = hexToBytes(stripPrefix(pubHex));
    return ed25519.verify(sigBytes, canonicalBytes(body), pubBytes);
  } catch {
    return false;
  }
}

/** `pkg` → `SignedBody`：只去掉 `from.sig`，其余字段原样保留。 */
export function bodyOf(pkg: SharePackage): SignedBody {
  return {
    version: pkg.version,
    shareId: pkg.shareId,
    from: { displayName: pkg.from.displayName, pubkey: pkg.from.pubkey },
    groups: pkg.groups,
    commitment: pkg.commitment,
    chain: pkg.chain,
    createdAt: pkg.createdAt,
  };
}

function stripPrefix(value: string): string {
  const trimmed = value.trim();
  return trimmed.startsWith(SIG_PREFIX) ? trimmed.slice(SIG_PREFIX.length) : trimmed;
}
