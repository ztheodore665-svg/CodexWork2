/** pack / unpack 的往返、屏蔽、篡改、版本闸门与视图约束。
 *
 * 这一组断言是整个 W6-4 的 DoD：任何一条红掉，都意味着"定向加密"这件产品承诺
 * 在某个环节上是假的（能看见不该看见的，或看不见该看见的）。
 */

import { describe, expect, it } from 'vitest';

import { pack, type PackInput } from './pack';
import { NOT_FOR_YOU_MESSAGE, unpack, unpackForViewer } from './unpack';
import { MAX_GROUPS, OLD_VERSION_MESSAGE, OldShareVersionError } from './schema';
import { identityA, identityB, identityC, identityD, makeTrip, makeVisibility, NOW, photoText, SEED_B, SEED_D } from './fixtures';
import type { SharePackage, VisibilityState } from '../../types/share';
import { fromBase64, toBase64 } from '../../utils/base64';

/** 一组：全可见，收件人默认 B。 */
function inputFor(visibility: VisibilityState = makeVisibility(), recipients: string[] = [identityB.pubkey]): PackInput {
  return {
    groups: [{ trip: makeTrip(), visibility, recipientPubkeys: recipients }],
    senderIdentity: identityA,
    displayName: 'A',
    now: NOW,
    shareId: 's_deadbeef',
    chainEnabled: false,
  };
}

/** 深拷贝一份包（模拟"文件被转存 / 被人改过"）。 */
function clone(pkg: SharePackage): SharePackage {
  return JSON.parse(JSON.stringify(pkg)) as SharePackage;
}

/** 翻密文的一个字节。 */
function flipOneByte(b64: string): string {
  const bytes = fromBase64(b64);
  bytes[0] ^= 0x01;
  return toBase64(bytes);
}

describe('pack → unpack 往返', () => {
  it('内容逐字段一致：地点名、label、日期、tag、intensity、照片字节、配文', async () => {
    const { pkg } = await pack(inputFor());
    const result = await unpack(pkg, { privHex: identityB.privHex });

    expect(result.groups.length).toBe(1);
    const places = result.groups[0].places;
    expect(places.map((p) => p.placeId)).toEqual(['p_1', 'p_2', 'p_3']);

    const [p1, p2, p3] = places;
    expect(p1.name).toBe('清水寺');
    expect(p1.label).toBe('上午');
    expect(p1.date).toBe('2026-10-01');
    expect(p1.tag).toBe('heritage');
    expect(p1.intensity).toBe('high');
    expect(p1.photos.map((ph) => ph.photoId)).toEqual(['ph_1', 'ph_2']);
    expect(await photoText(p1.photos[0].image)).toBe('photo-1');
    expect(p1.photos[0].caption).toBe('清晨的空殿');
    expect(p1.photos[1].caption).toBe('红叶回廊');

    expect(p2.name).toBe('锦市场');
    expect(p2.label).toBeUndefined(); // 没有 label 的地点不能凭空多出一个
    expect(p2.date).toBe('2026-10-01');
    expect(p2.tag).toBe('food');
    expect(p2.intensity).toBe('mid');
    expect(p2.photos[0].caption).toBeUndefined();

    expect(p3.name).toBe('岚山竹林');
    expect(p3.date).toBe('2026-10-02');
    expect(p3.tag).toBe('nature');
    expect(await photoText(p3.photos[0].image)).toBe('photo-4');
    expect(p3.photos[0].caption).toBe('竹林小径');
  });

  it('正常包的报告：结构 / 签名 / 承诺全过，可以接受', async () => {
    const { pkg } = await pack(inputFor());
    const result = await unpack(pkg, { privHex: identityB.privHex });
    expect(result.report.structureOk).toBe(true);
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
    expect(result.canAccept).toBe(true);
  });

  it('组内按 date 升序稳定排序', async () => {
    const { pkg } = await pack(inputFor());
    const result = await unpack(pkg, { privHex: identityB.privHex });
    const dates = result.groups[0].places.map((p) => p.date);
    expect(dates).toEqual(['2026-10-01', '2026-10-01', '2026-10-02']);
  });

  it('包内不泄露明文：地点名与配文都不出现在序列化结果里', async () => {
    const { pkg } = await pack(inputFor());
    const text = JSON.stringify(pkg);
    expect(text).not.toContain('清水寺');
    expect(text).not.toContain('清晨的空殿');
    expect(text).not.toContain('2026-10-01'); // 日期也在密文里
  });
});

