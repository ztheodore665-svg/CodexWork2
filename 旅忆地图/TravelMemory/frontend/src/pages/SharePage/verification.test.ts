/** `buildVerificationView` 的三态映射（Node 环境，不碰 DOM）。
 *
 * 这里守的核心约定是 **null ≠ 失败**：`commitmentOk` / `chainOk` 为 `null`
 * 表示"本机没有证据"，必须落成 unknown；只有明确的 `false` 才是 fail。
 * 把这两者混同会让跨设备收到的正常信物被涂红 —— 那是本协议最常见的误判。
 */

import { describe, expect, it } from 'vitest';

import type { SafeReport, UnpackFailure, UnpackView } from '../../types/share';
import { buildVerificationView, type CheckItem } from './verification';

function report(over: Partial<SafeReport> = {}): SafeReport {
  return {
    structureOk: true,
    signatureOk: true,
    commitmentOk: true,
    chainOk: true,
    chainNote: '链首记录（seq=0 且 prevHash 指向创世哈希），链路完整',
    ...over,
  };
}

function view(over: Partial<SafeReport> = {}): UnpackView {
  const safe = report(over);
  return {
    ok: true,
    groups: [],
    from: { displayName: '小林', pubkey: 'ed25519:' + 'ab'.repeat(32) },
    shareId: 's_test0001',
    commitment: 'sha256:' + 'cd'.repeat(32),
    signature: 'ef'.repeat(32),
    chainSeq: 0,
    chainPrevHash: 'sha256:' + '00'.repeat(32),
    chainOk: safe.chainOk,
    chainNote: safe.chainNote,
    report: safe,
    canAccept: true,
  };
}

function failure(kind: UnpackFailure['kind'], reason: string): UnpackFailure {
  return { ok: false, kind, reason };
}

function byKey(items: CheckItem[], key: CheckItem['key']): CheckItem {
  const found = items.find((item) => item.key === key);
  if (!found) throw new Error(`缺少检查项 ${key}`);
  return found;
}

describe('buildVerificationView', () => {
  it('全绿：三条全 pass，ok 为 true', () => {
    const result = buildVerificationView(view());
    expect(result.items.map((item) => item.state)).toEqual(['pass', 'pass', 'pass']);
    expect(result.ok).toBe(true);
    expect(result.shareId).toBe('s_test0001');
    expect(result.fromName).toBe('小林');
    expect(result.summary).toContain('可以放心接受');
  });

  it('签名失败：signature 为 fail', () => {
    const result = buildVerificationView(view({ signatureOk: false }));
    expect(byKey(result.items, 'signature').state).toBe('fail');
    expect(result.ok).toBe(false);
    // 其余两条不受牵连：它们各自有独立的证据
    expect(byKey(result.items, 'commitment').state).toBe('pass');
  });

  it('承诺为 null：commitment 是 unknown，不是 fail', () => {
    const result = buildVerificationView(view({ commitmentOk: null }));
    expect(byKey(result.items, 'commitment').state).toBe('unknown');
    expect(result.ok).toBe(true); // 没有证伪证据 → 仍然可接受
  });

  it('承诺为 false：commitment 是 fail', () => {
    const result = buildVerificationView(view({ commitmentOk: false }));
    expect(byKey(result.items, 'commitment').state).toBe('fail');
    expect(result.ok).toBe(false);
  });

  it('链为 null：chain 是 unknown 且 detail 带上 chainNote', () => {
    const note = 'prevHash 未命中本地账本（跨设备接收属预期现象），无法判定链路';
    const result = buildVerificationView(view({ chainOk: null, chainNote: note }));
    const chain = byKey(result.items, 'chain');
    expect(chain.state).toBe('unknown');
    expect(chain.detail).toContain(note);
    expect(chain.detail).toContain('跨设备');
    expect(chain.technical).toContain(note);
  });

  it('链为 false：chain 是 fail', () => {
    const result = buildVerificationView(view({ chainOk: false, chainNote: '本地哈希链不连续' }));
    expect(byKey(result.items, 'chain').state).toBe('fail');
    expect(result.ok).toBe(false);
  });

  it.each<[UnpackFailure['kind'], string]>([
    ['old-version', '信物版本过旧，请用新版本重新生成'],
    ['not-for-you', '此信物不包含给你的内容'],
    ['invalid', '文件已损坏，或不是 TrailShare 信物'],
  ])('解包失败（%s）：三条全 fail，summary 用 reason', (kind, reason) => {
    const result = buildVerificationView(failure(kind, reason));
    expect(result.items.map((item) => item.state)).toEqual(['fail', 'fail', 'fail']);
    expect(result.ok).toBe(false);
    expect(result.summary).toContain(reason);
    expect(result.shareId).toBeNull();
    expect(result.fromName).toBeNull();
  });

  it('还没选文件：三条 unknown，ok 为 false', () => {
    const result = buildVerificationView(null);
    expect(result.items).toHaveLength(3);
    expect(result.items.every((item) => item.state === 'unknown')).toBe(true);
    expect(result.ok).toBe(false);
  });
});
