/** 把 `journey.json`（`journey-2026.1`）导回主应用（阶段 11）。
 *
 * ## 为什么分两步：`planJourneyImport` + `importJourneyToMain`
 *
 * `planJourneyImport` 是**纯函数**（不碰网络、不碰 File、不碰 DOM），因此
 * "哪张照片该留、哪张该丢、文件名怎么起、顺序是什么"这些最容易出错的规则
 * 有单测钉着。`importJourneyToMain` 只负责把计划变成 HTTP 调用。
 *
 * ## 主应用只收得下缩略图
 *
 * 线格式里**没有原图字节**（`packages/journey-transfer` 的 `JourneyPhoto` 刻意
 * 不含 `bytes` 字段，见阶段 10 任务书 §6.3）。所以导回的每一张照片，写进照片库的
 * 都是那张 ≤200KB 的缩略图 JPEG：视觉上是"能看但是糊"，而不是"打不开"。
 * 这是阶段 10 定下的协议约束，不是本阶段的偷懒 —— 报告里如实写明。
 *
 * ## 为什么一次传一张
 *
 * 后端 `POST /api/v1/photos/import` 会按内容 hash 去重：批量传 5 张里有 1 张重复时，
 * 响应里的 `photos` 只含**新导入**的那些，下标跟请求整体错位（这正是
 * `pages/MapPage/photoImport.ts` 里 `assignThumbs` 存在的理由）。
 * 一张一张传就不存在错位：`result.photos[0].id` 就是这张的 id，拿不到就算失败。
 * 代价是 N 次请求 —— 一次导回的照片量级是几张到几十张，可以接受。
 *
 * ## 坐标系
 *
 * journey 里的 `lat` / `lng` 是 WGS84（`JourneyPhoto` 的注释写明了）。
 * 主应用的 `PATCH /api/v1/photos/{id}/location` 要的也是 WGS84（它内部再做 GCJ-02 转换），
 * 所以这里原样透传，不做任何偏移。
 */

import { fromBase64 } from '../../utils/base64';
import {
  importPhotos,
  patchPhotoLocation,
  type ImportPhotosResult,
} from '../../services/api/photos';
import { assignTripPhotos, createTrip, type TripView } from '../../services/api/trips';

/** 只认这个版本的线格式。改协议 = 改这里（与 `packages/journey-transfer` 同步）。 */
export const JOURNEY_BUNDLE_VERSION = 'journey-2026.1';

/** 单张缩略图 base64 解码后的字节上限（与 `journey-transfer` / `photo-source` 一致）。 */
export const MAX_THUMBNAIL_BYTES = 200_000;

/** 计划里的一张照片：数据 URL + 落库文件名 + 可选元数据。 */
export interface JourneyPhotoPlan {
  /** 缩略图 data URL（`data:image/...;base64,...`），已校验过长度。 */
  dataUrl: string;
  /** 上传时用的文件名。**只含 `[a-zA-Z0-9_-]`**：后端按扩展名决定是否解析 EXIF。 */
  filename: string;
  caption?: string;
  takenAt?: string;
  lat?: number;
  lng?: number;
}

/** 一次导回的完整计划。 */
export interface JourneyImportPlan {
  title: string;
  /** 行程起始日（`days[0].date`，YYYY-MM-DD）；包里没有就 undefined。 */
  startedOn?: string;
  photos: JourneyPhotoPlan[];
  /** 因为缩略图不合规被丢弃的张数（非 data URL / 空 / 超限 / 解码失败）。 */
  droppedPhotos: number;
}

/** 导回结果统计。 */
export interface JourneyImportResult {
  tripId: string;
  /** 真正写进照片库、并挂到这趟行程上的张数。 */
  photoCount: number;
  /** 被跳过的总张数（缩略图不合规 + 上传失败）。 */
  skipped: number;
}

/** 可注入的依赖（**只用于单测**；默认走 `services/api` 的真实实现）。 */
export interface JourneyImportDeps {
  importPhotos?: typeof importPhotos;
  createTrip?: typeof createTrip;
  assignTripPhotos?: typeof assignTripPhotos;
  patchPhotoLocation?: typeof patchPhotoLocation;
}


/**
 * 把 journey 包的 `days[].date` 归一成 `YYYY-MM-DD`。
 *
 * 只认日期或 ISO 时间戳两种形态：`2025-10-04` 与 `2025-10-04T09:00:00+08:00`
 * 都取前 10 位，其余（空串、`10/04/2025`、时间戳数字）一律返回 undefined。
 * **宁可没有日期，也不猜一个**：猜错会让行程按错误的月份分组。
 */
function normalizeDayDate(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const candidate = value.trim().slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(candidate) ? candidate : undefined;
}

/** 安全地取一个对象（数组 / null / 原始值一律返回 null）。 */
function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 校验并解码一张缩略图。
 *
 * @returns 解码后的字节数；不合规返回 `null`（调用方计 `droppedPhotos`）
 *
 * 为什么在这里就 base64 解码一次（上传时还会再解码一次）：不解码就无法知道
 * 真实字节数 —— `dataUrl.length` 与字节数差 4/3，用一个长度阈值判"是否超 200KB"
 * 会放行 260KB 的图，而小应用那边按 `MAX_THUMBNAIL_BYTES` 校验后只会静默丢掉它。
 */
