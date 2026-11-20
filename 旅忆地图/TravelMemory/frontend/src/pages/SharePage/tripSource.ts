/** 后端行程视图 → 分享协议里的 `Trip`（**纯逻辑**）。
 *
 * ## 这一层做什么
 *
 * 「生成信物」原来只能拿 `demoTrip()` 那份写死的示例数据 —— 用户导入的照片进不来，
 * 整条链路在"打包"这一步是断的。这里把后端 `GET /api/v1/trips` 的 `TripView`
 * 折成 `types/share.ts` 的 `Trip`，让导入的照片能真的被打包出去。
 *
 * ## 三个刻意的取舍
 *
 * 1. **`tag` 不做猜测式分类**。`semanticTag` 缺了就退回 `'leisure'`，
 *    用的是 `semanticTags.ts` 里那份与后端同序的词表做**校验与保底**；
 *    分类算法本身在后端 `app/services/semantic_service.py`，前端不复制、不复算。
 * 2. **一个行程 item = 一个 Place**。后端的一条 item 就是一张照片，
 *    它带的是这张照片的语义标签与坐标；"哪些照片算同一个地点"是后端的聚类职责，
 *    前端没有足够信息去重，硬合并只会凭空造出一个后端没说过的地点。
 * 3. **没有拍摄时间的照片归到行程起始日**，而不是丢掉。打包时 `PlaceMeta.date`
 *    必须是 `YYYY-MM-DD`（收件人侧按它排序），所以不能写"未知"这种自由文本；
 *    `1970-01-01` 是给排序用的哨兵，不是真实日期。
 *
 * ## 不碰 `types/share.ts`、不碰 `services/share/`
 *
 * 协议与打包逻辑是 141 条单测守着的封板层。这里只负责**构造**一个合法的
 * `Trip` 交给它们，`pack()` 怎么加密与它无关。
 */

import type { TripView } from '../../services/api/trips';
import type { ThumbnailStore } from '../../types/native';
import { TAG_LABELS, type Day, type Place, type Trip, type TripPhoto } from '../../types/share';
import { FALLBACK_SEMANTIC_TAG, normalizeSemanticTag } from './semanticTags';
// 缩略图的长边必须与导入时存进去的那一档一致，否则 `thumbs.get` 只会拿回 null。
// 故意跨页 import 而不是再写一个 256：两个常量各写一份，改一处忘另一处，
// 表现是"缩略图永远取不到"，而两边代码看着都对。
import { THUMB_MAX_EDGE } from '../MapPage/photoImport';

/** 无拍摄时间的照片归到这一天。`Day.date` 必须是 YYYY-MM-DD（收件人侧按它排序）。 */
export const UNDATED_DAY = '1970-01-01';

/** 语义标签暂缺时的提示语（`TripView.items[].semanticTag === null` 的那些地点）。
 *
 * 为什么必须说出来：`itemToPlace()` 会把缺失的标签**静默**退回 `'leisure'`，
 * 打包照样成功、收件人照样能解开 —— 于是"这个地点其实没被分类过"这件事
 * 在整条链路上没有任何一处会报错。收件人看到一个写着「休闲度假」的地点，
 * 会以为是发送方（或算法）的判断，而它其实只是个占位符。
 *
 * 静默兜底本身是对的（协议要求 `tag` 必填，抛错会让整个包生成失败），
 * 但"兜底"和"告诉用户兜底了"是两件事，少后者就是把猜测伪装成事实。 */
export const MISSING_SEMANTIC_TAG_HINT = '该地点标签暂缺，分享将使用默认标签';

/** 统计一趟行程里**语义标签缺失**的地点数（`semanticTag` 为 null）。
 *
 * 只数 `null`，不数"非法值"：后者是后端给了个新词（词表还没下发），
 * 那是另一回事；`null` 才是"导入那一刻还没有任何证据"——
 * 照片刚入库时 `caption` 还没生成，后端诚实返回 null，
 * 这是本阶段**必然发生**的常态，不是异常。
 */
export function countMissingSemanticTags(view: TripView): number {
  return view.items.filter((item) => item.semanticTag == null).length;
}

/** 缺失数 → 一句能直接显示的提示；没有缺失时返回 `null`（让 UI 不必判空字符串）。 */
export function describeMissingSemanticTags(count: number): string | null {
  if (count <= 0) return null;
  return `有 ${count} 个地点${MISSING_SEMANTIC_TAG_HINT}「${TAG_LABELS[FALLBACK_SEMANTIC_TAG]}」`;
}

/** 后端没有给出"喜爱程度"这类信号，统一给中档 —— 猜高猜低都是在编数据。 */
const DEFAULT_INTENSITY = 'mid';

