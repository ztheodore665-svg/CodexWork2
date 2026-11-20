/** 收件人侧"接受 / 拒绝"的本地存储（W15-4）。
 *
 * ## 为什么要落盘（而不是只放在 React state 里）
 *
 * 收件人做的事是**决定**：「这个地点我收下」/「这个地点我不要」。决定本身要能跨刷新
 * 存活，否则用户关掉标签页再打开，就分不清"上次我到底收没收"。
 *
 * ## 为什么是 IndexedDB 而不是 localStorage
 *
 * 一条记录里带缩略图 data URL（几十 KB 起），localStorage 的 5MB 配额与同步写
 * 会直接卡住主线程。IndexedDB 异步、配额大，且本应用已有用它的先例（`core/identity.ts`）。
 *
 * ## 红线
 *
 * - **不发网络请求**：这个模块只碰本机 IndexedDB。
 * - **不存原图字节**：只存缩略图 data URL（原图 `ArrayBuffer` 留在解包结果里，
 *   离开接收页就丢，见 D7）。`ReceivedPhoto` 里刻意没有 `bytes` 字段。
 * - **不 import `share/**`**：这里只认视图层的 `UnpackView` / `UnpackedPlace`。
 *
 * ## 关于 `id` 的形状
 *
 * `${shareId}:${placeId}` —— 同一个人可能在同一封/不同封信里收到同名地点，
 * 只有 `shareId + placeId` 才唯一。冒号在 `shareId`（`s_xxxxxxxx`）与
 * `placeId`（`p1_xxxxxx`）里都不会出现，所以可直接用来切分。
 */

import { toBase64 } from '../utils/base64';
import type { IntensityBucket, Tag, UnpackView, UnpackedPlace } from '../types/share';

/** IndexedDB 库名（测试与排查时用来在 DevTools → Application 里定位）。 */
export const RECEIVED_DB_NAME = 'trailshare-received';

/** 对象仓库名。 */
export const RECEIVED_STORE_NAME = 'places';

/** 库版本。加字段不需要升版本（IndexedDB 存的是对象），只有改 keyPath / 索引才要。 */
export const RECEIVED_DB_VERSION = 1;

/** 一张已接受的缩略图。**没有原图字节**（见文件头）。 */
export interface ReceivedPhoto {
  /** `data:image/jpeg;base64,...` */
  thumbnailDataUrl: string;
  caption?: string;
}

/** 一个"已接受"（或"已拒绝"）的地点。 */
export interface ReceivedPlace {
  /** `${shareId}:${placeId}` */
  id: string;
  shareId: string;
  fromPubkey: string;
  fromName: string;
  placeName: string;
  label?: string;
  tag: Tag;
  intensity: IntensityBucket;
  /** `YYYY-MM-DD` */
  date: string;
  photos: ReceivedPhoto[];
  lat?: number;
  lng?: number;
  /** ISO 时间戳：**决定发生的时间**（不是信物生成时间）。 */
  acceptedAt: string;
  rejected: boolean;
}

/** 把解出来的照片字节转成缩略图 data URL（内存里转，不落盘、不发请求）。
 *
 * 注意：解包拿到的其实是**原图**字节，这里原样当缩略图用。超过
 * `MAX_THUMBNAIL_BYTES`（200KB）的照片在生成 `journey.json` 时会被
 * `@travelmemory/journey-transfer` 丢掉 —— 本轮不做缩放（没有 canvas 保证，也不引依赖）。
 * TODO(歧义): 下一轮在这里接真实的缩略图缩放（或让发送方就只发小图）。
 */
export function imageToThumbnailDataUrl(image: ArrayBuffer): string {
  if (!(image instanceof ArrayBuffer) || image.byteLength === 0) return '';
  return `data:image/jpeg;base64,${toBase64(new Uint8Array(image))}`;
}

/** 一个解出来的地点 → 它的缩略图 data URL 数组（与 `place.photos` 同序）。 */
export function thumbnailsOf(place: UnpackedPlace): string[] {
  return (place.photos ?? []).map((photo) => imageToThumbnailDataUrl(photo.image));
}

/** 收件人没填显示名时的兜底（与 `ReceivePanel` 一致）。 */
export const UNNAMED_SENDER = '（未署名）';

/**
 * 解包结果 + 一个地点 → 可落盘的 `ReceivedPlace`（**纯函数**，单测不用 IndexedDB）。
 *
 * @param result 解包成功的结果（用它的 `shareId` 与 `from`）
 * @param place  要收下的地点
 * @param photoUrls 与 `place.photos` 同序的缩略图 data URL（来自 `thumbnailsOf`）
 * @param now    决定时间（测试用），缺省 = 当前时刻
 * @returns 一条 `rejected: false` 的记录
 */
