/** canonical JSON 与 Ed25519 签名 / 验签。 */

import { describe, expect, it } from 'vitest';

import { bodyOf, canonicalJson, signCanonical, verifyCanonical } from './sign';
import { publicKeyFromSeed } from './keys';
import { identityA, identityB, identityD, SEED_A } from '../share/fixtures';
import type { SharePackage, SignedBody } from '../../types/share';

/** 一个足够复杂的 body：嵌套、数组、可选字段缺失。 */
function sampleBody(): SignedBody {
  return {
    version: 'ts-2026.4',
    shareId: 's_abcdef12',
    from: { displayName: 'A', pubkey: identityA.pubkey },
    groups: [
      {
        groupId: 'g1_ab12cd',
        payload: {
          cipher: 'aes-gcm',
          salt: 'c2FsdA==',
          places: [
            {
              placeId: 'p_1',
              tag: 'heritage',
              intensity: 'high',
              ivMeta: 'AAAA',
              encryptedMeta: 'BBBB',
              photos: [{ photoId: 'ph_1', encrypted: 'CCCC', iv: 'DDDD' }],
            },
          ],
        },
        wrappedKeys: [
          { recipientPubkey: identityB.pubHex, ephemeralPubkey: 'ee'.repeat(32), encryptedKey: 'EE', iv: 'FF' },
        ],
      },
    ],
    commitment: { hash: 'sha256:' + '0'.repeat(64), algo: 'sha256' },
    chain: { prevHash: '0'.repeat(64), seq: 0, anchor: null },
    createdAt: '2026-10-18T12:00:00.000Z',
  };
}

describe('canonicalJson', () => {
  it('key 顺序不同 → 序列化结果相同', () => {
    const a = canonicalJson({ b: 1, a: 2 });
    const b = canonicalJson({ a: 2, b: 1 });
    expect(a).toBe(b);
    expect(a).toBe('{"a":2,"b":1}');
  });

  it('嵌套对象也要递归按 key 排序', () => {
    const a = canonicalJson({ outer: { z: 1, y: { d: 4, c: 3 } }, a: 0 });
    const b = canonicalJson({ a: 0, outer: { y: { c: 3, d: 4 }, z: 1 } });
    expect(a).toBe(b);
  });

  it('数组保持顺序（照片顺序与时间线顺序都是语义）', () => {
    expect(canonicalJson([3, 1, 2])).toBe('[3,1,2]');
    expect(canonicalJson([3, 1, 2])).not.toBe(canonicalJson([1, 2, 3]));
  });

  it('过滤 undefined（不产出 "label":null 这种两端可能不一致的写法）', () => {
    expect(canonicalJson({ name: 'x', label: undefined })).toBe('{"name":"x"}');
    expect(canonicalJson({ a: { b: undefined, c: 1 } })).toBe('{"a":{"c":1}}');
  });

  it('字符串用 JSON.stringify 的转义规则', () => {
    expect(canonicalJson({ s: 'a"b\\c\n' })).toBe(JSON.stringify({ s: 'a"b\\c\n' }));
  });
});

describe('signCanonical / verifyCanonical', () => {
  it('签名后验签通过', () => {
    const body = sampleBody();
    const sig = signCanonical(body, SEED_A);
    expect(sig.startsWith('ed25519:')).toBe(true);
    expect(sig.length).toBe('ed25519:'.length + 128);
    expect(verifyCanonical(body, sig, identityA.pubHex)).toBe(true);
  });

  it('验签只认字节：换个构造顺序的等价对象仍然通过', () => {
    const body = sampleBody();
    const sig = signCanonical(body, SEED_A);
    const reordered = JSON.parse(canonicalJson({ ...body, groups: body.groups })) as SignedBody;
    expect(verifyCanonical(reordered, sig, identityA.pubHex)).toBe(true);
  });

  it('篡改 body 任意一个字节（改一个字符）→ 验签失败', () => {
    const body = sampleBody();
    const sig = signCanonical(body, SEED_A);
    const tampered = JSON.parse(canonicalJson(body)) as SignedBody;
    tampered.shareId = 's_abcdef13'; // 最后一个字符 2 → 3
    expect(verifyCanonical(tampered, sig, identityA.pubHex)).toBe(false);
  });

  it('错误的公钥验签失败', () => {
    const body = sampleBody();
    const sig = signCanonical(body, SEED_A);
    expect(verifyCanonical(body, sig, identityB.pubHex)).toBe(false);
    expect(verifyCanonical(body, sig, identityD.pubHex)).toBe(false);
    // 长度不对的公钥也不能蒙混过关
    expect(verifyCanonical(body, sig, 'deadbeef')).toBe(false);
  });

  it('签名长度不对直接判失败，不抛异常', () => {
    const body = sampleBody();
    expect(verifyCanonical(body, 'ed25519:00', identityA.pubHex)).toBe(false);
    expect(verifyCanonical(body, '', identityA.pubHex)).toBe(false);
  });
});

describe('bodyOf', () => {
  it('去掉 from.sig，其余字段原样保留（避免签名自引用）', () => {
    const pkg: SharePackage = { ...sampleBody(), from: { ...sampleBody().from, sig: 'ed25519:' + '0'.repeat(128) } };
    const body = bodyOf(pkg);
    expect('sig' in body.from).toBe(false);
    expect(body.groups).toBe(pkg.groups);
    expect(verifyCanonical(body, pkg.from.sig, publicKeyFromSeed(SEED_A))).toBe(false); // 假签名当然不过
  });
});
