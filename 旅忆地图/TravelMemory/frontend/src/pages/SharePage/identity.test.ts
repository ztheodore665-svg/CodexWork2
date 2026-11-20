/** 身份模块单测。
 *
 * 重点：**永不触网**（私钥红线的可执行部分）与"存储坏了不炸"。
 * 假的 storage 用内存 Map 实现，不依赖任何浏览器 API。
 */

import { afterEach, describe, expect, it } from 'vitest';
import { clearIdentity, createIdentity, loadOrCreateIdentity, saveIdentity, setIdentityStorage } from './identity';

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    _map: map,
  };
}

afterEach(() => {
  setIdentityStorage(null);
});

describe('loadOrCreateIdentity', () => {
  it('空存储 → 创建并落库', () => {
    const store = fakeStorage();
    setIdentityStorage(store);
    const identity = loadOrCreateIdentity();
    expect(identity.privHex).toHaveLength(64);
    expect(identity.pubkey.startsWith('ed25519:')).toBe(true);
    expect(store._map.has('travelmemory.identity.v1')).toBe(true);
  });

  it('已有身份 → 原样读回（种子不变 = 身份不变）', () => {
    const first = createIdentity();
    const store = fakeStorage({
      'travelmemory.identity.v1': JSON.stringify({ privHex: first.privHex, pubHex: first.pubHex }),
    });
    setIdentityStorage(store);
    const again = loadOrCreateIdentity();
    expect(again.privHex).toBe(first.privHex);
    expect(again.pubkey).toBe(first.pubkey);
  });

  it('存储内容损坏 → 重新生成而不是抛错', () => {
    const store = fakeStorage({ 'travelmemory.identity.v1': 'not-json{{' });
    setIdentityStorage(store);
    const identity = loadOrCreateIdentity();
    expect(identity.privHex).toHaveLength(64);
  });

  it('privHex 长度不对 → 重新生成', () => {
    const store = fakeStorage({ 'travelmemory.identity.v1': JSON.stringify({ privHex: 'too-short' }) });
    setIdentityStorage(store);
    const identity = loadOrCreateIdentity();
    expect(identity.privHex).toHaveLength(64);
  });

  it('存储整体不可用（隐私模式）→ 内存身份，功能不中断', () => {
    const broken = {
      getItem() {
        throw new Error('SecurityError');
      },
      setItem() {
        throw new Error('SecurityError');
      },
      removeItem() {
        throw new Error('SecurityError');
      },
    };
    setIdentityStorage(broken);
    const identity = loadOrCreateIdentity();
    expect(identity.privHex).toHaveLength(64);
    expect(() => saveIdentity(identity)).not.toThrow();
    expect(() => clearIdentity()).not.toThrow();
  });
});

describe('createIdentity', () => {
  it('每次生成不同种子', () => {
    expect(createIdentity().privHex).not.toBe(createIdentity().privHex);
  });

  it('一份种子派生两套密钥（Ed25519 + X25519）', () => {
    const identity = createIdentity();
    expect(identity.x25519Priv).toBeInstanceOf(Uint8Array);
    expect(identity.x25519Pub).toBeInstanceOf(Uint8Array);
    expect(identity.x25519Pub.length).toBe(32);
  });
});
