/** D10 收件人规则：**填了 → 收件人 + 自己；没填 → 只有自己**。
 *
 * 这条规则是产品承诺的一部分（§7.2.2）：不填收件人时那个包只有自己能打开，
 * UI 必须明说。落实到协议上就是 `wrappedKeys` 的条数 —— 这里逐条钉死。
 */

import { describe, expect, it } from 'vitest';

import { identityFromSeed } from '../crypto/keys';
import { parseRecipientInput, resolveRecipients, packJourney } from '../../core/packFlow';
import type { TripDraft } from '../../core/buildTrip';
import type { Identity } from '../../types/share';

const MY_SEED = '55'.repeat(32);
const FRIEND_SEED = '66'.repeat(32);

function myIdentity(): Identity {
  return identityFromSeed(MY_SEED);
}

function friendPubkey(): string {
  return identityFromSeed(FRIEND_SEED).pubkey;
}

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function makeDraft(): TripDraft {
  return {
    title: '杭州两日',
    days: [
      {
        id: 'd1',
        date: '2026-11-01',
        places: [
          {
            id: 'p1',
            name: '西湖',
            tag: 'nature',
            intensity: 'high',
            photos: [
              {
                id: 'p_a',
                name: 'a.jpg',
                bytes: bytes([0xff, 0xd8, 0x01, 0x02]),
                caption: '断桥',
                thumbnailDataUrl: 'data:image/jpeg;base64,AAAA',
                takenAt: '2026-11-01T01:00:00.000Z',
                lat: 30.2431,
                lng: 120.1459,
              },
            ],
          },
        ],
      },
    ],
  };
}

describe('parseRecipientInput', () => {
  it('支持逗号 / 中文逗号 / 空格 / 换行分隔，剥掉 ed25519: 前缀', () => {
    const friend = friendPubkey();
    const friendBare = friend.replace('ed25519:', '').toLowerCase();
    const mineBare = myIdentity().pubkey.replace('ed25519:', '').toLowerCase();
    const parsed = parseRecipientInput(`${friend},${friendBare}，${mineBare}\n${friend}`);
    expect(parsed).toHaveLength(4);
    expect(parsed[0]).toBe(friendBare);
    expect(parsed[1]).toBe(friendBare);
    expect(parsed[2]).toBe(mineBare);
    expect(parsed[3]).toBe(friendBare);
    expect(parsed.every((item) => /^[0-9a-f]{64}$/.test(item))).toBe(true);
  });

  it('空输入返回空数组', () => {
    expect(parseRecipientInput('')).toEqual([]);
    expect(parseRecipientInput('   \n ')).toEqual([]);
  });

  it('非法公钥抛中文错误', () => {
    expect(() => parseRecipientInput('not-a-key')).toThrow(/收件人公钥格式不对/);
    expect(() => parseRecipientInput('abcd')).toThrow(/64 位十六进制/);
  });
});

describe('resolveRecipients（D10）', () => {
  it('没填 → 只有自己', () => {
    const mine = myIdentity().pubkey;
    const recipients = resolveRecipients('', mine);
    expect(recipients).toHaveLength(1);
    expect(recipients[0]).toBe(mine.replace('ed25519:', '').toLowerCase());
  });

  it('填了 → 收件人 + 自己', () => {
    const mine = myIdentity().pubkey;
    const recipients = resolveRecipients(friendPubkey(), mine);
    expect(recipients).toHaveLength(2);
    expect(recipients[0]).toBe(friendPubkey().replace('ed25519:', '').toLowerCase());
    expect(recipients[1]).toBe(mine.replace('ed25519:', '').toLowerCase());
  });

  it('用户把自己的公钥也填进去时去重', () => {
    const mine = myIdentity().pubkey;
    expect(resolveRecipients(mine, mine)).toHaveLength(1);
    // 同一个朋友填两次、写法不同（带/不带前缀）也只算一个
    const friend = friendPubkey();
    expect(resolveRecipients(`${friend}, ${friend.slice(8)}`, mine)).toHaveLength(2);
  });
});

describe('packJourney —— wrappedKeys 条数等于收件人数', () => {
  it('没填收件人：只有自己一条，并且 onlyMe 为真', async () => {
    const me = myIdentity();
    const result = await packJourney({
      draft: makeDraft(),
      myIdentity: me,
      recipientsRaw: '',
      now: new Date('2026-11-13T00:00:00.000Z'),
    });

    expect(result.pkg.groups[0].wrappedKeys).toHaveLength(1);
    expect(result.pkg.groups[0].wrappedKeys[0].recipientPubkey).toBe(
      me.pubkey.replace('ed25519:', '').toLowerCase(),
    );
    expect(result.onlyMe).toBe(true);
    expect(result.report.ok).toBe(true);
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
    expect(result.report.groups).toBe(1);
    expect(result.report.places).toBe(1);
    expect(result.report.photos).toBe(1);
  });

  it('填了收件人：两条（收件人 + 自己），onlyMe 为假，且自己仍能解开', async () => {
    const me = myIdentity();
    const result = await packJourney({
      draft: makeDraft(),
      myIdentity: me,
      recipientsRaw: friendPubkey(),
      now: new Date('2026-11-13T00:00:00.000Z'),
    });

    expect(result.pkg.groups[0].wrappedKeys).toHaveLength(2);
    const stored = result.pkg.groups[0].wrappedKeys.map((key) => key.recipientPubkey);
    expect(stored).toContain(friendPubkey().replace('ed25519:', '').toLowerCase());
    expect(stored).toContain(me.pubkey.replace('ed25519:', '').toLowerCase());
    expect(result.onlyMe).toBe(false);
    // 自检：用自己的种子能解开
    expect(result.report.ok).toBe(true);
  });

  it('收件人公钥非法时整个打包失败（不产出半成品包）', async () => {
    const me = myIdentity();
    await expect(
      packJourney({ draft: makeDraft(), myIdentity: me, recipientsRaw: 'oops' }),
    ).rejects.toThrow(/收件人公钥格式不对/);
  });
});
