/** 地图视野与证据定位的纯逻辑测试（Node 环境，无 skip）。 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_MAP_CENTER,
  DEFAULT_MAP_ZOOM,
  dedupePoints,
  findPointForEvidence,
  parseEvidenceRef,
} from './mapView';

describe('默认视野', () => {
  it('中心点是中国地理中心附近 [104.195, 35.861]', () => {
    expect(DEFAULT_MAP_CENTER).toEqual([104.195, 35.861]);
  });

  it('缩放级别是 4（能看全貌又能点开城市）', () => {
    expect(DEFAULT_MAP_ZOOM).toBe(4);
  });
});

describe('parseEvidenceRef', () => {
  it('解析 place/p10#0', () => {
    expect(parseEvidenceRef('place/p10#0')).toEqual({
      sourceType: 'place',
      sourceId: 'p10',
      chunkIndex: 0,
    });
  });

  it('解析带 UUID 的 photo ref', () => {
    expect(parseEvidenceRef('photo/3f2a9c11-aaaa-bbbb-cccc-1234567890ab#2')).toEqual({
      sourceType: 'photo',
      sourceId: '3f2a9c11-aaaa-bbbb-cccc-1234567890ab',
      chunkIndex: 2,
    });
  });

  it('没有 #chunk 时 chunkIndex 为 null', () => {
    expect(parseEvidenceRef('trip/t1')).toEqual({
      sourceType: 'trip',
      sourceId: 't1',
      chunkIndex: null,
    });
  });

  it('格式不符一律返回 null，不抛错', () => {
    expect(parseEvidenceRef('')).toBeNull();
    expect(parseEvidenceRef('noslash')).toBeNull();
    expect(parseEvidenceRef('/only-slash')).toBeNull();
    expect(parseEvidenceRef('type/')).toBeNull();
  });
});

describe('findPointForEvidence', () => {
  const POINTS = [
    { id: 'p10', name: '龙门石窟' },
    { id: 'abc-123', name: '老君山' },
    { id: 'trip-9:item-7', name: '洛阳博物馆' },
  ];

  it('id 全等命中', () => {
    expect(findPointForEvidence(POINTS, 'place/p10#0')?.id).toBe('p10');
  });

  it('id 以 sourceId 结尾时命中（兼容复合 id）', () => {
    expect(findPointForEvidence(POINTS, 'place/item-7#0')?.id).toBe('trip-9:item-7');
  });

  it('name 相等也能命中', () => {
    expect(findPointForEvidence(POINTS, 'place/abc-123#0')?.id).toBe('abc-123');
  });

  it('没有坐标通路的证据返回 null（不抛错）', () => {
    expect(findPointForEvidence(POINTS, 'place/不存在#0')).toBeNull();
    expect(findPointForEvidence(POINTS, '乱码')).toBeNull();
  });

  it('空点列表返回 null', () => {
    expect(findPointForEvidence([], 'place/p10#0')).toBeNull();
  });
});

describe('dedupePoints', () => {
  it('同 id 只留第一个', () => {
    const out = dedupePoints([
      { id: 'a', name: '一' },
      { id: 'b', name: '二' },
      { id: 'a', name: '一的副本' },
    ]);
    expect(out.map((p) => p.id)).toEqual(['a', 'b']);
    expect(out[0].name).toBe('一');
  });

  it('没有重复时原样返回', () => {
    const input = [{ id: 'x' }, { id: 'y' }];
    expect(dedupePoints(input)).toEqual(input);
  });

  it('空列表返回空列表', () => {
    expect(dedupePoints([])).toEqual([]);
  });
});
