/** AES-GCM 内容加密 + X25519/HKDF KEM（把组密钥定向发给每个收件人）。
 *
 * ## 为什么组密钥要"包"给每个人，而不是每人一套密文
 *
 * 内容是**一组一份**密钥（同一组里所有地点/照片共用），照片字节只加密一次；
 * 然后把这把 32 字节的组密钥分别包给每个收件人（`wrappedKeys`）。
 * 若改成"每个收件人一套完整密文"，包体积会随收件人数线性膨胀，
 * 而且同一个内容被加密 N 次会让"两组内容是否相同"这种侧信道更容易被观察。
 *
 * ## 三条必须照抄的细节（规范 §5.3 / §5.4 / §13）
 *
 * 1. **每次加密随机 12 字节 IV，绝不复用**。AES-GCM 下 (key, iv) 复用一次
 *    就等于把两条明文的异或白送给攻击者；所以连配文也各自独立取 IV。
 * 2. **临时密钥每次新生成**。复用 ephPriv 会让两个收件人的共享秘密只差在公钥上，
 *    等价于同一把 wrapKey 用两次 —— 与 IV 复用是同一类错误。
 * 3. **解封失败只报 `not-a-recipient`**，不区分"不是给你的"和"数据坏了"。
 *    区分就等于免费告诉攻击者"你猜对了，这个包确实发给过某人"。
 */

import { hkdf } from '@noble/hashes/hkdf';
import { sha256 } from '@noble/hashes/sha256';

import type { WrappedKey } from '../../types/share';
import { bytesToHex, concatBytes, hexToBytes, toArrayBuffer, toBytes, utf8 } from '../../utils/binary';
import { fromBase64, toBase64 } from '../../utils/base64';
import { sha256Bytes } from '../../utils/hash';
import {
  deriveSharedSecret,
  ed25519PubToX25519Pub,
  normalizePubkey,
  randomX25519Secret,
  x25519PublicFromPriv,
} from './keys';

/** 解封失败的唯一错误串。
 *
 * 刻意**不区分**"你不是收件人"与"密文损坏"：区分会让试探者拿到一条额外信息位。 */
export const NOT_A_RECIPIENT = 'not-a-recipient';

/** AES-GCM 的 IV 长度（12 字节 = 96 位，GCM 的标准/推荐值）。 */
export const IV_BYTES = 12;

/** KEM 派生 wrapKey 的 HKDF info。改它等于改协议版本，会让老包全部解不开。 */
const KEM_INFO = 'trailshare-kem-v1';

/** 派生出的 wrapKey 长度（= AES-256 密钥长度）。 */
const WRAP_KEY_BYTES = 32;

/** 导入一把 AES-256-GCM 密钥。`extractable: false`：密钥不该被导出到包外。 */
export async function importAesKey(rawBytes: Uint8Array): Promise<CryptoKey> {
  return globalThis.crypto.subtle.importKey(
    'raw',
    toArrayBuffer(rawBytes),
    { name: 'AES-GCM' },
    false,
    ['encrypt', 'decrypt'],
  );
}

/** 随机 12 字节 IV。 */
function newIv(): Uint8Array {
  const iv = new Uint8Array(IV_BYTES);
  globalThis.crypto.getRandomValues(iv);
  return iv;
}

/** AES-GCM 加密，返回 base64 的 `{ iv, data }`。
 *
 * 密文里已含 16 字节 GCM tag：`decrypt` 时会自动校验，改一字节即抛。 */
export async function encryptBytes(
  key: CryptoKey,
  data: Uint8Array,
): Promise<{ ivB64: string; dataB64: string }> {
  const iv = newIv();
  const cipher = await globalThis.crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: toArrayBuffer(iv) },
    key,
    toArrayBuffer(data),
  );
  return { ivB64: toBase64(iv), dataB64: toBase64(new Uint8Array(cipher)) };
}

/** AES-GCM 解密。密文/IV 被改动一个字节都会抛（GCM tag 校验失败）。 */
export async function decryptBytes(key: CryptoKey, ivB64: string, dataB64: string): Promise<Uint8Array> {
  const plain = await globalThis.crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: toArrayBuffer(fromBase64(ivB64)) },
    key,
    toArrayBuffer(fromBase64(dataB64)),
  );
  return new Uint8Array(plain);
}

