/** `tripScope`：行程归属判定与发件人分组（W15-5）。
 *
 * 这一层决定"哪些行程是我的、哪些是朋友发来的"，判错的后果是用户删掉别人的行程
 * 或者把自己的行程当成别人的锁起来 —— 所以每种标题形态都钉一条。
 */

import { describe, expect, it } from 'vitest';

import { groupFriendsBySender, senderOfTitle, splitTrips } from './tripScope';

const trip = (title: string) => ({ title });

describe('senderOfTitle', () => {
  it('普通标题：取出「的信物」前的名字（含 ` · 地点` 后缀）', () => {
    expect(senderOfTitle('来自 阿岚 的信物 · 西湖')).toBe('阿岚');
    // 旧格式（W15-4 早期，没有地点后缀）也要能认
    expect(senderOfTitle('来自 阿岚 的信物')).toBe('阿岚');
  });

  it('自己的行程（没有前缀）→ null', () => {
    expect(senderOfTitle('洛阳两日')).toBeNull();
    expect(senderOfTitle('杭州两日 · 西湖')).toBeNull();
    expect(senderOfTitle('')).toBeNull();
  });

  it('名字里带空格：只截到第一个「 的信物」之前', () => {
    expect(senderOfTitle('来自 张 三 的信物 · 灵隐寺')).toBe('张 三');
    expect(senderOfTitle('来自 A. B. Chen 的信物 · 西湖')).toBe('A. B. Chen');
    expect(senderOfTitle('  来自 小旅 的信物 · 西湖  ')).toBe('小旅');
  });

  it('名字为空 / 不是"信物"结尾 → null（不制造空分组）', () => {
    expect(senderOfTitle('来自  的信物')).toBeNull();
    expect(senderOfTitle('来自 的信物')).toBeNull();
    // 「信物们」不是约定的结尾：不能被误判成朋友发来的
    expect(senderOfTitle('来自 阿岚 的信物们')).toBeNull();
  });
});

describe('splitTrips', () => {
  it('按前缀拆成两组，各自保持原顺序', () => {
    const trips = [
      trip('洛阳两日'),
      trip('来自 阿岚 的信物 · 西湖'),
      trip('杭州两日'),
      trip('来自 老周 的信物 · 鼓浪屿'),
    ];
    const { mine, friends } = splitTrips(trips);

    expect(mine.map((item) => item.title)).toEqual(['洛阳两日', '杭州两日']);
    expect(friends.map((item) => item.title)).toEqual([
      '来自 阿岚 的信物 · 西湖',
      '来自 老周 的信物 · 鼓浪屿',
    ]);
  });

  it('空数组 / 全部是朋友的', () => {
    expect(splitTrips([])).toEqual({ mine: [], friends: [] });

    const onlyFriends = splitTrips([trip('来自 阿岚 的信物 · 西湖')]);
    expect(onlyFriends.mine).toEqual([]);
    expect(onlyFriends.friends).toHaveLength(1);
  });

  it('对象引用不变（不复制行程，避免选中态/回放丢引用）', () => {
    const only = trip('洛阳两日');
    expect(splitTrips([only]).mine[0]).toBe(only);
  });
});

describe('groupFriendsBySender', () => {
  it('同发件人多条进同一组，组顺序 = 首次出现顺序，组内保持原顺序', () => {
    const trips = [
      trip('来自 老周 的信物 · 鼓浪屿'),
      trip('来自 阿岚 的信物 · 西湖'),
      trip('来自 阿岚 的信物 · 灵隐寺'),
      trip('来自 老周 的信物 · 曾厝垵'),
    ];
    const groups = groupFriendsBySender(trips);

    expect(groups.map((group) => group.sender)).toEqual(['老周', '阿岚']);
    expect(groups[0].trips.map((item) => item.title)).toEqual([
      '来自 老周 的信物 · 鼓浪屿',
      '来自 老周 的信物 · 曾厝垵',
    ]);
    expect(groups[1].trips).toHaveLength(2);
  });

  it('没有前缀的行程不归任何组（分组语义是"谁发给我的"）', () => {
    const groups = groupFriendsBySender([trip('洛阳两日'), trip('来自 阿岚 的信物 · 西湖')]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sender).toBe('阿岚');
  });

  it('空数组 → 空分组', () => {
    expect(groupFriendsBySender([])).toEqual([]);
  });
});