describe('屏蔽 = 不打包（断言包结构，不是断言 UI）', () => {
  it('屏蔽一个 Day → 包里的 places.length 真的变少', async () => {
    const full = await pack(inputFor());
    const hidden = await pack(inputFor(makeVisibility({ hiddenDayIds: ['d_2'] })));
    expect(full.pkg.groups[0].payload.places.length).toBe(3);
    expect(hidden.pkg.groups[0].payload.places.length).toBe(2);
  });

  it('屏蔽一张照片 → 该 place 的 photos.length 变少', async () => {
    const hidden = await pack(inputFor(makeVisibility({ hiddenPhotoIds: ['ph_1'] })));
    const p1 = hidden.pkg.groups[0].payload.places.find((p) => p.placeId === 'p_1');
    expect(p1?.photos.length).toBe(1);
    expect(p1?.photos[0].photoId).toBe('ph_2');
  });

  it('屏蔽配文 → encryptedCaption / ivCaption 都不存在', async () => {
    const hidden = await pack(inputFor(makeVisibility({ hiddenCaptionPhotoIds: ['ph_1'] })));
    const p1 = hidden.pkg.groups[0].payload.places.find((p) => p.placeId === 'p_1');
    expect(p1?.photos[0].encryptedCaption).toBeUndefined();
    expect(p1?.photos[0].ivCaption).toBeUndefined();
    expect(p1?.photos[1].encryptedCaption).toBeDefined();
  });

  it('整组全屏蔽 → pack 抛「第 N 组没有任何可见内容」', async () => {
    const allHidden = makeVisibility({ hiddenDayIds: ['d_1', 'd_2'] });
    await expect(pack(inputFor(allHidden))).rejects.toThrow('第 1 组没有任何可见内容');
  });

  it('多组时报错带上正确的组号', async () => {
    const input = inputFor();
    const two: PackInput = {
      ...input,
      groups: [
        { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityB.pubkey] },
        { trip: makeTrip(), visibility: makeVisibility({ hiddenDayIds: ['d_1', 'd_2'] }), recipientPubkeys: [identityB.pubkey] },
      ],
    };
    await expect(pack(two)).rejects.toThrow('第 2 组没有任何可见内容');
  });

  it('组数上限：超过 3 组直接拒绝', async () => {
    const one = { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityB.pubkey] };
    const input: PackInput = { ...inputFor(), groups: [one, one, one, one] };
    await expect(pack(input)).rejects.toThrow(`1–${MAX_GROUPS}`);
  });
});

describe('篡改与未授权', () => {
  it('改 encryptedMeta 一个字节 → 验签失败，且该地点从结果里消失', async () => {
    const { pkg } = await pack(inputFor());
    const tampered = clone(pkg);
    const place = tampered.groups[0].payload.places[0];
    place.encryptedMeta = flipOneByte(place.encryptedMeta);

    const result = await unpack(tampered, { privHex: identityB.privHex });
    expect(result.report.signatureOk).toBe(false);
    expect(result.canAccept).toBe(false);
    // 承诺只覆盖 tag/intensity/placeId/盐，不受密文改动影响
    expect(result.report.commitmentOk).toBe(true);
    expect(result.groups[0].places.map((p) => p.placeId)).toEqual(['p_2', 'p_3']);
  });

  it('改 tag → 承诺对不上（承诺覆盖明文 tag）', async () => {
    const { pkg } = await pack(inputFor());
    const tampered = clone(pkg);
    tampered.groups[0].payload.places[0].tag = 'nature';

    const result = await unpack(tampered, { privHex: identityB.privHex });
    expect(result.report.commitmentOk).toBe(false);
    expect(result.report.signatureOk).toBe(false);
    expect(result.canAccept).toBe(false);
  });

  it('非收件人 unpack → 抛「此信物不包含给你的内容」', async () => {
    const { pkg } = await pack(inputFor());
    await expect(unpack(pkg, { privHex: SEED_D })).rejects.toThrow(NOT_FOR_YOU_MESSAGE);
    await expect(unpack(pkg, { privHex: identityD.privHex })).rejects.toThrow(NOT_FOR_YOU_MESSAGE);
  });

  it('只给 B 的第二组：C 解开第 2 组，看不到第 1 组', async () => {
    const input: PackInput = {
      ...inputFor(),
      groups: [
        { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityB.pubkey] },
        { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityC.pubkey] },
      ],
    };
    const { pkg } = await pack(input);
    expect(pkg.groups.length).toBe(2);

    const forC = await unpack(pkg, { privHex: identityC.privHex });
    expect(forC.groups.length).toBe(1);
    expect(forC.groups[0].groupId).toBe(pkg.groups[1].groupId);
  });

  it('旧版本 ts-2026.3 → 抛 OldShareVersionError，文案固定', async () => {
    const { pkg } = await pack(inputFor());
    const old = clone(pkg) as unknown as Record<string, unknown>;
    old.version = 'ts-2026.3';

    await expect(unpack(old, { privHex: SEED_B })).rejects.toBeInstanceOf(OldShareVersionError);
    await expect(unpack(old, { privHex: SEED_B })).rejects.toThrow(OLD_VERSION_MESSAGE);
  });

  it('更高版本 → 结构错误，提示升级', async () => {
    const { pkg } = await pack(inputFor());
    const future = clone(pkg) as unknown as Record<string, unknown>;
    future.version = 'ts-2026.5';
    await expect(unpack(future, { privHex: SEED_B })).rejects.toThrow('请升级本应用');
  });

  it('结构损坏（少字段）→ 统一文案，不透露出是哪个字段', async () => {
    const { pkg } = await pack(inputFor());
    const broken = clone(pkg) as unknown as Record<string, unknown>;
    broken.commitment = { hash: '', algo: 'sha256' };
    await expect(unpack(broken, { privHex: SEED_B })).rejects.toThrow('文件已损坏，或不是 TrailShare 信物');
  });
});