export function buildReceivedPlace(
  result: UnpackView,
  place: UnpackedPlace,
  photoUrls: string[],
  now: string = new Date().toISOString(),
): ReceivedPlace {
  const photos: ReceivedPhoto[] = [];
  place.photos.forEach((photo, index) => {
    const url = photoUrls[index] ?? '';
    // 没有可用缩略图的照片不落盘：存一条空 URL 只会在 UI 里渲染成破图。
    if (url.length === 0) return;
    const item: ReceivedPhoto = { thumbnailDataUrl: url };
    const caption = photo.caption?.trim();
    if (caption && caption.length > 0) item.caption = caption;
    photos.push(item);
  });

  const record: ReceivedPlace = {
    id: `${result.shareId}:${place.placeId}`,
    shareId: result.shareId,
    fromPubkey: result.from.pubkey,
    fromName: result.from.displayName?.trim() || UNNAMED_SENDER,
    placeName: place.name,
    tag: place.tag,
    intensity: place.intensity,
    date: place.date,
    photos,
    acceptedAt: now,
    rejected: false,
  };
  const label = place.label?.trim();
  if (label && label.length > 0) record.label = label;
  if (typeof place.lat === 'number' && Number.isFinite(place.lat)) record.lat = place.lat;
  if (typeof place.lng === 'number' && Number.isFinite(place.lng)) record.lng = place.lng;
  return record;
}

/**
 * 读出**这一封信**已经做过的决定（同一文件重复打开时显示"已接受 / 已拒绝"）。
 *
 * `UnpackedGroupView.processed` 的注释写着"由 UI 查本地溯源边后回填"，这就是那一步。
 * 没有它，用户关掉页面再打开同一封信会看到"全部未处理" —— 落盘就白做了。
 *
 * 读库失败**不抛**：读不到历史决定最多是"看起来没处理过"，
 * 不该让用户连内容都看不成（阅读本身不依赖 IndexedDB）。
 *
 * @param view 解包成功的结果
 * @returns 每个 `placeId` 的决定 + 已接受记录的完整内容（供"直接导出"用）
 */
export async function decisionsFor(view: UnpackView): Promise<{
  decisions: Record<string, 'accepted' | 'rejected'>;
  accepted: ReceivedPlace[];
}> {
  const decisions: Record<string, 'accepted' | 'rejected'> = {};
  const accepted: ReceivedPlace[] = [];
  const places = view.groups.flatMap((group) => group.places);
  if (places.length === 0) return { decisions, accepted };

  let stored: ReceivedPlace[];
  try {
    stored = await listReceived();
  } catch {
    return { decisions, accepted };
  }
  const byId = new Map(stored.map((record) => [record.id, record]));

  for (const place of places) {
    const record = byId.get(`${view.shareId}:${place.placeId}`);
    if (!record) continue;
    if (record.rejected) {
      decisions[place.placeId] = 'rejected';
      continue;
    }
    decisions[place.placeId] = 'accepted';
    accepted.push(record);
  }
  return { decisions, accepted };
}

/**
 * 同一个地点 → 一条**拒绝**记录（字段与接受记录一样完整）。
 *
 * 为什么不直接 `markRejected(id)` 了事：拒绝同样是"这个朋友发给我的一个地点"这个事实，
 * 将来按发件人分组/统计时，`fromName` 与 `placeName` 为空会让这条记录变成
 * 一个无主的空壳（QA 报的 P3）。所以拿得到上下文时，就写完整记录。
 *
 * @param result    解包结果（`shareId` 与 `from` 都从它来）
 * @param place     被拒绝的地点
 * @param photoUrls 与 `place.photos` 同序的缩略图（保留，便于"反悔"时直接恢复）
 * @param now       决定时间（测试用），缺省 = 当前时刻
 */
export function buildRejectedPlace(
  result: UnpackView,
  place: UnpackedPlace,
  photoUrls: string[],
  now: string = new Date().toISOString(),
): ReceivedPlace {
  return { ...buildReceivedPlace(result, place, photoUrls, now), rejected: true };
}

/** 打开库。**懒调用**：模块顶层不碰 `indexedDB`（Node 里跑单测也不会炸）。 */
function openDb(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined' || indexedDB === null) {
      reject(new Error('当前浏览器不支持本地存储（IndexedDB 不可用），本次操作没有被保存'));
      return;
    }
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(RECEIVED_DB_NAME, RECEIVED_DB_VERSION);
    } catch (caught) {
      reject(new Error(`打开本地存储失败：${caught instanceof Error ? caught.message : '未知原因'}`));
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(RECEIVED_STORE_NAME)) {
        db.createObjectStore(RECEIVED_STORE_NAME, { keyPath: 'id' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      reject(new Error(`打开本地存储失败：${request.error?.message ?? '未知原因'}`));
    };
    request.onblocked = () => {
      reject(new Error('本地存储被其它标签页占用，请关掉多余的标签页后重试'));
    };
  });
}

