/** 好友通讯录单测。
 *
 * 重点：四条录入校验必须给出人话错误（UI 直接把文案喂给 toast），
 * 以及"存储坏了不能连坐好数据"。
 * 假 storage 用内存 Map，不依赖任何浏览器 API —— Node 环境下没有 `localStorage`。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { addFriend, clearFriends, listFriends, removeFriend, setFriendsStorage } from './friends';

const OWN = '0'.repeat(64);
const ALICE = 'a'.repeat(64);
const BOB = 'b'.repeat(64);
const CAROL = 'c'.repeat(64);

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    _map: map,
  };
}

afterEach(() => {
  setFriendsStorage(null);
  vi.useRealTimers();
});

describe('listFriends', () => {
  it('空表 → 空数组', () => {
    setFriendsStorage(fakeStorage());
    expect(listFriends()).toEqual([]);
  });

  it('完全没有任何存储可用（Node / 隐私模式）→ 空数组而不是抛错', () => {
    setFriendsStorage(null);
    expect(listFriends()).toEqual([]);
  });
});

describe('addFriend', () => {
  it('添加成功 → 字段齐全且幂等 ID 来自公钥前 12 位', () => {
    const store = fakeStorage();
    setFriendsStorage(store);
    const friend = addFriend({ pubkey: `ed25519:${ALICE}`, name: 'Alice' }, OWN);

    expect(friend.id).toBe(ALICE.slice(0, 12));
    expect(friend.pubHex).toBe(ALICE);
    expect(friend.pubkey).toBe(`ed25519:${ALICE}`);
    expect(friend.name).toBe('Alice');
    expect(friend.addedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(listFriends()).toHaveLength(1);
    expect(store._map.has('travelmemory.friends.v1')).toBe(true);
  });

  it('不带 ed25519: 前缀的裸公钥也收', () => {
    setFriendsStorage(fakeStorage());
    expect(addFriend({ pubkey: ALICE, name: 'Alice' }, OWN).pubkey).toBe(`ed25519:${ALICE}`);
  });

  it('大写公钥 / 首尾空白 → 归一化成小写 bare hex', () => {
    setFriendsStorage(fakeStorage());
    const friend = addFriend({ pubkey: `  ${ALICE.toUpperCase()}  `, name: 'Alice' }, OWN);
    expect(friend.pubHex).toBe(ALICE);
    expect(friend.id).toBe(ALICE.slice(0, 12));
  });

  it('④ 校验：空公钥 → 请输入对方的公钥', () => {
    setFriendsStorage(fakeStorage());
    expect(() => addFriend({ pubkey: '', name: 'Alice' }, OWN)).toThrow('请输入对方的公钥');
    expect(() => addFriend({ pubkey: '   ', name: 'Alice' }, OWN)).toThrow('请输入对方的公钥');
    expect(listFriends()).toHaveLength(0);
  });

  it('④ 校验：长度不对 / 非 hex → 公钥格式不正确', () => {
    setFriendsStorage(fakeStorage());
    const message = '公钥格式不正确：应为 64 位十六进制，可带 ed25519: 前缀';
    expect(() => addFriend({ pubkey: 'ed25519:abc123', name: 'X' }, OWN)).toThrow(message);
    expect(() => addFriend({ pubkey: 'z'.repeat(64), name: 'X' }, OWN)).toThrow(message);
    expect(() => addFriend({ pubkey: `${ALICE}ff`, name: 'X' }, OWN)).toThrow(message);
    expect(listFriends()).toHaveLength(0);
  });

  it('④ 校验：自己的公钥（带前缀也认）→ 不能添加自己', () => {
    setFriendsStorage(fakeStorage());
    const message = '不能添加自己：自己默认就在收件人列表里';
    expect(() => addFriend({ pubkey: OWN, name: '我' }, OWN)).toThrow(message);
    expect(() => addFriend({ pubkey: `ed25519:${OWN}`, name: '我' }, OWN)).toThrow(message);
    expect(listFriends()).toHaveLength(0);
  });

  it('④ 校验：重复添加同一个公钥 → 该好友已在列表中', () => {
    setFriendsStorage(fakeStorage());
    addFriend({ pubkey: ALICE, name: 'Alice' }, OWN);
    expect(() => addFriend({ pubkey: `ed25519:${ALICE}`, name: 'Alice2' }, OWN)).toThrow(
      '该好友已在列表中',
    );
    expect(listFriends()).toHaveLength(1);
  });

  it('备注名为空 / 纯空白 → 默认名「未命名好友-<后 4 位>」', () => {
    setFriendsStorage(fakeStorage());
    expect(addFriend({ pubkey: ALICE, name: '' }, OWN).name).toBe(`未命名好友-${ALICE.slice(-4)}`);
    expect(addFriend({ pubkey: BOB, name: '   ' }, OWN).name).toBe(`未命名好友-${BOB.slice(-4)}`);
  });

  it('listFriends 按 addedAt 升序（后加的、时间更早的排到前面）', () => {
    vi.useFakeTimers();
    const store = fakeStorage();
    setFriendsStorage(store);
    vi.setSystemTime(new Date('2026-10-02T00:00:00.000Z'));
    addFriend({ pubkey: ALICE, name: '晚加' }, OWN);
    vi.setSystemTime(new Date('2026-10-01T00:00:00.000Z'));
    addFriend({ pubkey: BOB, name: '早加' }, OWN);

    expect(listFriends().map((friend) => friend.name)).toEqual(['早加', '晚加']);
  });
});

describe('removeFriend', () => {
  it('删除存在的好友 → true，且持久化', () => {
    setFriendsStorage(fakeStorage());
    const friend = addFriend({ pubkey: ALICE, name: 'Alice' }, OWN);
    expect(removeFriend(friend.id)).toBe(true);
    expect(listFriends()).toEqual([]);
  });

  it('删除不存在的好友 → false', () => {
    setFriendsStorage(fakeStorage());
    addFriend({ pubkey: ALICE, name: 'Alice' }, OWN);
    expect(removeFriend('ffffffffffff')).toBe(false);
    expect(listFriends()).toHaveLength(1);
  });
});

describe('存储损坏', () => {
  it('JSON 解析失败 → 当空表处理，不抛', () => {
    setFriendsStorage(fakeStorage({ 'travelmemory.friends.v1': 'not-json{{' }));
    expect(listFriends()).toEqual([]);
  });

  it('存的不是数组 → 当空表处理', () => {
    setFriendsStorage(fakeStorage({ 'travelmemory.friends.v1': JSON.stringify({ nope: 1 }) }));
    expect(listFriends()).toEqual([]);
  });

  it('数组里混着坏元素 → 只丢坏的，好的全留下', () => {
    const good: Record<string, unknown> = {
      id: ALICE.slice(0, 12),
      name: 'Alice',
      pubkey: `ed25519:${ALICE}`,
      pubHex: ALICE,
      addedAt: '2026-10-01T00:00:00.000Z',
    };
    const broken = [
      good,
      null, // 不是对象
      'just-a-string', // 不是对象
      { name: '没有 pubHex' }, // 缺唯一不可妥协的字段
      { pubHex: 'abc123' }, // pubHex 长度不对
      { pubHex: 'z'.repeat(64) }, // 非 hex
      { pubHex: BOB, addedAt: '2026-09-30T00:00:00.000Z' }, // 缺 name/id/pubkey → 补齐而不是丢
    ];
    setFriendsStorage(fakeStorage({ 'travelmemory.friends.v1': JSON.stringify(broken) }));

    const list = listFriends();
    expect(list).toHaveLength(2);
    // 缺字段的那条被补齐：id/name/pubkey 都能自己算出来
    const repaired = list[0];
    expect(repaired.pubHex).toBe(BOB);
    expect(repaired.pubkey).toBe(`ed25519:${BOB}`);
    expect(repaired.id).toBe(BOB.slice(0, 12));
    expect(repaired.name).toBe(`未命名好友-${BOB.slice(-4)}`);
    expect(list[1].name).toBe('Alice');
  });
});

describe('clearFriends', () => {
  it('清空通讯录', () => {
    const store = fakeStorage();
    setFriendsStorage(store);
    addFriend({ pubkey: ALICE, name: 'Alice' }, OWN);
    addFriend({ pubkey: CAROL, name: 'Carol' }, OWN);
    clearFriends();
    expect(listFriends()).toEqual([]);
    expect(store._map.has('travelmemory.friends.v1')).toBe(false);
  });
});

describe('隐私模式（存储读写全抛异常）', () => {
  it('功能不中断：读得到空表、写也不炸', () => {
    const broken = {
      getItem() {
        throw new Error('SecurityError');
      },
      setItem() {
        throw new Error('SecurityError');
      },
      removeItem() {
        throw new Error('SecurityError');
      },
    };
    setFriendsStorage(broken);

    expect(listFriends()).toEqual([]);
    // 退化成内存通讯录：拿到了结果，只是刷新即丢
    expect(addFriend({ pubkey: ALICE, name: 'Alice' }, OWN).pubHex).toBe(ALICE);
    expect(() => removeFriend(ALICE.slice(0, 12))).not.toThrow();
    expect(() => clearFriends()).not.toThrow();
  });
});
