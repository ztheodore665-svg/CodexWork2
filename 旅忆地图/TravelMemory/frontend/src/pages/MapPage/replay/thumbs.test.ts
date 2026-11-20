/** 回放层缩略图批量加载的行为契约。
 *
 *  重点不是「能不能取到」，而是**取不到时能不能安静地退场**：
 *  演示数据从没导入过照片、用户换过浏览器、IndexedDB 写入被拒，这三种都是正常状态，
 *  卡片应该退回渐变占位，而不是白屏、报错、或者让整批图一起消失。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const getUrl = vi.fn<(id: string) => Promise<string | null>>();
vi.mock('../PhotoThumb', () => ({
  getOrCreateThumbUrl: (id: string) => getUrl(id),
}));

import { loadReplayThumbs } from './thumbs';

beforeEach(() => {
  getUrl.mockReset();
});

describe('loadReplayThumbs', () => {
  it('空列表不发起任何读取', async () => {
    const map = await loadReplayThumbs([]);
    expect(map.size).toBe(0);
    expect(getUrl).not.toHaveBeenCalled();
  });

  it('全部取不到时返回空 Map（卡片走 gradient 兜底，不抛异常）', async () => {
    getUrl.mockResolvedValue(null);
    const map = await loadReplayThumbs(['a', 'b', 'c']);
    expect(map.size).toBe(0);
    expect(getUrl).toHaveBeenCalledTimes(3);
  });

  it('只把取到的放进 Map，键是 photoId', async () => {
    getUrl.mockImplementation(async (id) => (id === 'a' ? 'blob:a' : null));
    const map = await loadReplayThumbs(['a', 'b', 'c']);
    expect([...map.entries()]).toEqual([['a', 'blob:a']]);
  });

  it('单张读取抛异常时被吞掉，其余照片照常显示', async () => {
    getUrl.mockImplementation(async (id) => {
      if (id === 'b') throw new Error('IndexedDB 挂了');
      return `blob:${id}`;
    });
    const map = await loadReplayThumbs(['a', 'b', 'c']);
    expect([...map.keys()].sort()).toEqual(['a', 'c']);
  });
});
