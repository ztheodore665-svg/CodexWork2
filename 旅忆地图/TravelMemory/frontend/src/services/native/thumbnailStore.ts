/** 缩略图缓存：内存 LRU + IndexedDB 持久化 + Native 桩。
 *
 * ## 为什么 key 要带上 maxEdge
 *
 * 同一张照片会被三种地方用：列表格子（256）、地图气泡（128）、详情页（512）。
 * 如果只按 assetId 存，谁后写谁赢，列表页会拿到一张被放大到糊的 128 图。
 * 把它编进 key（`${assetId}:${maxEdge}`）是最省事也最不容易出错的做法。
 *
 * ## 为什么内存版要 LRU 而不是无限 Map
 *
 * 一次旅行选几百张照片很正常，每张 256px JPEG 大约 8~20KB。看起来不多，
 * 但这是**进程内永驻**的内存，加上 React 列表同时持有 blob URL，移动端 Chrome
 * 在 300 张上下就会开始回收标签页。设上限（默认 500）比事后调内存泄漏划算。
 */

import type { ThumbnailStore } from '../../types/native';

function cacheKey(assetId: string, maxEdge: number): string {
  return `${assetId}:${maxEdge}`;
}

/** 内存 LRU 缓存。
 *
 * 借助 `Map` 的**插入顺序**语义实现：命中时先 delete 再 set，把它挪到最末（最新），
 * 淘汰时删掉迭代器的第一个 key（最旧）。比维护链表 + 时间戳简单得多，
 * 而且天然不需要 `Date.now()`------纯函数层不碰时钟，测试才写得出确定性断言。
 */
export function createMemoryThumbnailStore(maxEntries = 500): ThumbnailStore {
  const limit = Math.max(1, Math.trunc(maxEntries));
  const cache = new Map<string, ArrayBuffer>();

  function touch(key: string, bytes: ArrayBuffer): void {
    cache.delete(key);
    cache.set(key, bytes);
    while (cache.size > limit) {
      const oldest = cache.keys().next();
      if (oldest.done) break;
      cache.delete(oldest.value);
    }
  }

  return {
    async get(assetId: string, maxEdge: number): Promise<ArrayBuffer | null> {
      const key = cacheKey(assetId, maxEdge);
      const hit = cache.get(key);
      if (!hit) return null;
      touch(key, hit); // 提升为最近使用
      return hit;
    },

    async put(assetId: string, maxEdge: number, bytes: ArrayBuffer): Promise<void> {
      touch(cacheKey(assetId, maxEdge), bytes);
    },

    async clear(): Promise<void> {
      cache.clear();
    },

    get sizeHint(): number {
      return cache.size;
    },
  };
}

type IdbRequest<T> = IDBRequest<T>;

function requestToPromise<T>(request: IdbRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB 请求失败'));
  });
}

function openThumbDb(dbName: string): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      // 只在首次建库时创建：不要在 open 成功后每次都 createObjectStore
      if (!db.objectStoreNames.contains('thumbs')) {
        db.createObjectStore('thumbs');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('打开 IndexedDB 失败'));
    request.onblocked = () => reject(new Error('IndexedDB 被其他标签页占用：请关闭旧页面后重试'));
  });
}

/** 基于原生 IndexedDB 的持久化缓存。
 *
 * 刻意用裸 IndexedDB 而不是 `idb` 这类封装包：装包会改 `package.json`，
 * 而本项目当前处于"技术选型未锁定"阶段，多一个依赖就多一个要人拍板的点。
 * 代价是下面这些 request→Promise 包装得手写一遍，换来的是零新增依赖。
 *
 * `sizeHint` 只能是近似值：IndexedDB 的 `count()` 是异步的，而接口约定它是同步
 * 只读属性。所以这里维护一个本地计数器，构造时异步回填一次，之后 put/clear 增量更新。
 */
