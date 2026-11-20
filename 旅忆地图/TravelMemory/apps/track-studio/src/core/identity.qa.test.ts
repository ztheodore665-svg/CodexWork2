// @vitest-environment jsdom
/**
 * 阶段 13 验收测试（QA 新增，不改动任何既有测试）。
 *
 * 背景：真浏览器验收发现 `public/embed-harness.html` 发 init 时把身份写成
 * `{ pubkey, seed }`，而 `setInjectedIdentity()` 只认 `{ privHex }`
 * （见 `src/core/identity.ts:154`）。结果是 harness 里子窗口永远显示
 * 「临时身份 · 仅本机有效」，而主应用（`StudioEmbed.tsx:173` 发 `privHex`）是正确的。
 *
 * 所以这里钉两件事：
 *  1. 契约本身（`privHex` 有效、`seed` 无效）—— 锁定 setInjectedIdentity 的入参形状；
 *  2. harness 这个"验证工具"必须发对键名 —— 这条现在会失败，正是要修的 Bug。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import { clearInjectedIdentity, identitySource, setInjectedIdentity } from './identity';

const SEED = '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f';

afterEach(() => {
  clearInjectedIdentity();
});

describe('身份注入契约：只认 privHex', () => {
  it('privHex 键名被接受，身份来源切到 injected', () => {
    const accepted = setInjectedIdentity({ privHex: SEED });

    expect(accepted).toBe(true);
    expect(identitySource()).toBe('injected');
  });

  it('seed 键名不被接受（缺失 privHex），来源保持 local', () => {
    const accepted = setInjectedIdentity({ seed: SEED });

    expect(accepted).toBe(false);
    expect(identitySource()).toBe('local');
  });

  it('空对象 / 非对象一律拒绝，且不抛错', () => {
    expect(setInjectedIdentity({})).toBe(false);
    expect(setInjectedIdentity(null)).toBe(false);
    expect(setInjectedIdentity('privHex')).toBe(false);
  });
});

describe('embed-harness 必须发 privHex（否则"使用主应用身份"永远验不到）', () => {
  it('public/embed-harness.html 的 init payload 含 privHex 而非 seed', () => {
    const harnessPath = resolve(process.cwd(), 'public/embed-harness.html');
    const html = readFileSync(harnessPath, 'utf8');
    // 只截取 btn-init 的 click 处理块，避免命中其它位置的 seed 字样
    const initBlock = html.slice(html.indexOf("getElementById('btn-init')"));
    const block = initBlock.slice(0, initBlock.indexOf('});'));

    expect(block).toContain('privHex');
    expect(block).not.toMatch(/\bseed\s*:/);
  });
});
