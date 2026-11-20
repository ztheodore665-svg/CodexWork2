/** 编辑态（草稿）→ 协议要的 `Trip`。
 *
 * 这一层是"UI 的自由度"和"协议的硬约束"之间的唯一闸门，所以所有校验都在这里做，
 * 且**错误文案是给用户看的中文**：UI 直接把 `Error.message` 显示出来，
 * 不需要再翻译一遍。
 *
 * 协议硬约束（来自 `share/schema.ts`，改这里必须同步那边）：
 * - 每个地点 **1–5 张**照片（0 张会出现空地点卡；>5 张是体积与可读性的折中）。
 *   **超限（>5 张）会在 `splitOversizedPlaces` 里自动拆分成多个地点**，
 *   所以用户"同一地点塞了 6 张"也能生成；协议层本身仍然只认 1–5 张（不可改）。
 * - 日期必须 `YYYY-MM-DD`（`PlaceMeta.date` 会被拿去排序）；
 * - `tag` 必须是六类之一，`intensity` 必须是三档之一。
 */

import {
  ALL_INTENSITIES,
  ALL_TAGS,
  INTENSITY_MEANING,
  TAG_LABELS,
  type IntensityBucket,
  type PlaceGeoHint,
  type Tag,
  type Trip,
} from '../types/share';
import { MAX_PHOTOS_PER_PLACE, MIN_PHOTOS_PER_PLACE } from '../share/schema';
import { newId } from '../utils/id';

/** 一张待打包照片。`bytes` 是**原图**（不做二次压缩），`thumbnailDataUrl` 只用于 journey.json。 */
export interface PhotoDraft {
  /** 运行时 id（`p_xxxx`，来自 photo-source）。 */
  id: string;
  bytes: ArrayBuffer;
  name: string;
  caption?: string;
  thumbnailDataUrl?: string;
  takenAt?: string;
  lat?: number;
  lng?: number;
}

export interface PlaceDraft {
  /** 运行时 id（仅 UI 用，最终写进包里的 id 会重新生成）。 */
  id: string;
  name: string;
  label?: string;
  tag: Tag;
  intensity: IntensityBucket;
  photos: PhotoDraft[];
}

export interface DayDraft {
  id: string;
  /** `YYYY-MM-DD` */
  date: string;
  places: PlaceDraft[];
}

export interface TripDraft {
  title: string;
  days: DayDraft[];
}

/** 标签下拉选项（中文名来自 `types/share.ts`，顺序就是协议顺序）。 */
export const TAG_OPTIONS: ReadonlyArray<{ value: Tag; label: string }> = ALL_TAGS.map((tag) => ({
  value: tag,
  label: TAG_LABELS[tag],
}));

