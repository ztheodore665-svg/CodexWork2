/** 协议对照测试（§7.2.4）：本应用的**复制版**协议与 `frontend/` 里的**原版**必须逐字节一致。
 *
 * ## 为什么可以做到"逐字节"
 *
 * `pack()` 里所有随机性都只来自 `crypto.getRandomValues`（salt / 组密钥 / 每个收件人的
 * KEM 临时私钥 / 每段密文的 IV / groupId），而 Ed25519 签名是确定性的、AES-GCM 在
 * (key, iv) 固定时也是确定性的。所以只要把 `getRandomValues` 换成一个**确定性序列**，
 * 两边在同一输入下就应该产出**完全相同**的 JSON。
 *
 * 这个断言的强度正是它值得写的原因：任何一处"顺手优化"（少取一个随机字节、
 * 换个字段顺序、把 `Object.keys` 换成 `Map` 迭代）都会让它失败。
 *
 * ## 怎么读失败信息
 *
 * 先看第 2、3 条（签名 / 承诺）。它们过了而 JSON 不等，说明差异在密文层
 * （IV 顺序或 salt）；它们本身就挂了，说明输入向量或身份派生有偏差。
 *
 * ## 跨包 import
 *
 * 5 个 `..` 回到仓库根：`src/share/__tests__` → `src/share` → `src` → app → `apps` → 根。
 * 因此这里是**唯一**引用 `frontend/` 的地方，且只在测试里（生产包不含它）。
 * vitest 不做类型检查，`tsconfig.json` 也把任意层级的 `__tests__` 目录排除掉了。
 */

import { afterEach, describe, expect, it } from 'vitest';

import { pack as originalPack } from '../../../../../frontend/src/services/share/pack';
import { unpack as originalUnpack } from '../../../../../frontend/src/services/share/unpack';

import { emptyVisibility } from '../filter';
import { pack as copyPack } from '../pack';
import { unpack as copyUnpack } from '../unpack';
import { identityFromSeed } from '../crypto/keys';
import type { Identity, Trip } from '../../types/share';

/** 固定发送方 / 收件人种子。用 `'11'.repeat(32)` 这种一眼假的向量，避免被误当成真钥匙。 */
const SENDER_SEED = '11'.repeat(32);
const RECIPIENT_SEED = '22'.repeat(32);

const FIXED_NOW = new Date('2026-11-13T00:00:00.000Z');
const FIXED_SHARE_ID = 's_test0001';
const FIXED_DISPLAY_NAME = '测试发送方';
const TRIP_ID = 't_test0001';
const GROUP_ID = 'g1_self';

/* ── 确定性随机 ─────────────────────────────────────────────── */

const realGetRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
let counter = 0;

function installDeterministicRandom(): void {
  Object.defineProperty(globalThis.crypto, 'getRandomValues', {
    configurable: true,
    writable: true,
    value: <T extends ArrayBufferView>(arr: T): T => {
      const bytes = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = (counter++ * 37 + 11) & 0xff;
      }
      return arr;
    },
  });
}

function restoreRandom(): void {
  Object.defineProperty(globalThis.crypto, 'getRandomValues', {
    configurable: true,
    writable: true,
    value: realGetRandomValues,
  });
  counter = 0;
}

afterEach(() => {
  restoreRandom();
});

/* ── 固定向量 ──────────────────────────────────────────────── */

