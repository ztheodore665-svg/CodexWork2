/** 密钥派生测试：种子 → Ed25519 / X25519，以及 ECDH 的双方一致性。 */

import { describe, expect, it } from 'vitest';

import { bytesToHex } from '../../utils/binary';
import {
  deriveSharedSecret,
  ed25519PubToX25519Pub,
  ed25519ToX25519Priv,
  identityFromSeed,
  newSeed,
  normalizePubkey,
  publicKeyFromSeed,
  randomX25519Secret,
  withPrefix,
  x25519PublicFromPriv,
} from './keys';
import { SEED_A, SEED_B } from '../share/fixtures';

describe('keys：种子 → 公钥', () => {
  it('同一个种子必得同一个公钥（确定性）', () => {
    expect(publicKeyFromSeed(SEED_A)).toBe(publicKeyFromSeed(SEED_A));
  });

  it('不同种子得到不同公钥', () => {
    expect(publicKeyFromSeed(SEED_A)).not.toBe(publicKeyFromSeed(SEED_B));
  });

  it('newSeed 产出 64 个 hex 字符，且两次不重复', () => {
    const a = newSeed();
    const b = newSeed();
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(a).not.toBe(b);
  });

  it('公钥前缀：剥了再加必须回到原值（防止两端混用 bare hex 与 ed25519: 前缀）', () => {
    const bare = publicKeyFromSeed(SEED_A);
    expect(normalizePubkey(withPrefix(bare))).toBe(bare);
    expect(normalizePubkey(bare)).toBe(bare);
  });
});

describe('keys：Ed25519 → X25519 派生', () => {
  it('种子派生的 X25519 私钥，推出的公钥 = Ed25519 公钥做 Montgomery 变换', () => {
    // 这条是"只拿到对方 Ed25519 公钥也能包密钥"的前提：
    // 两条路径必须落在同一个 X25519 公钥上，否则收件人永远解不开。
    const xPriv = ed25519ToX25519Priv(SEED_A);
    const fromPriv = x25519PublicFromPriv(xPriv);
    const fromPub = ed25519PubToX25519Pub(publicKeyFromSeed(SEED_A));
    expect(bytesToHex(fromPriv)).toBe(bytesToHex(fromPub));
  });

  it('派生是确定性的：同一种子两次派生逐字节相同', () => {
    expect(bytesToHex(ed25519ToX25519Priv(SEED_A))).toBe(bytesToHex(ed25519ToX25519Priv(SEED_A)));
  });

  it('ECDH 双方共享秘密一致（A 的私钥 × B 的公钥 == B 的私钥 × A 的公钥）', () => {
    const aPriv = ed25519ToX25519Priv(SEED_A);
    const bPriv = ed25519ToX25519Priv(SEED_B);
    const aPub = x25519PublicFromPriv(aPriv);
    const bPub = x25519PublicFromPriv(bPriv);

    const fromA = deriveSharedSecret(aPriv, bPub);
    const fromB = deriveSharedSecret(bPriv, aPub);

    expect(bytesToHex(fromA)).toBe(bytesToHex(fromB));
    expect(fromA.length).toBe(32);
  });

  it('不同双方算出不同的共享秘密', () => {
    const aPriv = ed25519ToX25519Priv(SEED_A);
    const cPub = ed25519PubToX25519Pub(publicKeyFromSeed('c'.repeat(64)));
    const bPub = x25519PublicFromPriv(ed25519ToX25519Priv(SEED_B));
    expect(bytesToHex(deriveSharedSecret(aPriv, bPub))).not.toBe(
      bytesToHex(deriveSharedSecret(aPriv, cPub)),
    );
  });

  it('临时私钥每次都是新的（不复用是 KEM 的硬要求）', () => {
    const one = randomX25519Secret();
    const two = randomX25519Secret();
    expect(bytesToHex(one)).not.toBe(bytesToHex(two));
  });
});

describe('keys：identityFromSeed', () => {
  it('一份身份里的两套密钥互相自洽', () => {
    const id = identityFromSeed(SEED_A);
    expect(id.pubHex).toBe(publicKeyFromSeed(SEED_A));
    expect(id.pubkey).toBe(`ed25519:${id.pubHex}`);
    expect(id.x25519Priv.length).toBe(32);
    expect(bytesToHex(id.x25519Pub)).toBe(bytesToHex(x25519PublicFromPriv(id.x25519Priv)));
  });
});