/** 强度下拉选项：值 + 星星 + 中文含义。 */
export const INTENSITY_OPTIONS: ReadonlyArray<{
  value: IntensityBucket;
  label: string;
  stars: string;
  meaning: string;
}> = ALL_INTENSITIES.map((intensity) => ({
  value: intensity,
  label: `${INTENSITY_MEANING[intensity]}`,
  stars: intensity === 'low' ? '★' : intensity === 'mid' ? '★★' : '★★★',
  meaning: INTENSITY_MEANING[intensity],
}));

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** 日期是否合法（格式 + 真实存在，`2026-02-30` 不算）。 */
export function isValidDate(value: string): boolean {
  if (!DATE_RE.test(value)) return false;
  const [year, month, day] = value.split('-').map((part) => Number.parseInt(part, 10));
  const ms = Date.UTC(year, month - 1, day);
  const date = new Date(ms);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

/** 今天（UTC）的 `YYYY-MM-DD`，新建一天时的默认值。 */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** 草稿里的照片总字节数（UI 用来提示"包会比较大"）。 */
export function totalPhotoBytes(draft: TripDraft): number {
  let total = 0;
  for (const day of draft.days) {
    for (const place of day.places) {
      for (const photo of place.photos) {
        total += photo.bytes.byteLength;
      }
    }
  }
  return total;
}

/**
 * 把单地点 >`limit` 张照片拆成多个地点（每个 ≤`limit`），名字加「（i/n）」。
 *
 * ## 为什么需要它（W15-2）
 *
 * 协议硬约束是"每个地点 1–5 张"（`share/schema.ts:36`，`pack.ts` 与 `schema.ts` 两处
 * **强制校验**，本轮不允许动协议层）。所以"同一地点放 6 张照片"在协议层是无解的，
 * 唯一不改协议的做法是在**应用层自动拆分**：6 张 → 「西湖（1/2）」5 张 +
 * 「西湖（2/2）」1 张。用户不会看到任何错误，包也照样能生成、能 `pack`。
 *
 * ## 语义细节
 *
 * - **只拆超限地点**，其它地点（含照片、id、顺序）原样返回，不做任何深拷贝；
 * - 照片按**原顺序**分块（第 1 块 = 前 `limit` 张），保证"打包顺序 = 用户看到顺序"；
 * - 第 1 块保留原 `id`（UI 的 key 不变），后续块用 `${id}~${n}`；
 * - `tag` / `intensity` / `label` 继承原地点，`day` 的 `date` 天然继承（只换地点）；
 * - 每张照片**保留原引用**（`bytes` 是 ArrayBuffer，复制对象会白白多占内存）；
 * - **幂等**：所有地点都 ≤`limit` 时返回的草稿与原草稿结构等价，重复调用无副作用。
 *
 * ## 已知副作用（本轮边界内无法修）
 *
 * - `journeyExport.photoMetaFromDrafts()` 用"天→地点→照片"的**结构位置**把草稿缩略图
 *   对到 trip 的照片 id 上；拆分会让超限地点之后的位置错开，多出来的照片回落到
 *   `FALLBACK_THUMBNAIL_DATA_URL`（不崩、但缩略图会缺）。修它要改 `journeyExport.ts`
 *   /App，本轮不许动。
 *   TODO(歧义): 下一轮让 `photoMetaFromDrafts` 按拆块顺序重建映射（或让 App 先拆草稿）。
 * - `PreviewPanel` 仍然渲染**未拆分**的草稿（"1 个地点 6 张"），生成后包里是 2 个地点。
 *   TODO(歧义): 预览层也过一遍 `splitOversizedPlaces` 才不会出现"预览≠成品"。
 *
 * @param draft 待检查的草稿
 * @param limit 每个地点允许的最大照片数，默认取协议上限 `MAX_PHOTOS_PER_PLACE`
 * @returns 拆分后的**新**草稿（无超限地点时结构上等价于原草稿）
 */
export function splitOversizedPlaces(
  draft: TripDraft,
  limit: number = MAX_PHOTOS_PER_PLACE,
): TripDraft {
  // 脏数据（外部注入 / 反序列化）一律放行给 `validateDraft` 报错，这里不猜也不崩。
  if (!draft || !Array.isArray(draft.days)) return draft;

  // 非法 limit（NaN / 0 / 负数 / 小数）回落到协议上限：宁可放过也不制造无限循环。
  const size =
    typeof limit === 'number' && Number.isFinite(limit) && limit >= 1
      ? Math.floor(limit)
      : MAX_PHOTOS_PER_PLACE;

  let changed = false;

  const days: DayDraft[] = draft.days.map((day) => {
    if (!day || !Array.isArray(day.places)) return day;

    let dayChanged = false;
    const places: PlaceDraft[] = [];
    for (const place of day.places) {
      const photos = place?.photos;
      if (!place || !Array.isArray(photos) || photos.length <= size) {
        places.push(place);
        continue;
      }

      dayChanged = true;
      changed = true;
      const total = Math.ceil(photos.length / size);
      for (let chunkIndex = 0; chunkIndex < total; chunkIndex += 1) {
        const chunk = photos.slice(chunkIndex * size, (chunkIndex + 1) * size);
        const suffix = `（${chunkIndex + 1}/${total}）`;
        const baseName = (place.name ?? '').trim();
        places.push({
          ...place,
          id: chunkIndex === 0 ? place.id : `${place.id}~${chunkIndex + 1}`,
          name: `${baseName}${suffix}`,
          // 照片对象原样带走（同一批引用，不复制 ArrayBuffer）
          photos: chunk,
        });
      }
    }

    return dayChanged ? { ...day, places } : day;
  });

  if (!changed) return draft;
  return { ...draft, days };
}

/**
 * 校验草稿。通过返回 `null`，否则返回**给用户看的中文错误**。
 *
 * 与 `buildTrip` 分开是为了让 UI 能在用户输入过程中实时提示，
 * 而不是点"生成"之后才炸。
 *
 * **会先自动拆分超限地点**（见 `splitOversizedPlaces`）：这样 "同一地点 6 张照片"
 * 在 UI 上不会显示成阻塞性错误，生成按钮也不会被挡住。
 * 拆分后的草稿里每个地点都在 1–5 张之间，所以下面的"超限"分支正常情况下不会触发 ——
 * 真触发说明拆分逻辑有 bug。
 */
export function validateDraft(draft: TripDraft): string | null {
  return validateDraftStrict(splitOversizedPlaces(draft));
}

/** `validateDraft` 的真身：**不做拆分**，按协议硬约束逐项检查。
 *
 * 导出只为测试/诊断用（验证"超限"那条兜底文案），业务代码一律走 `validateDraft`。 */
export function validateDraftStrict(draft: TripDraft): string | null {
  if (!draft || !Array.isArray(draft.days)) return '旅程数据不完整';
  if (draft.title.trim().length === 0) return '请先填写旅程标题';
  if (draft.days.length === 0) return '至少需要一天';

  for (let dayIndex = 0; dayIndex < draft.days.length; dayIndex += 1) {
    const day = draft.days[dayIndex];
    if (!isValidDate(day.date)) {
      return `第 ${dayIndex + 1} 天的日期不对，要写成 YYYY-MM-DD（例：2026-11-01）`;
    }
    if (day.places.length === 0) return `第 ${dayIndex + 1} 天还没有地点`;

    for (let placeIndex = 0; placeIndex < day.places.length; placeIndex += 1) {
      const place = day.places[placeIndex];
      const where = `第 ${dayIndex + 1} 天第 ${placeIndex + 1} 个地点`;
      if (place.name.trim().length === 0) return `${where}还没有名字`;
      if (!(ALL_TAGS as readonly string[]).includes(place.tag)) {
        return `${where}的标签不合法（只能是六类之一）`;
      }
      if (!(ALL_INTENSITIES as readonly string[]).includes(place.intensity)) {
        return `${where}的强度不合法（只能是 low / mid / high）`;
      }
      if (place.photos.length < MIN_PHOTOS_PER_PLACE) {
        return `${where}需要 ${MIN_PHOTOS_PER_PLACE}–${MAX_PHOTOS_PER_PLACE} 张照片，现在有 ${place.photos.length} 张`;
      }
      if (place.photos.length > MAX_PHOTOS_PER_PLACE) {
        // 正常情况下走不到这里：`validateDraft` 已经先调 `splitOversizedPlaces` 拆过一遍。
        return `${where}超过每个地点最多 ${MAX_PHOTOS_PER_PLACE} 张照片（协议上限），已自动拆分；若仍看到这条说明拆分未生效`;
      }
      for (const photo of place.photos) {
        if (!(photo.bytes instanceof ArrayBuffer) || photo.bytes.byteLength === 0) {
          return `${where}里有一张照片的字节是空的，请重新选照片`;
        }
      }
    }
  }
  return null;
}

/**
 * 一个地点的代表坐标：**该地点第一张带 lat/lng 的照片**的位置。
 *
 * 为什么是"第一张"而不是重心：用户心里的地点位置就是封面那张（`coverIndex` 恒为 0），
 * 而且第一张是稳定可解释的 —— 重心在"两张照片跨城"时会落到两城之间的荒地上，
 * 那种坐标看着像真的、其实更假。
 *
 * 坐标非法（非有限数 / 越界）一律当作"没有坐标"，绝不猜。
 *
 * @param photos 草稿照片
 * @returns `{lat,lng}` 或 `null`
 */
export function firstGeoOf(
  photos: readonly { lat?: number; lng?: number }[],
): { lat: number; lng: number } | null {
  for (const photo of photos) {
    const { lat, lng } = photo;
    if (typeof lat !== 'number' || !Number.isFinite(lat)) continue;
    if (typeof lng !== 'number' || !Number.isFinite(lng)) continue;
    if (lat < -90 || lat > 90) continue;
    if (lng < -180 || lng > 180) continue;
    return { lat, lng };
  }
  return null;
}

/**
 * `Trip` → `placeId → 坐标` 表（`pack()` 的 `geoHints` 入参，见 `share/pack.ts`）。
 *
 * ## 为什么必须有这一层（W12-4 的功能缺口）
 *
 * `pack()` **只通过 `geoHints`** 把 `meta.lat/lng/radius` 写进密文（它不读
 * `Place.lat/lng`）。不传 `geoHints` 的话，生成的包里根本没有坐标，
 * 收件人侧的 `UnpackedPlace.lat/lng` 全是 undefined —— 地图上一个点都画不出来。
 *
 * ## `radius: 0` 的语义
 *
 * 0 = **精确点，不是模糊圈**。本版本没有后端地理模糊（`prepare.ts` 那条路需要服务端），
 * 所以这里诚实地写 0：收件人地图会画 Marker 而不是 Circle。生成页会显示
 * 「信物会包含这些地点的精确坐标（本版本未做地理模糊）」提醒发送方。
 *
 * 没有坐标的地点**不写键**（不猜位置，也不写 `lat: 0, lng: 0` 那种"几内亚湾"）。
 *
 * @param trip 已构建的 `Trip`（坐标由 `buildTrip` 从照片上带过来）
 */
export function buildGeoHints(trip: Trip): Record<string, PlaceGeoHint> {
  const hints: Record<string, PlaceGeoHint> = {};
  for (const day of trip.days ?? []) {
    for (const place of day.places ?? []) {
      const lat = place.lat;
      const lng = place.lng;
      if (typeof lat !== 'number' || !Number.isFinite(lat)) continue;
      if (typeof lng !== 'number' || !Number.isFinite(lng)) continue;
      if (lat < -90 || lat > 90 || lng < -180 || lng > 180) continue;
      hints[place.id] = { lat, lng, radius: 0 };
    }
  }
  return hints;
}

export interface BuildTripOptions {
  /** 覆盖 trip id（测试用）。 */
  tripId?: string;
}

/**
 * 草稿 → `Trip`。**校验失败直接抛错**（`validateDraft` 的中文文案）。
 *
 * 进入校验前先 `splitOversizedPlaces` 拆一次（W15-2）：协议层每地点只认 1–5 张，
 * 且 `pack()` / `schema.ts` 两处强制校验、不可改，所以"6 张同一地点"只能在应用层
 * 拆成 5+1。拆分是幂等的，`validateDraft` 内部再拆一次也无副作用。
 *
 * id 形状是协议的可辨识特征（`t_`=旅程、`g1_`=组、`p1_`=地点、`ph_`=照片），
 * 所以这里统一用 `newId(prefix, len)` 生成，不自己拼随机串。
 */
export function buildTrip(draft: TripDraft, options: BuildTripOptions = {}): Trip {
  const safeDraft = splitOversizedPlaces(draft);
  const problem = validateDraftStrict(safeDraft);
  if (problem) throw new Error(problem);

  const tripId = options.tripId ?? newId('t', 8);

  return {
    id: tripId,
    title: draft.title.trim(),
    createdAt: new Date().toISOString(),
    days: safeDraft.days.map((day, dayIndex) => ({
      id: newId(`d${dayIndex + 1}`, 6),
      date: day.date,
      places: day.places.map((place, placeIndex) => {
        // 协议里坐标挂在 `Place` 上（TravelMemory 扩展，可选）。这里从照片上取
        // 代表点带过去，`buildGeoHints(trip)` 才有东西可读（见该函数注释）。
        const geo = firstGeoOf(place.photos);
        return {
          id: newId(`p${placeIndex + 1}`, 6),
          name: place.name.trim(),
          ...(place.label && place.label.trim().length > 0 ? { label: place.label.trim() } : {}),
          tag: place.tag,
          intensity: place.intensity,
          coverIndex: 0,
          photos: place.photos.map((photo) => {
            const out: { id: string; image: ArrayBuffer; caption?: string } = {
              id: newId('ph', 8),
              image: photo.bytes,
            };
            if (photo.caption && photo.caption.trim().length > 0) out.caption = photo.caption.trim();
            return out;
          }),
          ...(geo ? { lat: geo.lat, lng: geo.lng } : {}),
        };
      }),
    })),
  };
}

/** 空草稿：一天一个空地点（UI 的初始态）。 */
export function emptyDraft(): TripDraft {
  return {
    title: '',
    days: [
      {
        id: `d1`,
        date: todayIso(),
        places: [
          { id: 'p1', name: '', tag: 'nature', intensity: 'mid', photos: [] },
        ],
      },
    ],
  };
}