function decodeThumbnail(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  if (!value.startsWith('data:image/')) return null;
  const comma = value.indexOf(',');
  if (comma < 0) return null;
  // `data:image/jpeg;base64` 这一段：必须是 base64 编码（URL 编码的 data URL 无法当图片用）
  if (!/;base64$/i.test(value.slice(0, comma))) return null;
  const base64 = value.slice(comma + 1);
  if (base64.length === 0) return null;
  try {
    const bytes = fromBase64(base64);
    if (bytes.length === 0) return null;
    if (bytes.length > MAX_THUMBNAIL_BYTES) return null;
    return bytes.length;
  } catch {
    // 非法字符 / 长度畸形：当作"这张图坏了"，不整单失败
    return null;
  }
}

/** 文件名里的 id 清洗：只留 `[a-zA-Z0-9_-]`，空则用 `photo`。 */
function sanitizeId(raw: string): string {
  const cleaned = raw.replace(/[^a-zA-Z0-9_-]/g, '');
  return cleaned.length > 0 ? cleaned : 'photo';
}

/** `studio-<序号>-<photo.id>.jpg`。
 *
 *  扩展名固定 `.jpg`：后端靠扩展名判定 MIME 再决定是否解析 EXIF，
 *  而 id 里可能带 `/`、`.`、空格（脏字符串会变成路径穿越或错误后缀）。
 */
export function buildJourneyPhotoFilename(seq: number, id: unknown): string {
  const source = typeof id === 'string' ? id : '';
  return `studio-${seq}-${sanitizeId(source)}.jpg`;
}

/**
 * journey（`unknown`，来自另一个 origin 的另一份代码）→ 导入计划。
 *
 * @throws 版本不符 / 缺 `days` 数组时抛**中文**错误（这些是"文件不对"，
 *         不是"个别照片坏了"，必须让用户知道整体失败）
 */
export function planJourneyImport(journey: unknown): JourneyImportPlan {
  const root = asRecord(journey);
  if (!root) {
    throw new Error('导入旅程失败：文件内容不是一个对象，可能不是 journey 文件');
  }
  if (root.version !== JOURNEY_BUNDLE_VERSION) {
    throw new Error(
      `导入旅程失败：版本不匹配（期望 ${JOURNEY_BUNDLE_VERSION}，收到 ${
        typeof root.version === 'string' ? root.version : '缺失'
      }）`,
    );
  }
  if (!Array.isArray(root.days)) {
    throw new Error('导入旅程失败：缺少 days 列表');
  }

  const rawTitle = typeof root.title === 'string' ? root.title.trim() : '';
  const title = rawTitle.length > 0 ? rawTitle : '未命名旅程';
  // 第 1 天的日期是行程起始日**唯一**的事实来源：缩略图没有 EXIF，
  // 后端无法从照片反推（见文件末 `importJourneyToMain` 的说明）。
  const startedOn = normalizeDayDate(asRecord(root.days[0])?.date);

  const photos: JourneyPhotoPlan[] = [];
  let droppedPhotos = 0;
  /** 全局序号（跨天连续）：落地文件名要稳定且不重名。 */
  let seq = 0;

  for (const day of root.days) {
    const dayRecord = asRecord(day);
    if (!dayRecord) continue;
    const places = Array.isArray(dayRecord.places) ? dayRecord.places : [];

    for (const place of places) {
      const placeRecord = asRecord(place);
      if (!placeRecord) continue;
      const placePhotos = Array.isArray(placeRecord.photos) ? placeRecord.photos : [];

      for (const item of placePhotos) {
        const record = asRecord(item);
        if (!record) {
          droppedPhotos += 1;
          continue;
        }
        const byteLength = decodeThumbnail(record.thumbnailDataUrl);
        if (byteLength === null) {
          droppedPhotos += 1;
          continue;
        }

        seq += 1;
        const plan: JourneyPhotoPlan = {
          dataUrl: record.thumbnailDataUrl as string,
          filename: buildJourneyPhotoFilename(seq, record.id),
        };
        if (typeof record.caption === 'string' && record.caption.trim().length > 0) {
          plan.caption = record.caption.trim();
        }
        if (typeof record.takenAt === 'string' && record.takenAt.length > 0) {
          plan.takenAt = record.takenAt;
        }
        // 坐标必须成对出现：只有 lat 没有 lng 是脏数据，补标会让照片落到赤道/本初子午线
        if (isFiniteNumber(record.lat) && isFiniteNumber(record.lng)) {
          plan.lat = record.lat;
          plan.lng = record.lng;
        }
        photos.push(plan);
      }
    }
  }

  // `days[0].date` 已随计划带出（`startedOn`），由 `createTrip` 写进行程；
  // `photos[].takenAt` 仍然无处安放：后端没有"给已入库照片改拍摄时间"的接口。
  // TODO(歧义): `caption` 同样无处安放（没有 caption 写接口），计划里保留字段但不提交。
  return { title, startedOn, photos, droppedPhotos };
}

