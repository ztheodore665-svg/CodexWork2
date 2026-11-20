/** 生成 `journey.json`（§6.3 / D8）。
 *
 * ## 包里有什么、没有什么
 *
 * 有：缩略图 data URL + 地点名 + 日期 + 标签 + 强度。
 * 没有：**原图字节**。这一条由 `@travelmemory/journey-transfer` 的
 * `bundleFromTrip` 强制（它只认白名单字段），这里连传都不传。
 *
 * ## 分组规则
 *
 * 1. 按拍摄时间的 **UTC 日期**分天（与 `packages/trail` 的分天逻辑一致：
 *    EXIF 墙钟已按 UTC 存，所以 UTC 日期 = 用户相机里看到的日期）；
 * 2. 同一天里，**连续的同名地点**合成一个 place（多张照片挂在同一个地点下）。
 *
 * TODO(歧义): track-studio 的编辑页只让用户选交通工具，不采集标签/强度
 *  （那是 TrailShare 的字段）。这里统一填 `leisure` / `mid`，并在 UI 上说明
 * "导回主应用后可以再改标签"。若要求逐地点填写，需要在编辑页加表单。
 */

import {
  bundleFromTrip,
  type JourneyBundle,
  type JourneyPhotoLike,
} from '@travelmemory/journey-transfer';
import type { SystemPhoto } from '@travelmemory/photo-source';

import { parseTakenAt, placeNameFromFileName } from './photoImport';

/** 未采集标签时的默认值（六类标签里的"休闲度假"）。 */
const DEFAULT_TAG = 'leisure';
/** 未采集强度时的默认值（三档里语义最中性的）。 */
const DEFAULT_INTENSITY = 'mid';

export interface JourneyExportInput {
  title: string;
  photos: readonly SystemPhoto[];
  /** 覆盖导出时间（测试用）。缺省 = 当前时刻。 */
  exportedAt?: string;
}

/** 与 trip 结构对齐的最小子集：只有 `bundleFromTrip` 会读的字段。 */
interface TripLikePlace {
  id: string;
  name: string;
  tag: string;
  intensity: string;
  photos: Array<{ id: string }>;
}

interface TripLikeDay {
  id: string;
  date: string;
  places: TripLikePlace[];
}

/** 毫秒 → `YYYY-MM-DD`（UTC）。 */
export function isoDateFromMs(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * `SystemPhoto[]` → `JourneyBundle`。
 *
 * @param input 标题 + 照片（+ 可选的导出时间）
 * @returns 结构合法的 bundle；没有照片时 `days` 为空但仍然可导入
 */
export function buildJourneyBundle(input: JourneyExportInput): JourneyBundle {
  const photoMeta = new Map<string, JourneyPhotoLike>();
  const orderedDays: TripLikeDay[] = [];
  const dayByDate = new Map<string, TripLikeDay>();
  /** 用于给"没有 takenAt"的照片安排一个可排序的日期。 */
  const todayMs = Date.now();

  input.photos.forEach((photo, index) => {
    photoMeta.set(photo.id, {
      thumbnailDataUrl: photo.thumbnailDataUrl,
      takenAt: photo.takenAt,
      lat: photo.lat,
      lng: photo.lng,
    });

    const parsed = parseTakenAt(photo.takenAt);
    const ms = Number.isFinite(parsed) ? parsed : todayMs + index;
    const date = isoDateFromMs(ms);

    let day = dayByDate.get(date);
    if (!day) {
      day = { id: `d${orderedDays.length + 1}`, date, places: [] };
      dayByDate.set(date, day);
      orderedDays.push(day);
    }

    const placeName = placeNameFromFileName(photo.name);
    const last = day.places[day.places.length - 1];
    if (last && last.name === placeName) {
      last.photos.push({ id: photo.id });
      return;
    }
    day.places.push({
      id: `p${day.places.length + 1}_${date}`,
      name: placeName,
      tag: DEFAULT_TAG,
      intensity: DEFAULT_INTENSITY,
      photos: [{ id: photo.id }],
    });
  });

  const tripLike = {
    id: 'track-studio-export',
    title: input.title,
    createdAt: new Date().toISOString(),
    days: orderedDays,
  };

  return bundleFromTrip(tripLike, photoMeta, { exportedAt: input.exportedAt });
}

/** bundle → 落盘用的 JSON 文本（两空格缩进，便于用户拿文本编辑器看一眼）。 */
export function bundleToJson(bundle: JourneyBundle): string {
  return JSON.stringify(bundle, null, 2);
}

/** 触发浏览器下载（B16：`<a download>` + Blob URL，不用 `window.open`）。 */
export function downloadBlob(blob: Blob, filename: string): void {
  if (typeof document === 'undefined') return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 立刻 revoke 会让部分浏览器来不及开始下载，延后一拍
  setTimeout(() => {
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* 已经 revoke 过 */
    }
  }, 1000);
}
