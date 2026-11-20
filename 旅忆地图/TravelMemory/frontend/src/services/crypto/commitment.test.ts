/** 组织承诺：排序不敏感性 + 对 tag/intensity/盐的敏感性。 */

import { describe, expect, it } from 'vitest';

import { groupPlacesCommitmentValue, stripAlgo, type CommitmentGroupPart } from './commitment';

function part(overrides: Partial<CommitmentGroupPart> = {}): CommitmentGroupPart {
  return {
    groupId: 'g1_ab12cd',
    salt: 'c2FsdA==',
    places: [
      { placeId: 'p_1', tag: 'heritage', intensity: 'high' },
      { placeId: 'p_2', tag: 'food', intensity: 'mid' },
    ],
    ...overrides,
  };
}

describe('stripAlgo', () => {
  it('剥掉 sha256: 前缀；没有前缀时原样返回', () => {
    expect(stripAlgo('sha256:' + 'a'.repeat(64))).toBe('a'.repeat(64));
    expect(stripAlgo('a'.repeat(64))).toBe('a'.repeat(64));
  });
});

describe('groupPlacesCommitmentValue', () => {
  it('place 顺序变化不影响结果（因为承诺内部按 placeId 排序）', () => {
    const a = groupPlacesCommitmentValue([part()]);
    const b = groupPlacesCommitmentValue([
      part({
        places: [
          { placeId: 'p_2', tag: 'food', intensity: 'mid' },
          { placeId: 'p_1', tag: 'heritage', intensity: 'high' },
        ],
      }),
    ]);
    expect(a).toBe(b);
  });

  it('组顺序变化不影响结果（组之间按 groupId 排序）', () => {
    const g1 = part({ groupId: 'g1_aaaaaa' });
    const g2 = part({ groupId: 'g2_bbbbbb' });
    expect(groupPlacesCommitmentValue([g1, g2])).toBe(groupPlacesCommitmentValue([g2, g1]));
  });

  it('tag 变化 → 承诺变化', () => {
    const before = groupPlacesCommitmentValue([part()]);
    const after = groupPlacesCommitmentValue([
      part({
        places: [
          { placeId: 'p_1', tag: 'nature', intensity: 'high' },
          { placeId: 'p_2', tag: 'food', intensity: 'mid' },
        ],
      }),
    ]);
    expect(after).not.toBe(before);
  });

  it('intensity 变化 → 承诺变化', () => {
    const before = groupPlacesCommitmentValue([part()]);
    const after = groupPlacesCommitmentValue([
      part({
        places: [
          { placeId: 'p_1', tag: 'heritage', intensity: 'low' },
          { placeId: 'p_2', tag: 'food', intensity: 'mid' },
        ],
      }),
    ]);
    expect(after).not.toBe(before);
  });

  it('盐变化 → 承诺变化（同一批内容打两次包得到不同承诺）', () => {
    expect(groupPlacesCommitmentValue([part({ salt: 'AAAA' })])).not.toBe(
      groupPlacesCommitmentValue([part({ salt: 'BBBB' })]),
    );
  });

  it('placeId 集合变化 → 承诺变化（屏蔽 = 不打包，会体现在承诺里）', () => {
    const before = groupPlacesCommitmentValue([part()]);
    const after = groupPlacesCommitmentValue([
      part({ places: [{ placeId: 'p_1', tag: 'heritage', intensity: 'high' }] }),
    ]);
    expect(after).not.toBe(before);
  });

  it('输出形如 sha256:<64hex>', () => {
    expect(groupPlacesCommitmentValue([part()])).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
});
