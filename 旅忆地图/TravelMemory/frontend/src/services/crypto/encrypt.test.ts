/** AES-GCM 与 X25519 KEM。 */

import { describe, expect, it } from 'vitest';

import {
  NOT_A_RECIPIENT,
  decryptBytes,
  encryptBytes,
  importAesKey,
  unwrapKey,
  wrapKeyForRecipients,
} from './encrypt';
import { ed25519ToX25519Priv, publicKeyFromSeed } from './keys';
import { identityA, SEED_B, SEED_C, SEED_D } from '../share/fixtures';
import { fromBase64, toBase64 } from '../../utils/base64';
import { bytesToHex, utf8 } from '../../utils/binary';
import { randomBytes } from '../../utils/id';

function aes() {
  return importAesKey(randomBytes(32));
}

/** 翻一个比特再编码回 base64（模拟传输中被改了一个字节）。 */
function flipOneBit(b64: string): string {
  const bytes = fromBase64(b64);
  bytes[0] ^= 0x01;
  return toBase64(bytes);
}

describe('AES-GCM', () => {
  it('加解密往返一致', async () => {
    const key = await aes();
    const plain = utf8('清水寺的清晨');
    const { ivB64, dataB64 } = await encryptBytes(key, plain);
    const back = await decryptBytes(key, ivB64, dataB64);
    expect(bytesToHex(back)).toBe(bytesToHex(plain));
  });

  it('篡改密文一个字节 → 解密失败（GCM tag 校验）', async () => {
    const key = await aes();
    const { ivB64, dataB64 } = await encryptBytes(key, utf8('photo-bytes'));
    await expect(decryptBytes(key, ivB64, flipOneBit(dataB64))).rejects.toThrow();
  });

  it('篡改 IV 一个字节 → 解密失败', async () => {
    const key = await aes();
    const { ivB64, dataB64 } = await encryptBytes(key, utf8('photo-bytes'));
    await expect(decryptBytes(key, flipOneBit(ivB64), dataB64)).rejects.toThrow();
  });

  it('同一明文加密两次结果不同（每次独立随机 IV）', async () => {
    const key = await aes();
    const one = await encryptBytes(key, utf8('same-plain-text'));
    const two = await encryptBytes(key, utf8('same-plain-text'));
    expect(one.dataB64).not.toBe(two.dataB64);
    expect(one.ivB64).not.toBe(two.ivB64);
  });

  it('IV 长度恒为 12 字节', async () => {
    const key = await aes();
    const { ivB64 } = await encryptBytes(key, utf8('x'));
    expect(fromBase64(ivB64).length).toBe(12);
  });
});

describe('KEM（把组密钥包给每个收件人）', () => {
  it('包给 2 个收件人，各自都能解开同一把组密钥', async () => {
    const groupKey = randomBytes(32);
    const wrapped = await wrapKeyForRecipients(
      groupKey,
      [publicKeyFromSeed(SEED_B), publicKeyFromSeed(SEED_C)],
      identityA.x25519Priv,
    );

    expect(wrapped.length).toBe(2);
    const keyB = await unwrapKey(wrapped[0], ed25519ToX25519Priv(SEED_B));
    const keyC = await unwrapKey(wrapped[1], ed25519ToX25519Priv(SEED_C));
    expect(bytesToHex(keyB)).toBe(bytesToHex(groupKey));
    expect(bytesToHex(keyC)).toBe(bytesToHex(groupKey));
  });

  it('第 3 个人解不开 → 抛 not-a-recipient', async () => {
    const groupKey = randomBytes(32);
    const wrapped = await wrapKeyForRecipients(
      groupKey,
      [publicKeyFromSeed(SEED_B)],
      identityA.x25519Priv,
    );
    await expect(unwrapKey(wrapped[0], ed25519ToX25519Priv(SEED_D))).rejects.toThrow(NOT_A_RECIPIENT);
  });

  it('recipientPubkey 统一写成 bare hex（两端前缀混用不会失配）', async () => {
    const pubB = publicKeyFromSeed(SEED_B);
    const wrapped = await wrapKeyForRecipients(randomBytes(32), [`ed25519:${pubB}`], identityA.x25519Priv);
    expect(wrapped[0].recipientPubkey).toBe(pubB);
    expect(wrapped[0].recipientPubkey.startsWith('ed25519:')).toBe(false);
  });

  it('临时公钥每次不同：同一个收件人包两次也换 ephemeralPubkey', async () => {
    const groupKey = randomBytes(32);
    const one = await wrapKeyForRecipients(groupKey, [publicKeyFromSeed(SEED_B)], identityA.x25519Priv);
    const two = await wrapKeyForRecipients(groupKey, [publicKeyFromSeed(SEED_B)], identityA.x25519Priv);
    expect(one[0].ephemeralPubkey).not.toBe(two[0].ephemeralPubkey);
    // 换了临时密钥仍然能被同一个人解开：证明 salt 是跟着 ephPub 重算的
    expect(bytesToHex(await unwrapKey(two[0], ed25519ToX25519Priv(SEED_B)))).toBe(bytesToHex(groupKey));
  });

  it('解封失败不区分"不是给你"与"数据坏了"：两者都是同一个错误串', async () => {
    const groupKey = randomBytes(32);
    const wrapped = await wrapKeyForRecipients(
      groupKey,
      [publicKeyFromSeed(SEED_B)],
      identityA.x25519Priv,
    );
    const broken = { ...wrapped[0], encryptedKey: flipOneBit(wrapped[0].encryptedKey) };
    await expect(unwrapKey(broken, ed25519ToX25519Priv(SEED_B))).rejects.toThrow(NOT_A_RECIPIENT);
  });
});