function bytes(...values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

/** 两天 / 两个地点 / 三张照片 / 一个配文。刻意覆盖：多天、多地点、配文有无。 */
function makeTrip(): Trip {
  return {
    id: TRIP_ID,
    title: '测试旅程',
    createdAt: '2026-11-01T00:00:00.000Z',
    days: [
      {
        id: 'd1_test0001',
        date: '2026-11-01',
        places: [
          {
            id: 'p1_test0001',
            name: '西湖',
            label: '上午',
            tag: 'nature',
            intensity: 'high',
            coverIndex: 0,
            photos: [
              { id: 'ph1_test0001', image: bytes(0xff, 0xd8, 0x01, 0x02, 0x03), caption: '断桥残雪' },
              { id: 'ph2_test0001', image: bytes(0xff, 0xd8, 0x04, 0x05) },
            ],
          },
        ],
      },
      {
        id: 'd2_test0001',
        date: '2026-11-02',
        places: [
          {
            id: 'p2_test0001',
            name: '河坊街',
            tag: 'food',
            intensity: 'mid',
            coverIndex: 0,
            photos: [{ id: 'ph3_test0001', image: bytes(0xff, 0xd8, 0x06, 0x07, 0x08, 0x09) }],
          },
        ],
      },
    ],
  };
}

function senderIdentity(): Identity {
  return identityFromSeed(SENDER_SEED);
}

function recipientIdentity(): Identity {
  return identityFromSeed(RECIPIENT_SEED);
}

/** 每次跑之前把随机计数器归零 —— 两边必须从同一条随机序列开始。 */
function packInput() {
  return {
    groups: [
      {
        trip: makeTrip(),
        visibility: emptyVisibility(TRIP_ID, GROUP_ID),
        recipientPubkeys: [recipientIdentity().pubkey],
      },
    ],
    senderIdentity: senderIdentity(),
    displayName: FIXED_DISPLAY_NAME,
    now: FIXED_NOW,
    shareId: FIXED_SHARE_ID,
  };
}

async function runOriginal() {
  counter = 0;
  return originalPack(packInput());
}

async function runCopy() {
  counter = 0;
  return copyPack(packInput());
}

/* ── 测试 ─────────────────────────────────────────────────── */

describe('协议对照：复制版 vs frontend 原版', () => {
  it('同一确定性随机序列下，两侧 pkg 逐字节相同', async () => {
    installDeterministicRandom();
    const original = await runOriginal();
    const copy = await runCopy();

    // 最强断言：整包 JSON 全等。失败时看下面两条定位。
    expect(JSON.stringify(copy.pkg)).toBe(JSON.stringify(original.pkg));

    // 单独钉住签名（确定性，不含随机）
    expect(copy.pkg.from.sig).toBe(original.pkg.from.sig);
    expect(copy.pkg.from.pubkey).toBe(original.pkg.from.pubkey);

    // 单独钉住承诺（含 salt，覆盖随机序列的顺序）
    expect(copy.pkg.commitment.hash).toBe(original.pkg.commitment.hash);

    // 结构层面的关键计数
    expect(copy.pkg.version).toBe('ts-2026.4');
    expect(copy.pkg.groups).toHaveLength(1);
    expect(copy.pkg.groups[0].payload.places).toHaveLength(2);
    expect(copy.pkg.groups[0].payload.places[0].photos).toHaveLength(2);
    expect(copy.pkg.groups[0].wrappedKeys).toHaveLength(1);
    expect(copy.pkg.chain.seq).toBe(0);
    expect(copy.pkg.chain.prevHash).toBe('0'.repeat(64));
    expect(copy.pkg.createdAt).toBe(FIXED_NOW.toISOString());
  });

  it('交叉解包：原版解复制版、复制版解原版，都能解开且签名/承诺通过', async () => {
    installDeterministicRandom();
    const original = await runOriginal();
    const copy = await runCopy();
    const cred = { privHex: RECIPIENT_SEED };

    const originalUnpacksCopy = await originalUnpack(copy.pkg, cred);
    expect(originalUnpacksCopy.groups).toHaveLength(1);
    expect(originalUnpacksCopy.groups[0].places).toHaveLength(2);
    expect(originalUnpacksCopy.report.signatureOk).toBe(true);
    expect(originalUnpacksCopy.report.commitmentOk).toBe(true);
    expect(originalUnpacksCopy.canAccept).toBe(true);

    const copyUnpacksOriginal = await copyUnpack(original.pkg, cred);
    expect(copyUnpacksOriginal.groups).toHaveLength(1);
    expect(copyUnpacksOriginal.groups[0].places).toHaveLength(2);
    expect(copyUnpacksOriginal.report.signatureOk).toBe(true);
    expect(copyUnpacksOriginal.report.commitmentOk).toBe(true);

    // 解出来的内容也要一致：地点名、日期、标签、照片字节
    const a = originalUnpacksCopy.groups[0].places;
    const b = copyUnpacksOriginal.groups[0].places;
    expect(a.map((place) => place.name)).toEqual(b.map((place) => place.name));
    expect(a.map((place) => place.date)).toEqual(b.map((place) => place.date));
    expect(a[0].photos[0].caption).toBe('断桥残雪');
    expect(b[0].photos[0].caption).toBe('断桥残雪');
    expect(Array.from(new Uint8Array(a[0].photos[0].image))).toEqual(
      Array.from(new Uint8Array(b[0].photos[0].image)),
    );
  });

  it('随机源恢复后两侧仍能各自独立打包（桩没有污染后续调用）', async () => {
    installDeterministicRandom();
    await runOriginal();
    restoreRandom();

    const copy = await copyPack(packInput());
    const again = await copyPack(packInput());
    // 真实随机下两次打包的 salt 必须不同（这是"盐真的在工作"的最小证据）
    expect(again.pkg.groups[0].payload.salt).not.toBe(copy.pkg.groups[0].payload.salt);
  });
});
