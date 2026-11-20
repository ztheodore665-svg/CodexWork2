/** 缩略图缓存测试。内存版可以完整覆盖；IndexedDB 版在 Node 下没有 indexedDB，
 *  这里退化的路径 createIndexedDbThumbnailStore 内部会 console.warn + 降级。 */

import { describe, expect, it, vi } from 'vitest';
import type { ThumbnailStore } from '../../types/native';
import {
  createIndexedDbThumbnailStore,
  createMemoryThumbnailStore,
  createNativeThumbnailStore,
} from './thumbnailStore';

/** ArrayBuffer 在 TS 里的类型是 ArrayBufferLike 的子集，直接传 TextEncoder 的 buffer
 *  会类型不一致，所以统一经这里拷一份真正的 ArrayBuffer。 */
function toBuffer(text: string): ArrayBuffer {
  const buffer = new ArrayBuffer(text.length);
  new Uint8Array(buffer).set([...text].map((c) => c.charCodeAt(0)));
  return buffer;
}

function toText(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((n) => String.fromCharCode(n)).join('');
}

async function expectNotReady(store: ThumbnailStore): Promise<void> {
  await expect(store.get('p1', 256)).rejects.toThrow(/未接入/);
  await expect(store.put('p1', 256, toBuffer('x'))).rejects.toThrow(/未接入/);
  await expect(store.clear()).rejects.toThrow(/未接入/);
}

describe('createMemoryThumbnailStore', () => {
  it('put → get 往返内容一致', async () => {
    const store = createMemoryThumbnailStore();
    expect(await store.get('p1', 256)).toBeNull();

    await store.put('p1', 256, toBuffer('hello'));
    const hit = await store.get('p1', 256);
    expect(hit).not.toBeNull();
    expect(toText(hit as ArrayBuffer)).toBe('hello');
  });

  it('同一 assetId 的不同 maxEdge 互不覆盖', async () => {
    const store = createMemoryThumbnailStore();
    await store.put('p1', 256, toBuffer('big'));
    await store.put('p1', 128, toBuffer('small'));

    expect(toText((await store.get('p1', 256)) as ArrayBuffer)).toBe('big');
    expect(toText((await store.get('p1', 128)) as ArrayBuffer)).toBe('small');
    expect(store.sizeHint).toBe(2);
  });

  it('sizeHint 反映当前条目数', async () => {
    const store = createMemoryThumbnailStore();
    expect(store.sizeHint).toBe(0);
    await store.put('p1', 256, toBuffer('a'));
    expect(store.sizeHint).toBe(1);
    await store.put('p1', 256, toBuffer('a')); // 覆盖同一 key，不新增
    expect(store.sizeHint).toBe(1);
    await store.put('p2', 256, toBuffer('b'));
    expect(store.sizeHint).toBe(2);
  });

  it('clear 清空所有条目', async () => {
    const store = createMemoryThumbnailStore();
    await store.put('p1', 256, toBuffer('a'));
    await store.put('p2', 128, toBuffer('b'));
    await store.clear();

    expect(store.sizeHint).toBe(0);
    expect(await store.get('p1', 256)).toBeNull();
    expect(await store.get('p2', 128)).toBeNull();
  });

  it('LRU 淘汰：maxEntries=2 时放入第 3 条，最旧的那条被挤掉', async () => {
    const store = createMemoryThumbnailStore(2);
    await store.put('a', 256, toBuffer('A'));
    await store.put('b', 256, toBuffer('B'));
    await store.put('c', 256, toBuffer('C'));

    expect(await store.get('a', 256)).toBeNull();
    expect(toText((await store.get('b', 256)) as ArrayBuffer)).toBe('B');
    expect(toText((await store.get('c', 256)) as ArrayBuffer)).toBe('C');
    expect(store.sizeHint).toBe(2);
  });

  it('get 命中会提升热度，被访问过的条目不会被下一次插入挤掉', async () => {
    const store = createMemoryThumbnailStore(2);
    await store.put('a', 256, toBuffer('A'));
    await store.put('b', 256, toBuffer('B'));
    await store.get('a', 256); // a 变成最近使用
    await store.put('c', 256, toBuffer('C'));

    expect(await store.get('b', 256)).toBeNull();
    expect(toText((await store.get('a', 256)) as ArrayBuffer)).toBe('A');
  });

  it('maxEntries 退化到 1 时仍能工作（至少要留得住一条）', async () => {
    const store = createMemoryThumbnailStore(1);
    await store.put('a', 256, toBuffer('A'));
    await store.put('b', 256, toBuffer('B'));
    expect(store.sizeHint).toBe(1);
    expect(toText((await store.get('b', 256)) as ArrayBuffer)).toBe('B');
  });
});

describe('createNativeThumbnailStore', () => {
  it('三个方法都抛「未接入」明确的错误', async () => {
    await expectNotReady(createNativeThumbnailStore());
  });

  it('sizeHint 读起来不炸，返回 0', () => {
    expect(createNativeThumbnailStore().sizeHint).toBe(0);
  });
});

describe('createIndexedDbThumbnailStore', () => {
  it('无 IndexedDB 时降级为内存实现并给出告警', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    // Node 环境没有 indexedDB，走的正是降级分支
    const store = createIndexedDbThumbnailStore('travelmemory-thumbs-test');
    expect(warn).toHaveBeenCalled();

    await store.put('p1', 256, toBuffer('abc'));
    expect(toText((await store.get('p1', 256)) as ArrayBuffer)).toBe('abc');
    expect(store.sizeHint).toBe(1);
    await store.clear();
    expect(store.sizeHint).toBe(0);

    warn.mockRestore();
  });
});