/** 在一个事务里跑一次操作，成功后关闭连接。 */
async function withStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      let transaction: IDBTransaction;
      try {
        transaction = db.transaction(RECEIVED_STORE_NAME, mode);
      } catch (caught) {
        reject(new Error(`本地存储不可用：${caught instanceof Error ? caught.message : '未知原因'}`));
        return;
      }
      const request = run(transaction.objectStore(RECEIVED_STORE_NAME));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => {
        reject(new Error(`写入本地存储失败：${request.error?.message ?? '未知原因'}`));
      };
      transaction.onabort = () => {
        reject(new Error('本地存储事务被中断（可能是配额已满）'));
      };
    });
  } finally {
    db.close();
  }
}

/**
 * 保存一条地点记录（同一 `id` 覆盖写）。
 *
 * **`rejected` 原样落库**：接受记录（`buildReceivedPlace`）带 `false`，
 * 拒绝记录（`buildRejectedPlace`）带 `true` —— 不能在这里强行改成 `false`，
 * 否则"拒绝过"会被写成"接受过"。
 *
 * @throws `Error` 中文错误（IndexedDB 不可用 / 配额满 / 被其它标签页占用）
 */
export async function saveReceived(place: ReceivedPlace): Promise<void> {
  if (!place || typeof place.id !== 'string' || place.id.length === 0) {
    throw new Error('要保存的地点缺少 id，无法写入本地存储');
  }
  await withStore('readwrite', (store) => store.put({ ...place, rejected: place.rejected === true }));
}

/**
 * 读回全部已保存的记录（**已拒绝的也在里面**，调用方自己按 `rejected` 过滤）。
 *
 * 排序：`acceptedAt` 降序（最近的在前），与 UI 的"刚收下的排最上面"一致。
 *
 * @throws `Error` 中文错误（IndexedDB 不可用）
 */
export async function listReceived(): Promise<ReceivedPlace[]> {
  const rows = await withStore<unknown[]>('readonly', (store) => store.getAll());
  if (!Array.isArray(rows)) return [];
  const places = rows.filter(isReceivedPlace);
  return places.sort((a, b) => (a.acceptedAt < b.acceptedAt ? 1 : a.acceptedAt > b.acceptedAt ? -1 : 0));
}

/** 运行时收窄：库里可能有旧版本写下的数据，缺字段的一律当脏数据丢掉。 */
function isReceivedPlace(value: unknown): value is ReceivedPlace {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === 'string' &&
    typeof record.date === 'string' &&
    typeof record.placeName === 'string' &&
    Array.isArray(record.photos)
  );
}

/**
 * 标记一个地点为"已拒绝"。
 *
 * **兜底接口**：正常路径是 `buildRejectedPlace` + `saveReceived`（能写出发件人、
 * 地点名、缩略图等完整字段）。只有"手上只有一个 id、拿不到上下文"时才用这个 ——
 * 它写得进一条最小记录，但那条记录没有 `fromName/placeName`，分组时是无主记录。
 *
 * *记录不存在时也要写一条*：用户完全可能"只拒绝、不接受"（拒绝本身就是一次决定），
 * 什么都不写的话这次动作就丢了 —— 再次打开同一封信会看到"未处理"，等于没拒绝。
 *
 * @param placeId `${shareId}:${placeId}`
 * @throws `Error` 中文错误（IndexedDB 不可用 / 记录写不进去）
 */
export async function markRejected(placeId: string): Promise<void> {
  if (typeof placeId !== 'string' || placeId.trim().length === 0) {
    throw new Error('要拒绝的地点缺少 id');
  }
  const id = placeId.trim();
  const existing = await withStore<unknown>('readonly', (store) => store.get(id));

  if (isReceivedPlace(existing)) {
    await withStore('readwrite', (store) => store.put({ ...existing, rejected: true }));
    return;
  }

  const separator = id.indexOf(':');
  const now = new Date().toISOString();
  const minimal: ReceivedPlace = {
    id,
    shareId: separator > 0 ? id.slice(0, separator) : '',
    fromPubkey: '',
    fromName: '',
    placeName: '',
    // 缺字段的最小记录：标签/强度用协议里的中性值，日期用"今天"。
    tag: 'leisure',
    intensity: 'mid',
    date: now.slice(0, 10),
    photos: [],
    acceptedAt: now,
    rejected: true,
  };
  await withStore('readwrite', (store) => store.put(minimal));
}