/**
 * 把组密钥包给每个收件人。
 *
 * ```
 * ephPriv = x25519.randomSecretKey()            // 每次新生成
 * ephPub  = x25519.getPublicKey(ephPriv)
 * shared  = X25519(ephPriv, recipientX25519Pub)
 * salt    = sha256(ephPub || recipientX25519Pub)
 * wrapKey = HKDF-SHA256(ikm=shared, salt=salt, info='trailshare-kem-v1', L=32)
 * { encryptedKey, iv } = AES-GCM(wrapKey, groupKey)
 * ```
 *
 * @param groupKey      32 字节组密钥（明文）
 * @param recipientPubkeys 收件人的 **Ed25519** 公钥（bare hex 或 `ed25519:` 前缀）
 * @param _senderX25519Priv 保留形参以对齐规范 §6.1 的调用签名；本协议的 KEM 是
 *                      **纯临时密钥**构造（不绑定发送方长期私钥），所以实现里不使用它。
 *                      这样设计的好处是 wrappedKey 不泄露"发送方是谁"。
 */
export async function wrapKeyForRecipients(
  groupKey: Uint8Array,
  recipientPubkeys: string[],
  _senderX25519Priv: Uint8Array,
): Promise<WrappedKey[]> {
  const out: WrappedKey[] = [];
  for (const rawPub of recipientPubkeys) {
    const recipientEdPub = normalizePubkey(rawPub);
    const recipientXPub = ed25519PubToX25519Pub(recipientEdPub);

    const ephPriv = randomX25519Secret(); // 每个收件人一条新的临时密钥
    const ephPub = x25519PublicFromPriv(ephPriv);
    const shared = deriveSharedSecret(ephPriv, recipientXPub);

    // salt 绑定"临时公钥 + 收件人公钥"：换掉任一方都会得到不同的 wrapKey
    const salt = await sha256Bytes(concatBytes(ephPub, recipientXPub));
    const wrapKeyBytes = hkdf(sha256, shared, salt, utf8(KEM_INFO), WRAP_KEY_BYTES);
    const aes = await importAesKey(wrapKeyBytes);
    const { ivB64, dataB64 } = await encryptBytes(aes, groupKey);

    out.push({
      recipientPubkey: recipientEdPub, // 统一存 bare hex
      ephemeralPubkey: bytesToHex(ephPub),
      encryptedKey: dataB64,
      iv: ivB64,
    });
  }
  return out;
}

/** 用自己的 X25519 私钥解出组密钥。解不开就抛 `not-a-recipient`。
 *
 * 复算路径必须与封装路径严格对称：`salt` 里的 `recipientX25519Pub` 由**自己的私钥**
 * 重新推出，所以解封方只需要一把种子。 */
export async function unwrapKey(wrapped: WrappedKey, myX25519Priv: Uint8Array): Promise<Uint8Array> {
  const myXPub = x25519PublicFromPriv(myX25519Priv);
  try {
    const ephPub = hexToBytes(wrapped.ephemeralPubkey);
    const shared = deriveSharedSecret(myX25519Priv, ephPub);
    const salt = await sha256Bytes(concatBytes(ephPub, myXPub));
    const wrapKeyBytes = hkdf(sha256, shared, salt, utf8(KEM_INFO), WRAP_KEY_BYTES);
    const aes = await importAesKey(wrapKeyBytes);
    return await decryptBytes(aes, wrapped.iv, wrapped.encryptedKey);
  } catch {
    // 吞掉原始异常：GCM tag 失败、HKDF 失败、格式错误一律同一个结论
    throw new Error(NOT_A_RECIPIENT);
  }
}

/** 把 `ArrayBuffer`（照片字节）转成 `Uint8Array` 再送去加密 —— 保留本文件的唯一出口，
 * 免得调用方自己 `new Uint8Array` 时忘了 byteOffset。 */
export function bytesForEncrypt(buffer: ArrayBuffer): Uint8Array {
  return toBytes(buffer);
}
