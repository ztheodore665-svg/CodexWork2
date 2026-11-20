/** `Trip`（主应用内存里的旅程）→ `JourneyBundle`（`journey.json` 线格式）。
 *
 * 核心原则（§11.2 / B13）：**全部输入按 `unknown` 处理**，非法数据丢弃或降级，
 * 绝不抛错。理由是导出是"尽力而为"的旁路动作 —— 用户点"导回主应用"时，
 * 一条脏数据不该让整个包导不出来。
 *
 * 另一半原则：**只拷贝白名单字段**。原始图片字节（`TripPhoto.image`）从不出现在
 * 输出里，因为这里从来没有读过它 —— 不是"读了再删"，是"根本没碰"。
 */

import {
  INTENSITY_VALUES,
  JOURNEY_BUNDLE_VERSION,
  MAX_THUMBNAIL_BYTES,
  TAG_VALUES,
  type JourneyBundle,
  type JourneyDay,
  type JourneyIntensity,
  type JourneyPhoto,
  type JourneyPhotoLike,
  type JourneyPlace,
  type JourneyTag,
} from './types';

/** `YYYY-MM-DD`。允许重复日期（一天拆两段写），所以日期不是唯一键。 */
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** 非法 tag 的降级值。选 `leisure`（休闲度假）而不是丢弃地点：
 *  丢掉一个地点会连带丢掉它下面所有照片，信息损失远大于标签不准。
 *
 * TODO(歧义): §11.2 只说"非法输入丢弃而不是抛"，没说"非法 tag"该丢地点还是丢 tag。
 *  这里选"保留地点 + 标签降级"，与"非法日期整条丢天"不一致；若产品侧要求严格丢弃，
 *  把 `normalizePlaces` 里的三元表达式改成 return 即可。
 */
const FALLBACK_TAG: JourneyTag = 'leisure';

/** 非法 intensity 的降级值。`mid` 是三星里的中点，语义上"没写"最接近它。 */
const FALLBACK_INTENSITY: JourneyIntensity = 'mid';

/** 旅程标题缺失时的兜底文案。 */
export const FALLBACK_TITLE = '未命名旅程';

/** 把一个 trip 里某张照片的 id 映射到它的缩略图与元数据。 */
export type JourneyPhotoLookup = (photoId: string) => JourneyPhotoLike | null;

/** 把 `raw` 里的照片字段映射成 `JourneyPhoto`；返回 `null` = 丢弃这张照片。 */
export type PhotoMapper = (raw: unknown, index: number) => JourneyPhoto | null;

// ---------------------------------------------------------------------------
// 基础校验原语
// ---------------------------------------------------------------------------

/** 把 `unknown` 收窄成"可索引的对象"。数组与 `null` 都算失败。 */
export function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** 非空字符串（去掉首尾空白后仍非空），否则 `null`。 */
export function asNonEmptyString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** 可选字符串：非法或空白一律当成"没这个字段"。 */
export function asOptionalString(value: unknown): string | undefined {
  return asNonEmptyString(value) ?? undefined;
}

/** 可选有限数字：`NaN` / `Infinity` / 数字字符串都不接受。 */
export function asOptionalFiniteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function isJourneyTag(value: unknown): value is JourneyTag {
  return typeof value === 'string' && (TAG_VALUES as readonly string[]).includes(value);
}

export function isJourneyIntensity(value: unknown): value is JourneyIntensity {
  return typeof value === 'string' && (INTENSITY_VALUES as readonly string[]).includes(value);
}

/** `data:` 开头的字符串才可能是缩略图。缺了它浏览器 `<img>` 会直接报错。 */
export function isDataUrl(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('data:');
}

/** 估算一个 data URL 解码后的字节数。
 *
 * 只用于"是否超过 200KB"这一判断，所以不真的去 `atob`：
 * base64 的长度与解码后字节数是固定比例（每 4 字符 → 3 字节，减去 padding）。
 * 真实解码几十万个字符在导出循环里会明显拖慢，而这个估算的误差恒为 0。
 */
