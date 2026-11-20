/** 往返测试：pack → unpack（自己的种子）→ 内容逐一相等；换别人的种子必须解不开。
 *
 * 这是"协议复制是否可用"的地基：对照测试（parity）证明**字节一致**，
 * 这里证明**语义闭环**（能解开、内容不丢、未授权解不开）。
 */

import { describe, expect, it } from 'vitest';

import type { Identity, Tag, Trip, VisibilityState } from '../../types/share';
import { emptyVisibility } from '../filter';
import { identityFromSeed } from '../crypto/keys';
import { pack } from '../pack';
import { NOT_FOR_YOU_MESSAGE, unpack } from '../unpack';

const OWNER_SEED = '33'.repeat(32);
const STRANGER_SEED = '44'.repeat(32);

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function photoBytes(marker: number): ArrayBuffer {
  // 每张照片开头带自己的标记，便于断言"解出来的是同一张"
  return bytes([0xff, 0xd8, marker, marker ^ 0xff, 0x00, 0x11]);
}

function makeTrip(): Trip {
  return {
    id: 't_roundtrip1',
    title: '苏州一日',
    createdAt: '2026-11-01T00:00:00.000Z',
    days: [
      {
        id: 'd1_roundtrip',
        date: '2026-11-01',
        places: [
          {
            id: 'p1_roundtrip',
            name: '拙政园',
            label: '上午',
            tag: 'heritage' as Tag,
            intensity: 'high',
            coverIndex: 0,
            photos: [
              { id: 'ph_a', image: photoBytes(0x01), caption: '雨后的廊桥' },
              { id: 'ph_b', image: photoBytes(0x02) },
            ],
          },
          {
            id: 'p2_roundtrip',
            name: '平江路',
            tag: 'food' as Tag,
            intensity: 'mid',
            coverIndex: 0,
            photos: [{ id: 'ph_c', image: photoBytes(0x03), caption: '' }],
          },
        ],
      },
    ],
  };
}

function visibility(trip: Trip): VisibilityState {
  return emptyVisibility(trip.id, 'g1_self');
}

async function packFixture(owner: Identity) {
  const trip = makeTrip();
  return pack({
    groups: [
      { trip, visibility: visibility(trip), recipientPubkeys: [owner.pubkey] },
    ],
    senderIdentity: owner,
    displayName: '我',
    now: new Date('2026-11-13T00:00:00.000Z'),
    shareId: 's_roundtrip1',
  });
}

describe('pack → unpack 往返', () => {
  it('地点数 / 照片字节 / 配文全部原样解出', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const { pkg } = await packFixture(owner);

    const result = await unpack(pkg, { privHex: OWNER_SEED });

    expect(result.groups).toHaveLength(1);
    const places = result.groups[0].places;
    expect(places).toHaveLength(2);

    // 组内按 date 稳定排序；这里同一天，顺序保持打包顺序
    expect(places.map((place) => place.name)).toEqual(['拙政园', '平江路']);
    expect(places[0].tag).toBe('heritage');
    expect(places[0].intensity).toBe('high');
    expect(places[0].date).toBe('2026-11-01');
    expect(places[0].label).toBe('上午');

    expect(places[0].photos).toHaveLength(2);
    expect(Array.from(new Uint8Array(places[0].photos[0].image))).toEqual([
      0xff, 0xd8, 0x01, 0xfe, 0x00, 0x11,
    ]);
    expect(Array.from(new Uint8Array(places[0].photos[1].image))).toEqual([
      0xff, 0xd8, 0x02, 0xfd, 0x00, 0x11,
    ]);
    // 配文能解出；没有配文的照片不会凭空长出一个空字符串
    expect(places[0].photos[0].caption).toBe('雨后的廊桥');
    expect(places[0].photos[1].caption).toBeUndefined();

    // 自检三态
    expect(result.report.signatureOk).toBe(true);
    expect(result.report.commitmentOk).toBe(true);
    expect(result.report.chainOk).toBe(true); // seq=0 + 创世哈希
    expect(result.canAccept).toBe(true);
    expect(result.report.errors).toEqual([]);
  });

  it('未授权（第三方种子）解不开，抛固定文案', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const { pkg } = await packFixture(owner);

    await expect(unpack(pkg, { privHex: STRANGER_SEED })).rejects.toThrow(NOT_FOR_YOU_MESSAGE);
    await expect(unpack(pkg, { privHex: STRANGER_SEED })).rejects.toThrow('此信物不包含给你的内容');
  });

  it('包是纯 JSON 可序列化的（.trailshare 就是 JSON.stringify）', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const { pkg } = await packFixture(owner);

    const text = JSON.stringify(pkg);
    expect(typeof text).toBe('string');
    const restored = JSON.parse(text) as unknown;
    const result = await unpack(restored, { privHex: OWNER_SEED });
    expect(result.groups[0].places).toHaveLength(2);
    expect(result.report.signatureOk).toBe(true);
  });

  it('篡改一个字节会导致解包失败或内容不一致（GCM tag 校验）', async () => {
    const owner = identityFromSeed(OWNER_SEED);
    const { pkg } = await packFixture(owner);

    const tampered = JSON.parse(JSON.stringify(pkg)) as typeof pkg;
    const photo = tampered.groups[0].payload.places[0].photos[0];
    const raw = photo.encrypted;
    photo.encrypted = `${raw.slice(0, 4)}${raw[4] === 'A' ? 'B' : 'A'}${raw.slice(5)}`;

    // 篡改密文 → 该地点解密失败被跳过（不会静默返回错内容）
    const result = await unpack(tampered, { privHex: OWNER_SEED });
    const names = result.groups[0].places.map((place) => place.name);
    expect(names).not.toContain('拙政园');
    expect(result.report.errors.some((message) => message.includes('解密失败'))).toBe(true);
    // 签名覆盖的是密文，所以篡改同时会让签名失败
    expect(result.report.signatureOk).toBe(false);
    expect(result.canAccept).toBe(false);
  });
});
