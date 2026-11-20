// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { sendToParent } from '../theme-light/bridge';
import {
  clearInjectedIdentity,
  getIdentity,
  identitySource,
  loadIdentity,
  publicKeyFromSeed,
  resetIdentityCache,
  secretHexValues,
  setInjectedIdentity,
} from './identity';

const INJECTED_SEED = '11'.repeat(32);

function setEmbedded(value: boolean): void {
  window.history.replaceState(null, '', value ? '/?embedded=1' : '/');
}

/** 把 `indexedDB` 换成一个可控的假实现（jsdom 默认没有）。 */
function installFakeIndexedDb(open: (name: string, version?: number) => unknown): void {
  Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: { open } });
}

function removeFakeIndexedDb(): void {
  Reflect.deleteProperty(globalThis, 'indexedDB');
}

beforeEach(() => {
  clearInjectedIdentity();
  resetIdentityCache();
  setEmbedded(false);
  vi.restoreAllMocks();
});

afterEach(() => {
  clearInjectedIdentity();
  resetIdentityCache();
  setEmbedded(false);
  removeFakeIndexedDb();
  vi.restoreAllMocks();
});

describe('注入身份（§10.2）', () => {
  it('合法 privHex → true、source=injected、getIdentity 返回注入身份', () => {
    expect(identitySource()).toBe('local'); // 注入前
    expect(setInjectedIdentity({ privHex: INJECTED_SEED })).toBe(true);

    expect(identitySource()).toBe('injected');
    const identity = getIdentity();
    expect(identity).not.toBeNull();
    expect(identity?.privHex).toBe(INJECTED_SEED);
    expect(identity?.seedHex).toBe(INJECTED_SEED);
    // 公钥由种子现算，不是从 payload 抄的
    expect(identity?.pubHex).toBe(publicKeyFromSeed(INJECTED_SEED));
    expect(identity?.pubkey).toBe(`ed25519:${publicKeyFromSeed(INJECTED_SEED)}`);
    expect(identity?.persistent).toBe(false);
  });

  it('带 ed25519: 前缀 / 大写 hex 也接受', () => {
    expect(setInjectedIdentity({ privHex: `ed25519:${INJECTED_SEED.toUpperCase()}` })).toBe(true);
    expect(getIdentity()?.privHex).toBe(INJECTED_SEED);
  });

  it('非法 privHex → false，且 identitySource 不变成 injected、不抛错', () => {
    expect(setInjectedIdentity({ privHex: 'xx' })).toBe(false);
    expect(identitySource()).not.toBe('injected');
    expect(getIdentity()).toBeNull();

    // 各种脏输入都不许抛
    for (const bad of [null, undefined, 'seed', 42, {}, { privHex: 123 }, { privHex: '' }]) {
      expect(setInjectedIdentity(bad as unknown)).toBe(false);
    }
    expect(identitySource()).not.toBe('injected');
  });

  it('clearInjectedIdentity 回到 local / memory', () => {
    setInjectedIdentity({ privHex: INJECTED_SEED });
    expect(identitySource()).toBe('injected');
    clearInjectedIdentity();
    expect(['local', 'memory']).toContain(identitySource());
    expect(getIdentity()).toBeNull();
  });

  it('注入后 loadIdentity 直接返回注入身份（优先于 IDB）', async () => {
    const open = vi.fn();
    installFakeIndexedDb(open);
    setInjectedIdentity({ privHex: INJECTED_SEED });

    const identity = await loadIdentity();
    expect(identity.privHex).toBe(INJECTED_SEED);
    expect(identitySource()).toBe('injected');

    removeFakeIndexedDb();
  });
});

describe('注入身份不落盘（本任务的验收点）', () => {
  it('注入 + 读取全程不调用 indexedDB.open', async () => {
    const open = vi.fn();
    installFakeIndexedDb(open);

    expect(setInjectedIdentity({ privHex: INJECTED_SEED })).toBe(true);
    // 连读两次（模拟 App 的初始化 + onInit 后刷新）
    await loadIdentity();
    await loadIdentity();

    expect(open).not.toHaveBeenCalled();
    removeFakeIndexedDb();
  });

  it('对照：没有注入时会去开 IndexedDB（证明上一条不是因为假实现没生效）', async () => {
    const open = vi.fn(() => {
      throw new Error('blocked');
    });
    installFakeIndexedDb(open);

    await loadIdentity();
    expect(open).toHaveBeenCalledTimes(1);
    // 打不开 → 'memory' 三态
    expect(identitySource()).toBe('memory');

    removeFakeIndexedDb();
  });
});

describe('身份不外发（sendToParent 兜底）', () => {
  it('含 privHex 字段 / 含种子值的消息被拒绝，正常消息照发', () => {
    setEmbedded(true);
    setInjectedIdentity({ privHex: INJECTED_SEED });
    expect(secretHexValues()).toContain(INJECTED_SEED);

    // 预期会 warn（拒绝外发），把它静音以免污染测试输出
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const postMessage = vi.spyOn(window, 'postMessage');
    const identity = getIdentity();
    expect(identity).not.toBeNull();
    const seed = identity?.privHex ?? INJECTED_SEED;

    // ① 字段名泄漏
    sendToParent({ type: 'x', identity: { privHex: seed } });
    expect(postMessage).not.toHaveBeenCalled();

    // ② 换字段名但值还是种子（值检查）
    sendToParent({ type: 'x', note: seed });
    expect(postMessage).not.toHaveBeenCalled();

    // ③ 正常消息不受影响
    sendToParent({ type: 'ready' });
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][0]).toEqual({ type: 'ready' });
    expect(warn).toHaveBeenCalledTimes(2);
  });
});