export function base64ByteLength(dataUrl: string): number {
  const comma = dataUrl.indexOf(',');
  if (comma < 0) return 0;
  const meta = dataUrl.slice(0, comma);
  const payload = dataUrl.slice(comma + 1).replace(/\s+/g, '');
  if (!meta.includes(';base64')) {
    // 非 base64 的 data URL（极少见）：按字符数近似，够做上限判定。
    return payload.length;
  }
  if (payload.length === 0) return 0;
  let padding = 0;
  if (payload.endsWith('==')) padding = 2;
  else if (payload.endsWith('=')) padding = 1;
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

// ---------------------------------------------------------------------------
// 照片
// ---------------------------------------------------------------------------

/** 把 `photos` 入参收窄成查表函数。
 *
 * 为什么两种形态都支持：`photos` 可能来自 `postMessage`，`structuredClone` 之后
 * `Map` 仍然是 `Map`，但如果中间过了一次 `JSON.stringify`（比如写进日志再读回来），
 * 它就变成普通对象了。同一份代码两种都能跑，省掉调用方一次调试。
 */
function createPhotoLookup(photos: unknown): JourneyPhotoLookup {
  if (photos instanceof Map) {
    return (photoId) => normalizePhotoLike(photos.get(photoId));
  }
  const record = asRecord(photos);
  if (!record) return () => null;
  return (photoId) =>
    Object.prototype.hasOwnProperty.call(record, photoId)
      ? normalizePhotoLike(record[photoId])
      : null;
}

/** `SystemPhoto`（有 bytes）→ `JourneyPhotoLike`（只有缩略图与元数据）。
 *
 * 这是把原图字节挡在包外的**唯一**关口：只认四个键，其余键（含 `bytes`）看不见。
 */
function normalizePhotoLike(value: unknown): JourneyPhotoLike | null {
  const record = asRecord(value);
  if (!record) return null;
  if (!isDataUrl(record.thumbnailDataUrl)) return null;
  const like: JourneyPhotoLike = { thumbnailDataUrl: record.thumbnailDataUrl };
  const takenAt = asOptionalString(record.takenAt);
  if (takenAt !== undefined) like.takenAt = takenAt;
  const lat = asOptionalFiniteNumber(record.lat);
  if (lat !== undefined) like.lat = lat;
  const lng = asOptionalFiniteNumber(record.lng);
  if (lng !== undefined) like.lng = lng;
  return like;
}

/** 检查缩略图是否可用（data URL + 解码后不超上限）。 */
function isUsableThumbnail(thumbnailDataUrl: string): boolean {
  return base64ByteLength(thumbnailDataUrl) <= MAX_THUMBNAIL_BYTES;
}

/** trip 侧的照片 → `JourneyPhoto`。
 *
 * 三次"丢弃"的判断，任一命中就是这张照片不进包：
 * 1. 没有 id —— 无法回查缩略图；
 * 2. `photos` 里没有这张照片的缩略图 —— 包里写不出图；
 * 3. 缩略图超过 200KB 或不是 data URL —— 包会变得巨大且无法渲染。
 */
function mapTripPhoto(raw: unknown, index: number, lookup: JourneyPhotoLookup): JourneyPhoto | null {
  const record = asRecord(raw);
  if (!record) return null;
  const photoId = asNonEmptyString(record.id);
  if (!photoId) return null;
  const like = lookup(photoId);
  if (!like) return null;
  if (!isUsableThumbnail(like.thumbnailDataUrl)) return null;

  const photo: JourneyPhoto = { id: photoId, thumbnailDataUrl: like.thumbnailDataUrl };
  const caption = asOptionalString(record.caption);
  if (caption !== undefined) photo.caption = caption;
  if (like.takenAt !== undefined) photo.takenAt = like.takenAt;
  if (like.lat !== undefined) photo.lat = like.lat;
  if (like.lng !== undefined) photo.lng = like.lng;
  // 消除未使用参数告警的同时保留签名对称性：index 只用于 bundle 侧的兜底 id。
  void index;
  return photo;
}

/** bundle 侧的照片 → `JourneyPhoto`（import 时复用）。
 *
 * 与 `mapTripPhoto` 的区别：缩略图就在照片对象里，不需要查表。
 * 同样做上限与 data URL 校验 —— 导进来的文件可能是别人手改过的。
 */
export function mapBundlePhoto(raw: unknown, index: number): JourneyPhoto | null {
  const record = asRecord(raw);
  if (!record) return null;
  const id = asNonEmptyString(record.id) ?? `photo-${index + 1}`;
  const thumbnailDataUrl = record.thumbnailDataUrl;
  if (!isDataUrl(thumbnailDataUrl)) return null;
  if (!isUsableThumbnail(thumbnailDataUrl)) return null;

  const photo: JourneyPhoto = { id, thumbnailDataUrl };
  const caption = asOptionalString(record.caption);
  if (caption !== undefined) photo.caption = caption;
  const takenAt = asOptionalString(record.takenAt);
  if (takenAt !== undefined) photo.takenAt = takenAt;
  const lat = asOptionalFiniteNumber(record.lat);
  if (lat !== undefined) photo.lat = lat;
  const lng = asOptionalFiniteNumber(record.lng);
  if (lng !== undefined) photo.lng = lng;
  return photo;
}

// ---------------------------------------------------------------------------
// 地点 / 日期 / 天数
// ---------------------------------------------------------------------------

export function normalizePlaces(rawPlaces: unknown, mapPhoto: PhotoMapper): JourneyPlace[] {
  if (!Array.isArray(rawPlaces)) return [];
  const places: JourneyPlace[] = [];
  rawPlaces.forEach((rawPlace, placeIndex) => {
    const record = asRecord(rawPlace);
    if (!record) return;
    const name = asNonEmptyString(record.name);
    // 地点名是时间线上唯一可读的标识，没有名字的地点画不出来，直接丢。
    if (!name) return;
    const place: JourneyPlace = {
      id: asNonEmptyString(record.id) ?? `place-${placeIndex + 1}`,
      name,
      tag: isJourneyTag(record.tag) ? record.tag : FALLBACK_TAG,
      intensity: isJourneyIntensity(record.intensity) ? record.intensity : FALLBACK_INTENSITY,
      photos: [],
    };
    const label = asOptionalString(record.label);
    if (label !== undefined) place.label = label;
    if (Array.isArray(record.photos)) {
      record.photos.forEach((rawPhoto, photoIndex) => {
        const photo = mapPhoto(rawPhoto, photoIndex);
        if (photo) place.photos.push(photo);
      });
    }
    places.push(place);
  });
  return places;
}

/** 把任意 `unknown` 归一化成 `JourneyDay[]`。非法天（缺日期 / 日期格式不对）整条丢弃：
 *  时间线靠日期排序，没有日期的天无处安放。 */
export function normalizeDays(rawDays: unknown, mapPhoto: PhotoMapper): JourneyDay[] {
  if (!Array.isArray(rawDays)) return [];
  const days: JourneyDay[] = [];
  rawDays.forEach((rawDay, dayIndex) => {
    const record = asRecord(rawDay);
    if (!record) return;
    const date = asNonEmptyString(record.date);
    if (!date || !DATE_PATTERN.test(date)) return;
    days.push({
      id: asNonEmptyString(record.id) ?? `day-${dayIndex + 1}`,
      date,
      places: normalizePlaces(record.places, mapPhoto),
    });
  });
  return days;
}

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

export interface BundleFromTripOptions {
  /** 覆盖导出时间（测试与"导出后立刻回放"的场景用得上）。缺省 = 当前时刻。 */
  exportedAt?: string;
  /** 覆盖标题。缺省 = trip.title，再缺省 = `FALLBACK_TITLE`。 */
  title?: string;
}

/**
 * `Trip`（结构未知）→ `JourneyBundle`。
 *
 * @param trip 主应用的旅程对象。**不 import 它的类型**，运行时逐个字段校验。
 * @param photos 照片 id → 缩略图与元数据。`SystemPhoto` 因结构兼容可直接放入（多余字段被忽略）。
 * @param options 可选覆盖项（导出时间 / 标题）。
 * @returns 永远返回一个结构合法的 bundle；非法输入降级为空 days / 兜底标题。
 */
export function bundleFromTrip(
  trip: unknown,
  photos: Map<string, JourneyPhotoLike>,
  options: BundleFromTripOptions = {},
): JourneyBundle {
  const lookup = createPhotoLookup(photos);
  const record = asRecord(trip);
  const title =
    asNonEmptyString(options.title) ?? asNonEmptyString(record?.title) ?? FALLBACK_TITLE;
  const exportedAt = asOptionalString(options.exportedAt) ?? new Date().toISOString();
  return {
    version: JOURNEY_BUNDLE_VERSION,
    title,
    days: normalizeDays(record?.days, (rawPhoto, index) => mapTripPhoto(rawPhoto, index, lookup)),
    exportedAt,
  };
}
