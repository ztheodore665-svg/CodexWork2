/** 协议严谨性验收（QA 独立补充，不改现有测试）。
 *
 * 目的：用四个"坏人会干的事"钉住协议边界——
 *  1. 篡改地点 tag → 签名必须报 false；
 *  2. 只改承诺哈希一个字符 → 承诺必须报 false；
 *  3. 第三方种子解包 → 必须被拒，且错误文案**不含 groupId、不含组数**；
 *  4. 版本闸门：旧版本 / 未来版本给出各自的固定文案。
 *
 * 断言口径来自任务书 §7.2.4 / §10.3 与 schema.ts 的协议定义，不是照抄实现。
 */

import { describe, expect, it } from 'vitest';

import type { Identity, SharePackage, Tag, Trip } from '../../types/share';
import { identityFromSeed } from '../crypto/keys';
import { emptyVisibility } from '../filter';
import { pack } from '../pack';
import {
  OLD_VERSION_MESSAGE,
  OldShareVersionError,
  UPGRADE_MESSAGE,
} from '../schema';
import { NOT_FOR_YOU_MESSAGE, unpack } from '../unpack';

const OWNER_SEED = '33'.repeat(32);
const STRANGER_SEED = '44'.repeat(32);

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function makeTrip(): Trip {
  return {
    id: 't_hardening1',
    title: '协议边界',
    createdAt: '2026-11-01T00:00:00.000Z',
    days: [
      {
        id: 'd1_hardening',
        date: '2026-11-01',
        places: [
          {
            id: 'p1_hardening',
            name: '拙政园',
            tag: 'heritage' as Tag,
            intensity: 'high',
            coverIndex: 0,
            photos: [{ id: 'ph_h1', image: bytes([0xff, 0xd8, 0x01, 0xfe, 0x11]) }],
          },
        ],
      },
    ],
  };
}

async function packFixture(owner: Identity): Promise<SharePackage> {
  const trip = makeTrip();
  const { pkg } = await pack({
    groups: [{ trip, visibility: emptyVisibility(trip.id, 'g1_self'), recipientPubkeys: [owner.pubkey] }],
    senderIdentity: owner,
    displayName: '我',
    now: new Date('2026-11-13T00:00:00.000Z'),
    shareId: 's_hardening1',
  });
  return pkg;
}

/** 深拷贝，避免篡改污染 fixture 本身。 */
function clone(pkg: SharePackage): SharePackage {
  return JSON.parse(JSON.stringify(pkg)) as SharePackage;
}

describe('协议严谨性 @qa', () => {
  it('篡改地点 tag 后，report.signatureOk === false', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const tampered = clone(pkg);
    // 改成另一个**合法** tag，保证不会先被结构校验拦掉——必须让验签来抓。
    tampered.groups[0].payload.places[0].tag = 'food' as Tag;

    const result = await unpack(tampered, { privHex: OWNER_SEED });
    expect(result.report.signatureOk).toBe(false);
    expect(result.canAccept).toBe(false);
    // 承诺值也用 tag 复算，理应一并报不一致
    expect(result.report.commitmentOk).toBe(false);
  });

  it('只改承诺哈希一个字符，report.commitmentOk === false', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const tampered = clone(pkg);
    const original = tampered.commitment.hash;
    const flipped = original[0] === 'a' ? 'b' : 'a';
    tampered.commitment.hash = flipped + original.slice(1);
    expect(tampered.commitment.hash).not.toBe(original);

    const result = await unpack(tampered, { privHex: OWNER_SEED });
    expect(result.report.commitmentOk).toBe(false);
    expect(result.canAccept).toBe(false);
  });

  it('第三方种子解不开，且错误文案不含 groupId、不含组数', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    let message = '';
    await expect(
      unpack(pkg, { privHex: STRANGER_SEED }).catch((error: unknown) => {
        message = error instanceof Error ? error.message : String(error);
        throw error;
      }),
    ).rejects.toThrow(NOT_FOR_YOU_MESSAGE);

    // 文案必须是固定的"非收件人"话术，不能泄露内部结构
    expect(message).toBe(NOT_FOR_YOU_MESSAGE);
    expect(message).not.toContain('g1_self'); // 不含 groupId
    expect(message).not.toContain(pkg.groups[0].groupId);
    expect(/\d+\s*组/.test(message)).toBe(false); // 不含"N 组"这类组数提示
    expect(/\d/.test(message)).toBe(false); // 连数字都没有
  });

  it('旧版本 ts-2026.3 抛出「此信物为旧版本，请让发送方重新生成」', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const oldPkg = clone(pkg);
    oldPkg.version = 'ts-2026.3' as typeof pkg.version;

    const run = unpack(oldPkg, { privHex: OWNER_SEED });
    await expect(run).rejects.toThrow(OLD_VERSION_MESSAGE);
    await expect(unpack(oldPkg, { privHex: OWNER_SEED })).rejects.toBeInstanceOf(
      OldShareVersionError,
    );
  });

  it('未来版本 ts-2026.5 抛出「请升级本应用」', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const pkg = await packFixture(owner);

    const futurePkg = clone(pkg);
    futurePkg.version = 'ts-2026.5' as typeof pkg.version;

    await expect(unpack(futurePkg, { privHex: OWNER_SEED })).rejects.toThrow(UPGRADE_MESSAGE);
  });
});
