/** `journey.json` 的签名封装（W14-3b）。
 *
 * ## 文件长什么样
 *
 * ```json
 * { "version": "journey-json-1",
 *   "bundle": { … },
 *   "signerPubkey": "ed25519:<64 hex>",
 *   "signature": "<128 hex>" }
 * ```
 *
 * ## 为什么先"稳定序列化"再签
 *
 * 签名是对**字节**做的，而这个文件要经 `JSON.stringify` → 落盘 → 别人 `JSON.parse`
 * 走一圈。JS 对象 key 的顺序取决于构造顺序（两台机器不保证一致），
 * 直接对 `JSON.stringify(obj)` 的产物签名会出现"本机能验、换台机器验不过"的幽灵失败。
 * 所以这里自己写一遍 `stableStringify`：递归按 key 字典序、数组保序、丢掉 `undefined`。
 * 验签方必须用**同一个函数**重算字节 —— 这也是为什么 `verifyJourneyFile` 与
 * `signJourneyPayload` 挨着放，它们共享同一份序列化。
 *
 * ## 签的是什么
 *
 * 签 `{ version, bundle, signerPubkey }` 三个字段的稳定形式，**不含 `signature` 自己**
 * （签名不可能把自己签进去）。把 `version` 也签进去是刻意的：否则别人可以
 * 把 `journey-json-1` 改成一个未来版本号，让解析方走"新格式"分支。
 *
 * ## 边界
 *
 * 纯函数 + 纯本地：不碰网络、不碰存储、不碰 DOM（下载是调用方的事，
 * 见 `core/journeyBuild` 的 `downloadBlob`）。种子只在内存里用一次，绝不进文件。
 */

import { ed25519 } from '@noble/curves/ed25519.js';
import type { JourneyBundle } from '@travelmemory/journey-transfer';
import type { Photo } from '@travelmemory/trail';
import type { SystemPhoto } from '@travelmemory/photo-source';

import {
  PUBKEY_PREFIX,
  hexToBytes,
  publicKeyFromSeed,
  signWithSeed,
} from '../core/identity';

/** 文件格式版本。改动**结构**（不是 bundle 内容）时必须升这个值。 */
export const JOURNEY_JSON_VERSION = 'journey-json-1';

/** 落盘的 `journey.json`。 */
export interface SignedJourneyFile {
  version: typeof JOURNEY_JSON_VERSION;
  bundle: JourneyBundle;
  /** `ed25519:` + 64 hex。可以给用户看，用于确认"是谁签的"。 */
  signerPubkey: string;
  /** 128 hex（64 字节签名），**不带前缀** —— 与文件格式示例一致。 */
  signature: string;
}

/**
 * 稳定 JSON：递归按 key 字典序、数组保序、过滤 `undefined`。
 *
 * 三条规则各有代价，别随手简化：
 *  1. 对象按 key 排序 —— 不排序就等于把"构造顺序"签进哈希里；
 *  2. 数组不排序 —— 照片顺序、天顺序都是语义；
 *  3. 过滤 `undefined` —— `{a: undefined}` 与 `{}` 在 `JSON.stringify` 下都变 `{}`，
 *     但手写序列化可能产出 `{"a":null}`，先在规范化这层统一丢掉就没有歧义。
 */
export function stableStringify(value: unknown): string {
  return JSON.stringify(stabilize(value));
}

function stabilize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map((item) => stabilize(item));
  const record = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(record).sort()) {
    const item = record[key];
    if (item === undefined) continue;
    out[key] = stabilize(item);
  }
  return out;
}

/** 稳定 JSON → UTF-8 字节（真正被签名/验签的东西）。 */
export function stableBytes(value: unknown): Uint8Array {
  return new TextEncoder().encode(stableStringify(value));
}

/** 被签名的载荷：除 `signature` 之外的全部字段。 */
function payloadOf(file: Omit<SignedJourneyFile, 'signature'>): {
  version: string;
  bundle: JourneyBundle;
  signerPubkey: string;
} {
  return { version: file.version, bundle: file.bundle, signerPubkey: file.signerPubkey };
}