/** 取 `YYYY-MM-DD`。`takenAt` 优先（UTC ISO），缺了退回 `takenAtLocal`（无时区的当地时间）。 */
export function dayKeyOf(takenAt: string | null, takenAtLocal: string | null): string | null {
  for (const value of [takenAt, takenAtLocal]) {
    if (typeof value === 'string' && value.length >= 10) return value.slice(0, 10);
  }
  return null;
}

/** 后端行程视图 → 分享协议里的 `Trip`。 */
export function tripViewToTrip(view: TripView): Trip {
  const items = [...view.items].sort(byTakenAtAsc);
  const fallbackDay = dayKeyOf(view.startedAt, view.endedAt) ?? UNDATED_DAY;

  // Map 保持插入顺序，所以同一天里的 item 与上面排好的顺序一致
  const buckets = new Map<string, typeof items>();
  for (const item of items) {
    const key = dayKeyOf(item.takenAt, null) ?? fallbackDay;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(item);
    else buckets.set(key, [item]);
  }

  const days: Day[] = [...buckets.entries()]
    // 按日期升序：收件人看到的时间线必须与真实行程顺序一致
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, dayItems]) => ({
      id: `${view.id}:${date}`,
      date,
      places: dayItems.map((item) => itemToPlace(item, view.id)),
    }));

  return {
    id: view.id,
    // 后端标题可能为空（`TripView.title` 归一化后是空串），给一个能认出来的名字
    title: view.title.length > 0 ? view.title : '未命名旅程',
    createdAt: view.startedAt ?? view.endedAt ?? '',
    days,
  };
}

function itemToPlace(item: TripView['items'][number], tripId: string): Place {
  const place: Place = {
    id: `${tripId}:${item.id}`,
    name: item.semanticLabel ?? '未命名地点',
    tag: normalizeSemanticTag(item.semanticTag ?? undefined) ?? FALLBACK_SEMANTIC_TAG,
    intensity: DEFAULT_INTENSITY,
    coverIndex: 0,
    // 照片字节是空的占位：`hydrateTripPhotos` 会在打包前用缩略图缓存填上。
    // 至少保留 1 张 —— 0 张照片的 Place 在打包时会被可见性过滤直接丢掉。
    photos: [{ id: item.photoId, image: new ArrayBuffer(0) }],
  };
  // 没有坐标就不写这两个键：让 `PlaceMeta` 退化成规范原版（不调地理模糊旁路）
  if (typeof item.lat === 'number' && typeof item.lng === 'number') {
    place.lat = item.lat;
    place.lng = item.lng;
  }
  return place;
}

function byTakenAtAsc(a: TripView['items'][number], b: TripView['items'][number]): number {
  return (a.takenAt ?? '').localeCompare(b.takenAt ?? '');
}

export interface HydrateResult {
  trip: Trip;
  /** 本机缩略图缓存里取不到字节、因此没进包的地点数。UI 必须把它报出来。 */
  droppedPlaces: number;
}

/**
 * 用缩略图缓存把 `Trip` 里的空照片字节换成真实缩略图。
 *
 * ## 取不到缩略图的地点**必须丢掉**，不能留着
 *
 * `pack()` 对 0 字节照片是**抛错**的（`pack.ts`：「照片 X 的字节为空」），
 * 而抛错发生在 `prepareShare` 内部 —— 整包生成挂掉，用户只看到一句看不懂的
 * 「照片 ph_xxx 的字节为空」。所以"缓存里没有"这件事必须在这里就地处理掉：
 * 丢掉这个地点，把数量报给 UI，让它显示成一条降级提示。
 *
 * 什么时候会取不到：换浏览器/清了 IndexedDB 之后`GET /trips` 仍然返回行程，
 * 但本机已经没有那批缩略图了。这种情况只能让用户重新导一次照片。
 */
export async function hydrateTripPhotos(trip: Trip, thumbs: ThumbnailStore): Promise<HydrateResult> {
  const days: Day[] = [];
  let droppedPlaces = 0;

  for (const day of trip.days) {
    const places: Place[] = [];
    for (const place of day.places) {
      const photos: TripPhoto[] = [];
      for (const photo of place.photos) {
        const bytes = await thumbs.get(photo.id, THUMB_MAX_EDGE).catch(() => null);
        if (bytes && bytes.byteLength > 0) photos.push({ ...photo, image: bytes });
      }
      if (photos.length === 0) {
        droppedPlaces += 1;
        continue;
      }
      places.push({ ...place, photos });
    }
    if (places.length > 0) days.push({ ...day, places });
  }

  return { trip: { ...trip, days }, droppedPlaces };
}
