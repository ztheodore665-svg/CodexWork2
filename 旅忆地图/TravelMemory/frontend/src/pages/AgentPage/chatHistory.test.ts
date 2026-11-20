/** 会话历史的纯逻辑测试（Node 环境，注入假存储，无 skip）。 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
  MAX_CONVERSATIONS,
  STORAGE_KEY,
  appendMessage,
  conversationTitle,
  deleteConversation,
  loadConversations,
  newConversation,
  saveConversations,
  setConversationStore,
  toStoredMessages,
  upsertConversation,
  type ChatConversation,
} from './chatHistory';

/** 一个内存版 Storage。`Pick<Storage,...>` 的三个方法都要有。 */
function memoryStore() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
    dump: () => map,
  };
}

const NOW = new Date('2026-10-25T10:00:00.000Z');

beforeEach(() => {
  setConversationStore(memoryStore());
});

describe('conversationTitle', () => {
  it('取第一条用户消息', () => {
    expect(
      conversationTitle([
        { id: '1', role: 'assistant', content: '你好' },
        { id: '2', role: 'user', content: '我去过哪些古镇？' },
      ]),
    ).toBe('我去过哪些古镇？');
  });

  it('空会话给「新对话」而不是空串', () => {
    expect(conversationTitle([])).toBe('新对话');
    expect(conversationTitle([{ id: '1', role: 'user', content: '   ' }])).toBe('新对话');
  });

  it('超长截断并加省略号', () => {
    const long = '啊'.repeat(50);
    const title = conversationTitle([{ id: '1', role: 'user', content: long }], 10);
    expect(title).toBe(`${'啊'.repeat(10)}…`);
    expect(title.length).toBe(11);
  });
});

describe('newConversation', () => {
  it('生成带 id 与空消息的会话', () => {
    const conv = newConversation(NOW);
    expect(conv.id).toMatch(/^c-/);
    expect(conv.messages).toEqual([]);
    expect(conv.title).toBe('新对话');
    expect(conv.createdAt).toBe(NOW.toISOString());
  });

  it('每次 id 都不同', () => {
    expect(newConversation(NOW).id).not.toBe(newConversation(NOW).id);
  });
});

describe('appendMessage', () => {
  it('追加消息并推进 updatedAt', () => {
    const conv = newConversation(NOW);
    const next = appendMessage(conv, { id: 'u1', role: 'user', content: '问' }, NOW);
    expect(next.messages).toHaveLength(1);
    expect(next.updatedAt).toBe(NOW.toISOString());
  });

  it('首条用户消息自动成为标题', () => {
    let conv = newConversation(NOW);
    conv = appendMessage(conv, { id: 'u1', role: 'user', content: '我去年秋天去过哪些古镇' }, NOW);
    expect(conv.title).toBe('我去年秋天去过哪些古镇');
  });

  it('标题已定就不再改', () => {
    let conv = newConversation(NOW);
    conv = appendMessage(conv, { id: 'u1', role: 'user', content: '第一个问题' }, NOW);
    conv = appendMessage(conv, { id: 'u2', role: 'user', content: '第二个问题' }, NOW);
    expect(conv.title).toBe('第一个问题');
  });

  it('不改入参（纯函数）', () => {
    const conv = newConversation(NOW);
    const before = JSON.stringify(conv);
    appendMessage(conv, { id: 'u1', role: 'user', content: 'x' }, NOW);
    expect(JSON.stringify(conv)).toBe(before);
  });
});

describe('toStoredMessages', () => {
  it('丢掉空白消息', () => {
    const stored = toStoredMessages([
      { id: 'a', role: 'user', content: '有问题' },
      { id: 'b', role: 'assistant', content: '   ' },
      { id: 'c', role: 'assistant', content: '有答案' },
    ]);
    expect(stored).toEqual([
      { id: 'a', role: 'user', content: '有问题' },
      { id: 'c', role: 'assistant', content: '有答案' },
    ]);
  });
});

describe('持久化往返', () => {
  it('存进去能原样读出来', () => {
    const conv = newConversation(NOW);
    const withMsg = appendMessage(conv, { id: 'u1', role: 'user', content: '问题' }, NOW);
    saveConversations([withMsg]);
    expect(loadConversations()).toEqual([withMsg]);
  });

  it('读出来按 updatedAt 倒序', () => {
    const older = { ...newConversation(NOW), id: 'old', updatedAt: '2026-10-25T09:00:00.000Z' };
    const newer = { ...newConversation(NOW), id: 'new', updatedAt: '2026-10-25T11:00:00.000Z' };
    saveConversations([older, newer]);
    expect(loadConversations().map((c) => c.id)).toEqual(['new', 'old']);
  });

  it('超过 MAX_CONVERSATIONS 时裁到上限', () => {
    const many: ChatConversation[] = Array.from({ length: MAX_CONVERSATIONS + 10 }, (_, i) => ({
      ...newConversation(NOW),
      id: `c${i}`,
      updatedAt: `2026-10-25T10:00:${String(i % 60).padStart(2, '0')}.000Z`,
    }));
    saveConversations(many);
    expect(loadConversations()).toHaveLength(MAX_CONVERSATIONS);
  });

  it('存储损坏（非数组 / 坏 JSON）时当没有历史，不抛错', () => {
    const store = memoryStore();
    store.setItem(STORAGE_KEY, '"我不是数组"');
    setConversationStore(store);
    expect(loadConversations()).toEqual([]);

    store.setItem(STORAGE_KEY, '{坏 JSON');
    expect(loadConversations()).toEqual([]);
  });

  it('数组里混进脏元素时只保留合法的', () => {
    const store = memoryStore();
    store.setItem(
      STORAGE_KEY,
      JSON.stringify([
        { id: 'ok', title: 't', createdAt: 'c', updatedAt: 'u', messages: [] },
        { id: '缺字段' },
        null,
        '字符串',
      ]),
    );
    setConversationStore(store);
    const loaded = loadConversations();
    expect(loaded).toHaveLength(1);
    expect(loaded[0].id).toBe('ok');
  });

  it('没有存储时不抛错，返回空列表', () => {
    setConversationStore(null);
    // 注意：Node 环境下没有 localStorage，defaultStore() 返回 null
    expect(loadConversations()).toEqual([]);
  });
});

describe('upsertConversation / deleteConversation', () => {
  const base = newConversation(NOW);

  it('upsert 把新会话放到最前', () => {
    const list = upsertConversation([base], { ...base, id: 'other' });
    expect(list[0].id).toBe('other');
    expect(list).toHaveLength(2);
  });

  it('同 id upsert 是覆盖而不是新增', () => {
    const updated = { ...base, title: '改过的标题' };
    const list = upsertConversation([base], updated);
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe('改过的标题');
  });

  it('delete 移除指定会话', () => {
    const other = { ...base, id: 'other' };
    expect(deleteConversation([base, other], base.id).map((c) => c.id)).toEqual(['other']);
  });

  it('delete 不存在的 id 时原样返回', () => {
    expect(deleteConversation([base], 'nope')).toHaveLength(1);
  });
});
