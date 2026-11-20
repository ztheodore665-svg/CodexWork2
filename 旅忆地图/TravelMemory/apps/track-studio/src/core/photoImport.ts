/** `SystemPhoto` → 回放内核的 `Photo`。
 *
 * ## 为什么要一层转换而不是让内核直接吃 `SystemPhoto`
 *
 * `packages/trail` 的 `Photo` 是**回放专用**的数据形状：`takenAt` 是毫秒数、
 * `lat/lng` 用 `null` 表示"没定位"、还要带 `locationSource` 让 UI 区分
 * "EXIF 拍的"和"手工补的"。`SystemPhoto` 是**相册专用**：`takenAt` 是 ISO 串、
 * 坐标是可选字段、还带着原图字节。
 *
 * 让内核认 `SystemPhoto` 会把两个包的关注点焊死；让 app 做一层映射只多 40 行，
 * 但内核可以同时服务 showcase（演示数据）、主应用（IndexedDB）和这里。
 *
 * ## 排序与"没坐标"
 *
 * - 一律按 `takenAt` 升序（内核自己也会再排一次，这里排是为了让 UI 列表也是这个顺序）；
 * - **没有 GPS 的照片保留在数组里**（用户要能看到它们、能删），`lat/lng = null`、
 *   `locationSource = 'none'`。轨迹构建时内核会跳过 null —— 也就是说
 *   "没有坐标的照片不进轨迹"，而**绝不会给它编一个坐标**。
 */

import type { Photo, TransportMode } from '@travelmemory/trail';
import type { SystemPhoto } from '@travelmemory/photo-source';

/** 缺 `takenAt` 的照片之间的伪时间戳间隔。 */
const FALLBACK_STEP_MS = 60_000;

export interface TrailPhotoOptions {
  /** 每张照片「到下一张」的交通工具，按 `SystemPhoto.id` 索引。 */
  transports?: ReadonlyMap<string, TransportMode>;
  /** 缺 `takenAt` 时的伪时间戳基准（毫秒）。缺省取已知时间的最小值。 */
  fallbackBaseMs?: number;
}

/** 文件名 → 地点名：去掉扩展名。`IMG_0001.jpg` → `IMG_0001`。 */
export function placeNameFromFileName(name: string): string {
  const trimmed = typeof name === 'string' ? name.trim() : '';
  if (trimmed.length === 0) return '未命名';
  const dot = trimmed.lastIndexOf('.');
  const base = dot > 0 ? trimmed.slice(0, dot) : trimmed;
  const cleaned = base.trim();
  return cleaned.length > 0 ? cleaned : '未命名';
}

/** ISO（或任意 `Date.parse` 能吃的）字符串 → 毫秒；解析不出来返回 `NaN`。 */
export function parseTakenAt(takenAt: string | undefined): number {
  if (typeof takenAt !== 'string' || takenAt.length === 0) return Number.NaN;
  const ms = Date.parse(takenAt);
  return Number.isFinite(ms) ? ms : Number.NaN;
}

/** 是否带可用坐标（`lat`/`lng` 都必须是有限数字）。 */
function locatedCoords(photo: SystemPhoto): { lat: number; lng: number } | null {
  const { lat, lng } = photo;
  if (typeof lat !== 'number' || !Number.isFinite(lat)) return null;
  if (typeof lng !== 'number' || !Number.isFinite(lng)) return null;
  return { lat, lng };
}

/**
 * `SystemPhoto[]` → `Photo[]`（按 `takenAt` 升序）。
 *
 * TODO(歧义): 任务书没说"没有 takenAt 的照片"该怎么排。当前做法：给它们
 *  `min(已知时间) + 序号 × 60s` 的伪时间戳 —— 只为让排序稳定、轨迹能生成，
 *  不是真实拍摄时间。若产品侧要求"没时间的照片不参与轨迹"，改 `takenAt` 为 0
 *  并在调用处过滤即可。
 *
 * @param photos 内存里的照片
 * @param options 交通工具表 / 伪时间基准
 */
export function toTrailPhotos(
  photos: readonly SystemPhoto[],
  options: TrailPhotoOptions = {},
): Photo[] {
  if (photos.length === 0) return [];

  const parsed = photos.map((photo) => parseTakenAt(photo.takenAt));
  const known = parsed.filter((value) => Number.isFinite(value));
  const baseFromOptions =
    typeof options.fallbackBaseMs === 'number' && Number.isFinite(options.fallbackBaseMs)
      ? options.fallbackBaseMs
      : Number.NaN;
  const fallbackBase = Number.isFinite(baseFromOptions)
    ? baseFromOptions
    : known.length > 0
      ? Math.min(...known)
      : 0;

  const rows = photos.map((photo, index) => ({
    photo,
    index,
    time: Number.isFinite(parsed[index]) ? parsed[index] : fallbackBase + index * FALLBACK_STEP_MS,
  }));
  rows.sort((a, b) => (a.time === b.time ? a.index - b.index : a.time - b.time));

  return rows.map(({ photo, time }) => {
    const coords = locatedCoords(photo);
    const result: Photo = {
      id: photo.id,
      thumb: photo.thumbnailDataUrl,
      lat: coords ? coords.lat : null,
      lng: coords ? coords.lng : null,
      takenAt: time,
      placeName: placeNameFromFileName(photo.name),
      locationSource: coords ? 'exif' : 'none',
    };
    const transport = options.transports?.get(photo.id);
    if (transport !== undefined) result.transportToNext = transport;
    return result;
  });
}

/** 哪些照片没有坐标（UI 用来提示"这 3 张没有位置信息，不会出现在轨迹上"）。 */
export function photosWithoutLocation(photos: readonly SystemPhoto[]): SystemPhoto[] {
  return photos.filter((photo) => locatedCoords(photo) === null);
}