export function createIndexedDbThumbnailStore(dbName = 'travelmemory-thumbs'): ThumbnailStore {
  if (typeof indexedDB === 'undefined') {
    console.warn('[travelmemory] 当前环境不支持 IndexedDB，缩略图缓存降级为内存实现');
    return createMemoryThumbnailStore();
  }

  // 延迟打开：工厂本身是同步的，不能 await。首次读写时才真正连库。
  let dbPromise: Promise<IDBDatabase> | null = null;
  let size = 0;
  let counted = false;

  function db(): Promise<IDBDatabase> {
    if (!dbPromise) {
      const opening = openThumbDb(dbName).then((handle) => {
        if (counted) return handle;
        counted = true;
        const tx = handle.transaction('thumbs', 'readonly');
        return requestToPromise(tx.objectStore('thumbs').count()).then(
          (n): IDBDatabase => {
            size = n;
            return handle;
          },
          (): IDBDatabase => handle, // 数不清就算了，sizeHint 本来就是近似值
        );
      });
      // 打开失败要允许下次重试：不能把 rejected 的 promise 永久缓存下来。
      // 顺带也避免 index.html 里冒出 unhandled rejection。
      opening.catch(() => {
        dbPromise = null;
      });
      dbPromise = opening;
    }
    return dbPromise;
  }

  return {
    async get(assetId: string, maxEdge: number): Promise<ArrayBuffer | null> {
      const handle = await db();
      const tx = handle.transaction('thumbs', 'readonly');
      const value = await requestToPromise<ArrayBuffer | undefined>(
        tx.objectStore('thumbs').get(cacheKey(assetId, maxEdge)),
      );
      return value ?? null;
    },

    async put(assetId: string, maxEdge: number, bytes: ArrayBuffer): Promise<void> {
      const key = cacheKey(assetId, maxEdge);
      // 刻意拆成两个事务：IndexedDB 在"当前任务里没有待处理请求"时就自动提交，
      // 在同一个 transaction 里 await 上一个 request 再发下一个，Safari 会报
      // TransactionInactiveError。多一次事务开销换跨端稳定性，值。
      const existing = await (async (): Promise<ArrayBuffer | null> => {
        const handle = await db();
        const tx = handle.transaction('thumbs', 'readonly');
        const value = await requestToPromise<ArrayBuffer | undefined>(
          tx.objectStore('thumbs').get(key),
        );
        return value ?? null;
      })();
      const handle = await db();
      const tx = handle.transaction('thumbs', 'readwrite');
      await requestToPromise(tx.objectStore('thumbs').put(bytes, key));
      if (existing === null) size += 1;
    },

    async clear(): Promise<void> {
      const handle = await db();
      const tx = handle.transaction('thumbs', 'readwrite');
      await requestToPromise(tx.objectStore('thumbs').clear());
      size = 0;
    },

    get sizeHint(): number {
      return size;
    },
  };
}

/** Native 缩略图缓存（未接入的占位实现）。
 *
 * 明早接真机时要替换的内容：
 *   1. 落盘目录用 `Capacitor.Filesystem` 的 `Directory.Cache`（可被系统回收、不占备份配额），
 *      **不要**用 Documents------会被 iCloud/Google 备份，也会进用户的文件 App
 *   2. 文件名建议直接用 `${assetId}:${maxEdge}` 的转义形式，和 Web 侧 key 规则保持一致，
 *      排查"这张缩略图到底哪来的"时能少绕一圈
 *   3. iOS 上 `@capacitor/filesystem` 读出来是 base64，需要自己 `atob` 转 ArrayBuffer
 *   4. 记得给缓存目录加容量上限与清空入口（设置页「清除缓存」），否则相册清了房子脏
 */
export function createNativeThumbnailStore(): ThumbnailStore {
  const NOT_READY = '原生缩略图缓存未接入：请先集成 Capacitor 并实现该桥接';
  return {
    async get(_assetId: string, _maxEdge: number): Promise<ArrayBuffer | null> {
      throw new Error(NOT_READY);
    },
    async put(_assetId: string, _maxEdge: number, _bytes: ArrayBuffer): Promise<void> {
      throw new Error(NOT_READY);
    },
    async clear(): Promise<void> {
      throw new Error(NOT_READY);
    },
    get sizeHint(): number {
      return 0;
    },
  };
}