describe('unpackForViewer：界面不暴露组数', () => {
  it('返回值里没有 pkg / totalGroups / groupCount', async () => {
    const { pkg } = await pack(inputFor());
    const view = await unpackForViewer(pkg, { privHex: SEED_B });

    expect(view.ok).toBe(true);
    if (!view.ok) throw new Error('应当解开');
    expect('pkg' in view).toBe(false);
    expect('totalGroups' in view).toBe(false);
    expect('groupCount' in view).toBe(false);
  });

  it('只给 B 一组的两组包，B 的视图里只有 1 组（无法反推总数）', async () => {
    const input: PackInput = {
      ...inputFor(),
      groups: [
        { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityC.pubkey] },
        { trip: makeTrip(), visibility: makeVisibility(), recipientPubkeys: [identityB.pubkey] },
      ],
    };
    const { pkg } = await pack(input);
    const view = await unpackForViewer(pkg, { privHex: identityB.privHex });
    expect(view.ok).toBe(true);
    if (!view.ok) throw new Error('应当解开');
    expect(view.groups.length).toBe(1);
    expect(pkg.groups.length).toBe(2); // 真实总数是 2，但视图无从得知
  });

  it('report 不含 errors（errors 里可能出现 groupId）', async () => {
    const { pkg } = await pack(inputFor());
    const view = await unpackForViewer(pkg, { privHex: SEED_B });
    if (!view.ok) throw new Error('应当解开');
    expect('errors' in view.report).toBe(false);
    expect(view.report.commitmentOk).toBe(true);
  });

  it('失败分支：旧版本 / 非收件人 / 损坏，各自给出不泄露信息的文案', async () => {
    const { pkg } = await pack(inputFor());

    const old = clone(pkg) as unknown as Record<string, unknown>;
    old.version = 'ts-2026.3';
    const oldView = await unpackForViewer(old, { privHex: SEED_B });
    expect(oldView).toEqual({ ok: false, kind: 'old-version', reason: OLD_VERSION_MESSAGE });

    const notForYou = await unpackForViewer(pkg, { privHex: SEED_D });
    expect(notForYou).toEqual({ ok: false, kind: 'not-for-you', reason: NOT_FOR_YOU_MESSAGE });

    const broken = clone(pkg) as unknown as Record<string, unknown>;
    broken.groups = [];
    const invalid = await unpackForViewer(broken, { privHex: SEED_B });
    expect(invalid).toEqual({ ok: false, kind: 'invalid', reason: '文件已损坏，或不是 TrailShare 信物' });
  });

  it('processed 由 UI 回填（重复导入时显示已处理）', async () => {
    const { pkg } = await pack(inputFor());
    const first = await unpackForViewer(pkg, { privHex: SEED_B });
    if (!first.ok) throw new Error('应当解开');
    expect(first.groups[0].processed).toBe(false);

    const again = await unpackForViewer(pkg, { privHex: SEED_B }, { processedGroupIds: [first.groups[0].groupId] });
    if (!again.ok) throw new Error('应当解开');
    expect(again.groups[0].processed).toBe(true);
  });
});

describe('链与承诺的开关行为', () => {
  it('关链：seq=0、prevHash=GENESIS、chainRecord=null', async () => {
    const { pkg, chainRecord } = await pack(inputFor());
    expect(pkg.chain).toEqual({ prevHash: '0'.repeat(64), seq: 0, anchor: null });
    expect(chainRecord).toBeNull();
  });

  it('开链：seq 从 0 开始，chainRecord 带 hash 与 prevHash', async () => {
    const { chainRecord } = await pack({ ...inputFor(), chainEnabled: true });
    expect(chainRecord).not.toBeNull();
    expect(chainRecord?.seq).toBe(0);
    expect(chainRecord?.event).toBe('share_created');
    expect(chainRecord?.hash.startsWith('sha256:')).toBe(true);
  });

  it('两次打包的盐不同 → 承诺不同（同一批内容打两次不留下相同指纹）', async () => {
    const one = await pack(inputFor());
    const two = await pack(inputFor());
    expect(one.pkg.commitment.hash).not.toBe(two.pkg.commitment.hash);
  });
});
