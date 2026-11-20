/** 行程 + 照片库 → 回放内核（`@travelmemory/trail`）的 `Journey`。
 *
 * ## 这一层只做"翻译"，不做业务判断
 *
 * 后端给的 `TripView` 只有 `photoId`，照片库侧又没有 trip 字段，所以两边靠
 * `Map<photoId, PhotoListItem>` 反查合并。任何一侧缺数据都**退化为丢点**，
 * 而不是补 0 或猜默认值 —— 假数据会让回放看起来"多了几个不存在的地方"。
 *
 * ## 三个必须按现状处理的点
 *
 * 1. **时间**：演示数据的 `taken_at` 全是 null（seed 脚本只写 `taken_at_local`）。
 *    `takenAtLocal` 是**无时区的墙钟字符串**，而内核约定「墙钟塞进 `Date.UTC`」，
 *    两边语义正好一致 → 直接补 `Z` 交给 `Date.parse`，**绝不做时区换算**。
 * 2. **坐标**：后端恒存 WGS84，内核也吃 WGS84，转换发生在渲染器内部（转 GCJ-02）。
 *    这里再转一次就是双重偏移。
 * 3. **locationSource**：后端是 `exif / user / inferred / none`，内核只认
 *    `exif / manual / none`。`user` 与 `inferred` 都收进 `manual`（都是"不是相机原生写的"）。
 */

import { buildJourney, formatDateDot, type Journey, type Photo } from '@travelmemory/trail';
import type { PhotoListItem } from '../../../services/api/photos';
import type { TripView } from '../../../services/api/trips';

/** 深色渐变，给没有缩略图的点兜底。按 id 哈希取，保证同一条行程每次回放颜色一致。 */
const GRADIENTS = [
  'linear-gradient(135deg,#0f2027,#203a43)',
  'linear-gradient(135deg,#1a2a6c,#2a5298)',
  'linear-gradient(135deg,#2c3e50,#4ca1af)',
  'linear-gradient(135deg,#153b2f,#2f6f4f)',
  'linear-gradient(135deg,#3a1c4a,#7b4397)',
  'linear-gradient(135deg,#0b3a53,#146e8c)',
  'linear-gradient(135deg,#2b1055,#6a3ea1)',
  'linear-gradient(135deg,#3b2f14,#8c6a2f)',
] as const;

/** FNV-1a：32 位、确定性、无依赖。不用 `Math.random`，否则每次进回放颜色都在变。 */
function hashString(value: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function gradientFor(id: string): string {
  return GRADIENTS[hashString(id) % GRADIENTS.length];
}

/** `2026-07-12T08:00:00` 这种"带 T 不带时区"的墙钟串 */
const WALL_CLOCK = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;

/**
 * 墙钟字符串 → 毫秒。语义是「把这串数字当成 UTC」——
 * 与内核 `Photo.takenAt` 的约定一致，显示时一律用 `getUTC*`，跨时区不漂移。
 */
export function parseWallClock(local: string | null | undefined): number | null {
  const text = (local ?? '').trim();
  if (text.length === 0) return null;
  const iso = WALL_CLOCK.test(text) ? `${text}Z` : text;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/** 带时区的 ISO 串（后端 `takenAt` / `createdAt`）→ 毫秒 */
function parseInstant(value: string | null | undefined): number | null {
  const text = (value ?? '').trim();
  if (text.length === 0) return null;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : null;
}

/** 后端的定位来源 → 内核的三值枚举 */
export function toLocationSource(source: string | null | undefined): Photo['locationSource'] {
  switch (source) {
    case 'exif':
      return 'exif';
    case 'user':
    case 'inferred':
      return 'manual';
    default:
      return 'none';
  }
}

/** `2026.07.12` 或跨天时 `2026.07.12 — 2026.07.18` */
function subtitleOf(timestamps: readonly number[]): string {
  if (timestamps.length === 0) return '';
  let first = timestamps[0];
  let last = timestamps[0];
  for (const value of timestamps) {
    if (value < first) first = value;
    if (value > last) last = value;
  }
  const head = formatDateDot(first);
  const tail = formatDateDot(last);
  return head === tail ? head : `${head} — ${tail}`;
}

/**
 * 组出可直接喂给 `prepareScene` 的 Journey。
 *
 * 少于 2 个可定位点返回 `null` —— 一个点画不出轨迹，让 UI 显示提示比画一颗孤立的光点诚实。
 */
export function buildReplayJourney(
  trip: TripView,
  photos: readonly PhotoListItem[],
): Journey | null {
  const byId = new Map<string, PhotoListItem>();
  for (const photo of photos) byId.set(photo.id, photo);

  const points: Photo[] = [];

  for (const item of trip.items) {
    const photo = byId.get(item.photoId);

    // 坐标以**行程快照**为准（它是后端聚类/用户手改后的结果），照片库只用来补时间与来源。
    // 快照没有坐标就是"这个点没定位"，不做二次猜测。
    if (item.lat === null || item.lng === null) continue;
    if (!Number.isFinite(item.lat) || !Number.isFinite(item.lng)) continue;

    // 时间：墙钟 → 行程快照的 UTC ISO → 入库时间。三者都拿不到就丢，
    // 不能用 0 —— 那会把这个点排到 1970 年，整条轨迹的时间轴被它拉到最前。
    const takenAt =
      parseWallClock(photo?.takenAtLocal) ??
      parseInstant(item.takenAt) ??
      parseInstant(photo?.createdAt) ??
      null;
    if (takenAt === null) continue;

    points.push({
      id: item.photoId,
      // `PhotoListItem` 没有缩略图字段，回放里也不画卡片，所以只用渐变占位；
      // 按 id 哈希而不是随机，保证同一条行程每次回放配色一致。
      gradient: gradientFor(item.photoId),
      lat: item.lat,
      lng: item.lng,
      takenAt,
      placeName: item.semanticLabel ?? undefined,
      locationSource: toLocationSource(photo?.locationSource),
    });
  }

  if (points.length < 2) return null;

  return buildJourney({
    id: trip.id,
    title: trip.title,
    subtitle: subtitleOf(points.map((point) => point.takenAt)),
    photos: points,
  });
}