/**
 * 对 `bundle` 签名并组装成可落盘的文件。
 *
 * @param bundle `core/journeyBuild` 产出的包（**不上传，只落盘**）
 * @param seedHex 32 字节种子（64 hex，可带 `ed25519:` 前缀）
 * @returns 字段顺序固定的对象；序列化请用 `journeyFileJson`
 */
export function signJourneyFile(bundle: JourneyBundle, seedHex: string): SignedJourneyFile {
  const pubHex = publicKeyFromSeed(seedHex);
  const signerPubkey = `${PUBKEY_PREFIX}${pubHex}`;
  const signature = signWithSeed(seedHex, stableBytes(payloadOf({
    version: JOURNEY_JSON_VERSION,
    bundle,
    signerPubkey,
  })));
  return { version: JOURNEY_JSON_VERSION, bundle, signerPubkey, signature };
}

/**
 * 验签（本阶段只有单测用得到，但它是"这份文件到底能不能被信任"的唯一判据，
 * 所以跟签名放在一起，避免两边各写一份序列化逻辑）。
 *
 * 任何结构/长度错误都返回 `false` 而不抛：验签是**判定**，不是解析。
 */
export function verifyJourneyFile(input: unknown): boolean {
  if (!input || typeof input !== 'object') return false;
  const file = input as Partial<SignedJourneyFile>;
  if (file.version !== JOURNEY_JSON_VERSION) return false;
  if (typeof file.signerPubkey !== 'string' || typeof file.signature !== 'string') return false;
  if (!file.bundle || typeof file.bundle !== 'object') return false;

  try {
    const barePub = file.signerPubkey.startsWith(PUBKEY_PREFIX)
      ? file.signerPubkey.slice(PUBKEY_PREFIX.length)
      : file.signerPubkey;
    const pubBytes = hexToBytes(barePub);
    const sigBytes = hexToBytes(file.signature);
    if (pubBytes.length !== 32 || sigBytes.length !== 64) return false;
    const bytes = stableBytes(
      payloadOf({
        version: JOURNEY_JSON_VERSION,
        bundle: file.bundle,
        signerPubkey: file.signerPubkey,
      }),
    );
    return ed25519.verify(sigBytes, bytes, pubBytes);
  } catch {
    return false;
  }
}

/** 文件 → 落盘文本（缩进 2 空格；字段顺序 = 接口声明顺序，人眼可读）。 */
export function journeyFileJson(file: SignedJourneyFile): string {
  const ordered: SignedJourneyFile = {
    version: file.version,
    bundle: file.bundle,
    signerPubkey: file.signerPubkey,
    signature: file.signature,
  };
  return JSON.stringify(ordered, null, 2);
}

/** 签名短码：给 UI 一行提示用（前 8 位）。 */
export function shortSignature(signature: string): string {
  return signature.slice(0, 8);
}

/**
 * 演示线（`@travelmemory/trail` 的 `Photo[]`）→ `SystemPhoto[]`，只为了让
 * `core/journeyBuild` 的 `buildJourneyBundle` 能吃到同一份输入类型。
 *
 * `bytes` 填一个空 buffer：`bundleFromTrip` **只白名单缩略图**，原图字节永远不进
 * `journey.json`（这是那个包的硬约束，不是这里的约定）。所以给空值不会泄漏任何东西，
 * 也不会让导出结果不同 —— 但调用方必须知道它不是一个"能再解码成图片"的照片对象。
 */
export function demoPhotosForExport(photos: readonly Photo[]): SystemPhoto[] {
  return photos.map((photo) => ({
    id: photo.id,
    bytes: new ArrayBuffer(0),
    name: `${photo.placeName ?? photo.id}.jpg`,
    takenAt: new Date(photo.takenAt).toISOString(),
    lat: typeof photo.lat === 'number' ? photo.lat : undefined,
    lng: typeof photo.lng === 'number' ? photo.lng : undefined,
    thumbnailDataUrl: photo.thumb ?? '',
  }));
}