/** 一张计划照片 → `File`（后端要 multipart 文件）。 */
function toFile(planPhoto: JourneyPhotoPlan): File {
  const comma = planPhoto.dataUrl.indexOf(',');
  const bytes = fromBase64(planPhoto.dataUrl.slice(comma + 1));
  if (bytes.length === 0) throw new Error('缩略图为空');
  // 拷进一个独立的 `ArrayBuffer`：`File` 的 BlobPart 只接受 `ArrayBuffer`
  // （不含 `SharedArrayBuffer`），而 `Uint8Array.buffer` 的类型是 `ArrayBufferLike`。
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return new File([buffer], planPhoto.filename, { type: 'image/jpeg' });
}

/**
 * 导回主应用：上传缩略图 → 建行程 → 按顺序挂照片 → best-effort 补坐标。
 *
 * ## 失败处理（不粉饰）
 *
 * - **单张上传失败**：计入 `skipped`，继续下一张。整批不中断 —— 一张坏图不该让
 *   用户丢掉另外九张。
 * - **一张都没成功**：抛「没有可导入的照片」。此时**不建空行程**：
 *   界面上多出一条没有照片的旅程，用户只会以为是 bug。
 * - **建行程失败**：直接抛（已上传的照片留在照片库里，不会丢；下次导入会因 hash
 *   去重变成 duplicates，由 `skipped` 反映）。
 * - **挂照片失败**：抛。此时行程已建好但没有照片，属于真实的部分失败，
 *   必须让用户看见（toast 里是后端返回的原文）。
 * - **补坐标失败**：只计数，不影响整体成功。坐标是"锦上添花"，
 *   为它把一次成功的导入判成失败，用户会重导一遍并得到一堆重复照片。
 */
export async function importJourneyToMain(
  journey: unknown,
  deps: JourneyImportDeps = {},
): Promise<JourneyImportResult> {
  const plan = planJourneyImport(journey);

  const importPhotosImpl = deps.importPhotos ?? importPhotos;
  const createTripImpl = deps.createTrip ?? createTrip;
  const assignTripPhotosImpl = deps.assignTripPhotos ?? assignTripPhotos;
  const patchPhotoLocationImpl = deps.patchPhotoLocation ?? patchPhotoLocation;

  const photoIds: string[] = [];
  /** 成功入库且带坐标的照片：**挂行程之前**补标，否则 trip_item 快照里是 null。 */
  const pendingLocations: Array<{ photoId: string; lat: number; lng: number }> = [];
  let failedPhotos = 0;

  for (const planPhoto of plan.photos) {
    let file: File;
    try {
      file = toFile(planPhoto);
    } catch {
      failedPhotos += 1;
      continue;
    }

    let result: ImportPhotosResult;
    try {
      // 一次一张：见文件头"为什么一次传一张"
      result = await importPhotosImpl([file]);
    } catch {
      failedPhotos += 1;
      continue;
    }

    const created = result.photos[0];
    const photoId = created?.id ?? '';
    if (photoId.length === 0) {
      // 后端把它当重复（duplicates）或解析失败（failed）—— 两种情况都不加进新行程
      failedPhotos += 1;
      continue;
    }

    photoIds.push(photoId);
    if (typeof planPhoto.lat === 'number' && typeof planPhoto.lng === 'number') {
      pendingLocations.push({ photoId, lat: planPhoto.lat, lng: planPhoto.lng });
    }
  }

  if (photoIds.length === 0) {
    throw new Error(
      plan.photos.length === 0
        ? '没有可导入的照片：这份旅程文件里没有可用的缩略图'
        : '没有可导入的照片：全部照片都没能写入照片库（可能都是重复照片）',
    );
  }

  const trip: TripView = await createTripImpl(
    plan.title,
    plan.startedOn ? { startedOn: plan.startedOn } : undefined,
  );
  const tripId = trip.id;
  if (typeof tripId !== 'string' || tripId.length === 0) {
    throw new Error('新建旅程失败：后端没有返回行程 id');
  }

  // **先补坐标、再挂行程**：`assign_photos_to_trip` 会把 `photo.lat/lng` 快照进
  // `trip_item`。反过来的话，挂进去的那一瞬间坐标还是 null，于是地图上出现
  // "5 张照片未定位" —— 这正是阶段 15 那条 P1 的另一半。
  // 补标失败不影响挂载：行程结构仍然正确，只是那几张点不在地图上。
  for (const location of pendingLocations) {
    try {
      await patchPhotoLocationImpl(location.photoId, location.lat, location.lng);
    } catch {
      // best-effort：没有 UI 展示位可计数，用户下次可手动补标
    }
  }

  // 顺序 = journey 顺序（后端按传入 order 存 ord）
  await assignTripPhotosImpl(tripId, photoIds);

  return {
    tripId,
    photoCount: photoIds.length,
    skipped: plan.droppedPhotos + failedPhotos,
  };
}
