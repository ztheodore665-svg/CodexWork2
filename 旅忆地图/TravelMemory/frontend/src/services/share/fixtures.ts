/** 测试夹具：固定种子、固定 `NOW`、固定一份 2 天 3 地点的旅程。
 *
 * 为什么单独一个文件：pack / unpack / prepare 三个测试文件要用同一份数据，
 * 各写一份的话"改一处夹具"会变成改三处，很快就漂移成三个不同的场景，
 * 到时候"往返一致"这种断言就只是在跟自己比。
 *
 * 种子与 `NOW` 全部写死：密码学的失败往往是间歇性的，可复现的输入是唯一防线。
 */

import { identityFromSeed } from '../crypto/keys';
import type { Identity, Trip, VisibilityState } from '../../types/share';

/** 所有时间相关断言的锚点。与 `experiments/fixtures.ts` 的 NOW 同值。 */
export const NOW = new Date('2026-10-18T12:00:00Z');

/** 四个固定身份。种子是 64 个重复 hex 字符，纯为可读性（不是真实密钥）。 */
export const SEED_A = 'a'.repeat(64);
export const SEED_B = 'b'.repeat(64);
export const SEED_C = 'c'.repeat(64);
export const SEED_D = 'd'.repeat(64);

export const identityA: Identity = identityFromSeed(SEED_A);
export const identityB: Identity = identityFromSeed(SEED_B);
export const identityC: Identity = identityFromSeed(SEED_C);
export const identityD: Identity = identityFromSeed(SEED_D);

/** 照片字节用 UTF-8 文本代替 JPEG：断言时可以直接比对，出错也能一眼看出内容。 */
export function photoBytes(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer;
}

export async function photoText(buffer: ArrayBuffer): Promise<string> {
  return new TextDecoder().decode(new Uint8Array(buffer));
}

/** 2 天 3 地点：
 *  d1（10-01）→ p1 人文/high（2 张，都有配文）、p2 美食/mid（1 张，无配文）
 *  d2（10-02）→ p3 自然/low（1 张，有配文）
 */
export function makeTrip(): Trip {
  return {
    id: 't_00000001',
    title: '关西五日',
    createdAt: '2026-10-10T00:00:00.000Z',
    days: [
      {
        id: 'd_1',
        date: '2026-10-01',
        places: [
          {
            id: 'p_1',
            name: '清水寺',
            label: '上午',
            tag: 'heritage',
            intensity: 'high',
            coverIndex: 0,
            photos: [
              { id: 'ph_1', image: photoBytes('photo-1'), caption: '清晨的空殿' },
              { id: 'ph_2', image: photoBytes('photo-2'), caption: '红叶回廊' },
            ],
          },
          {
            id: 'p_2',
            name: '锦市场',
            tag: 'food',
            intensity: 'mid',
            coverIndex: 0,
            photos: [{ id: 'ph_3', image: photoBytes('photo-3') }],
          },
        ],
      },
      {
        id: 'd_2',
        date: '2026-10-02',
        places: [
          {
            id: 'p_3',
            name: '岚山竹林',
            label: '下午',
            tag: 'nature',
            intensity: 'low',
            coverIndex: 0,
            photos: [{ id: 'ph_4', image: photoBytes('photo-4'), caption: '竹林小径' }],
          },
        ],
      },
    ],
  };
}

/** 全可见的屏蔽状态。`overrides` 里给什么就屏蔽什么（Day → Place → Photo → Caption 四层）。 */
export function makeVisibility(overrides: Partial<VisibilityState> = {}): VisibilityState {
  return {
    id: 't_00000001::g1',
    tripId: 't_00000001',
    groupId: 'g1',
    hiddenDayIds: [],
    hiddenPlaceIds: [],
    hiddenPhotoIds: [],
    hiddenCaptionPhotoIds: [],
    ...overrides,
  };
}
