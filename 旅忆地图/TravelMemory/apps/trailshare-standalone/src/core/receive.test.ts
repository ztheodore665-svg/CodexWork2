/** 收件人侧（W12-5）的单测：真包 → `receiveFromText` → 三种失败 + 成功。
 *
 * 用**真实的 `pack()`** 造包（不手写假 JSON）：手写的假包只能证明 if 分支写对了，
 * 证明不了"这套解包真的认得我们自己的包"。三种结果各有一条：
 *  - 自己的种子 → `ok: true`；
 *  - 别人的种子 → `not-for-you`（文案必须与协议一致，不透露组数）；
 *  - 版本改成旧版 → `old-version`（提示重新生成，**不能**说"文件损坏"）。
 */

import { describe, expect, it } from 'vitest';

import { RECEIVE_CORRUPT_MESSAGE, placesOfResult, receiveFromText } from './receive';
import { identityFromSeed } from '../share/crypto/keys';
import { emptyVisibility } from '../share/filter';
import { pack } from '../share/pack';
import { OLD_VERSION_MESSAGE } from '../share/schema';
import type { Identity, Tag, Trip, UnpackFailure } from '../types/share';

const OWNER_SEED = '55'.repeat(32);
const STRANGER_SEED = '66'.repeat(32);

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function makeTrip(): Trip {
  return {
    id: 't_receive1',
    title: '苏州半日',
    createdAt: '2026-11-02T00:00:00.000Z',
    days: [
      {
        id: 'd1_receive',
        date: '2026-11-02',
        places: [
          {
            id: 'p1_receive',
            name: '拙政园',
            label: '上午',
            tag: 'heritage' as Tag,
            intensity: 'high',
            coverIndex: 0,
            photos: [{ id: 'ph_r1', image: bytes([0xff, 0xd8, 0x11]), caption: '雨后的廊桥' }],
          },
          {
            id: 'p2_receive',
            name: '平江路',
            tag: 'food' as Tag,
            intensity: 'mid',
            coverIndex: 0,
            photos: [{ id: 'ph_r2', image: bytes([0xff, 0xd8, 0x22]) }],
          },
        ],
      },
    ],
  };
}

async function packFixture(owner: Identity): Promise<Record<string, unknown>> {
  const trip = makeTrip();
  const { pkg } = await pack({
    groups: [{ trip, visibility: emptyVisibility(trip.id, 'g1_self'), recipientPubkeys: [owner.pubkey] }],
    senderIdentity: owner,
    displayName: '小旅',
    now: new Date('2026-11-13T00:00:00.000Z'),
    shareId: 's_receive1',
  });
  return pkg as unknown as Record<string, unknown>;
}

describe('receiveFromText —— 成功', () => {
  it('自己的种子能解开：报验签/承诺/链，地点与配文都在', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const result = await receiveFromText(JSON.stringify(pkg), OWNER_SEED);
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    expect(result.from.displayName).toBe('小旅');
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
    expect(result.report.structureOk).toBe(true);
    expect(result.canAccept).toBe(true);
    // 组数不暴露：视图里虽然有 groups，但 UI 只用 placesOfResult
    expect(placesOfResult(result).map((place) => place.name)).toEqual(['拙政园', '平江路']);
    expect(placesOfResult(result)[0].photos[0].caption).toBe('雨后的廊桥');
    expect(placesOfResult(result)[0].date).toBe('2026-11-02');
  });
});

describe('receiveFromText —— 三种失败', () => {
  it('别人的种子 → not-for-you（此信物不包含给你的内容）', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const result = await receiveFromText(JSON.stringify(pkg), STRANGER_SEED);
    expect(result.ok).toBe(false);
    const failure = result as UnpackFailure;
    expect(failure.kind).toBe('not-for-you');
    expect(failure.reason).toBe('此信物不包含给你的内容');
    // 红线：文案里不许出现组数/组 id 的字样
    expect(failure.reason).not.toContain('组');
  });

  it('版本改成 ts-2026.3 → old-version（让发送方重新生成）', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);
    const oldPkg = { ...pkg, version: 'ts-2026.3' };

    const result = await receiveFromText(JSON.stringify(oldPkg), OWNER_SEED);
    expect(result.ok).toBe(false);
    const failure = result as UnpackFailure;
    expect(failure.kind).toBe('old-version');
    expect(failure.reason).toBe(OLD_VERSION_MESSAGE);
    expect(failure.reason).not.toBe(RECEIVE_CORRUPT_MESSAGE);
  });

  it('结构破损 / 不是 JSON / 不是对象 → invalid（文件已损坏，或不是 TrailShare 信物）', async () => {
    const cases = ['{ not json', '"just a string"', '[]', JSON.stringify({ version: 'ts-2026.4' }), ''];
    for (const text of cases) {
      const result = await receiveFromText(text, OWNER_SEED);
      expect(result.ok).toBe(false);
      const failure = result as UnpackFailure;
      expect(failure.kind).toBe('invalid');
      expect(failure.reason).toBe(RECEIVE_CORRUPT_MESSAGE);
    }
  });
});

describe('placesOfResult', () => {
  it('失败结果返回空数组（UI 不会画出半张列表）', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);
    const result = await receiveFromText(JSON.stringify(pkg), STRANGER_SEED);
    expect(placesOfResult(result)).toEqual([]);
  });
});
