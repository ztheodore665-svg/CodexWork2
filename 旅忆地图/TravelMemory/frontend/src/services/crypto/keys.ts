/** 身份与密钥派生：一份 32 字节种子 → Ed25519（签名）+ X25519（KEM）两套密钥。
 *
 * ## 为什么一份种子要养两套密钥
 *
 * 信物需要两件不同的事：
 *   - **可验证**：收件人要能证明"这包确实是 A 发的" → Ed25519 签名；
 *   - **可定向**：只有指定的人能解开内容 → X25519 KEM。
 * 让用户备份两把私钥是不可接受的（丢一把就全废），所以两套密钥都从同一个种子派生。
 * 代价是**一个种子泄露等于两套全泄露**，这一点在密钥导出 UI 上必须讲清楚。
 *
 * ## 三个必须照抄的细节（规范 §13）
 *
 * 1. `ed25519.utils.toMontgomerySecret(seed)` 的入参是**种子**（32 字节），
 *    不是 `SHA512(seed)` 也不是私钥哈希 —— noble 内部已经做了 clamp。
 *    传错一层会让 A 派生的 X25519 私钥与 B 派生的公钥算不出同一个共享秘密，
 *    症状是"自己发的包自己也解不开"。
 * 2. 公钥对外一律 **bare hex**（64 小写 hex）或 **`ed25519:` + bare hex**。
 *    `normalizePubkey` 统一剥前缀，`withPrefix` 统一加前缀；
 *    两端混用会让 `recipientPubkey` 对不上，且**报错是"解不开"而不是"找不到"**，极难查。
 * 3. 临时密钥（KEM 的 ephPriv）**每次新生成**，绝不复用 —— 见 `encrypt.ts`。
 */

import { ed25519, x25519 } from '@noble/curves/ed25519.js';

import type { Identity } from '../../types/share';
import { bytesToHex, hexToBytes } from '../../utils/binary';
import { randomBytes } from '../../utils/id';

/** 公钥前缀。好友之间手工交换公钥时带上它，人一眼能看出"这是 Ed25519"。 */
export const PUBKEY_PREFIX = 'ed25519:';

/** 种子 / 公钥 / 签名 / 承诺的 hex 长度（64 个 hex 字符 = 32 字节）。 */
export const KEY_HEX_LEN = 64;

/** 新生成一份 32 字节种子（hex）。种子 = Ed25519 私钥，备份它就备份了全部身份。 */
export function newSeed(): string {
  return bytesToHex(randomBytes(32));
}

/** 剥掉 `ed25519:` 前缀，得到 bare hex。带前缀或裸的都接受。 */
export function normalizePubkey(pub: string): string {
  const trimmed = pub.trim();
  return trimmed.startsWith(PUBKEY_PREFIX) ? trimmed.slice(PUBKEY_PREFIX.length) : trimmed;
}

/** 加上 `ed25519:` 前缀（已经是的话不重复加）。 */
export function withPrefix(pub: string): string {
  const bare = normalizePubkey(pub);
  return `${PUBKEY_PREFIX}${bare}`;
}

/** 种子（hex 或字节）→ Ed25519 公钥 bare hex。同种子必得同公钥（确定性）。 */
export function publicKeyFromSeed(privHex: string): string {
  return bytesToHex(ed25519.getPublicKey(seedBytes(privHex)));
}

/** Ed25519 种子 → X25519 私钥。
 *
 * 入参是**种子**：noble 的 `toMontgomerySecret` 内部做 `clamp(SHA512(seed)[0..32])`。
 * 这里不要预先哈希，否则 clamp 会作用在错误的数据上。 */
export function ed25519ToX25519Priv(seed: string | Uint8Array): Uint8Array {
  return ed25519.utils.toMontgomerySecret(typeof seed === 'string' ? seedBytes(seed) : seed);
}

/** Ed25519 公钥（bare hex / `ed25519:` 前缀 / 字节）→ X25519 公钥字节。
 *
 * 只有在拿到对方 Ed25519 公钥（而不是 X25519 公钥）时才需要这一步 ——
 * 好友通讯录里存的是 Ed25519 公钥，所以打包时必须走它。 */
export function ed25519PubToX25519Pub(pub: string | Uint8Array): Uint8Array {
  const bytes = typeof pub === 'string' ? hexToBytes(normalizePubkey(pub)) : pub;
  return ed25519.utils.toMontgomery(bytes);
}

/** X25519 私钥 → X25519 公钥字节（写进 `Identity`，也用于自测对端一致性）。 */
export function x25519PublicFromPriv(xPriv: Uint8Array): Uint8Array {
  return x25519.getPublicKey(xPriv);
}

/** ECDH：`我的 X25519 私钥 × 对方 X25519 公钥`。
 *
 * 双方算出的结果必须逐字节相同 —— `crypto.test.ts` 里的
 * "ECDH 双方共享秘密一致"就是钉住这条不变量。 */
export function deriveSharedSecret(myX25519Priv: Uint8Array, theirX25519Pub: Uint8Array): Uint8Array {
  return x25519.getSharedSecret(myX25519Priv, theirX25519Pub);
}

/** KEM 用的临时私钥。**每次调用都必须是新的** —— 复用会让两个收件人的
 * 共享秘密只差在公钥上，等价于把同一把 wrapKey 用两次。 */
export function randomX25519Secret(): Uint8Array {
  return x25519.utils.randomSecretKey();
}

/** 种子 → 完整 `Identity`（签名私钥 + 公钥 + X25519 两套）。 */
export function identityFromSeed(seed: string): Identity {
  const privHex = normalizePubkey(seed);
  const pubHex = publicKeyFromSeed(privHex);
  const x25519Priv = ed25519ToX25519Priv(privHex);
  return {
    privHex,
    pubHex,
    pubkey: withPrefix(pubHex),
    x25519Priv,
    x25519Pub: x25519PublicFromPriv(x25519Priv),
  };
}

/** 只给解包用的最小凭证：拿着自己的种子现算 X25519 私钥。 */
export function x25519PrivFromSeed(seed: string): Uint8Array {
  return ed25519ToX25519Priv(normalizePubkey(seed));
}

function seedBytes(seed: string): Uint8Array {
  const bytes = hexToBytes(normalizePubkey(seed));
  if (bytes.length !== 32) {
    throw new Error(`种子必须是 32 字节（64 hex），收到 ${bytes.length} 字节`);
  }
  return bytes;
}
