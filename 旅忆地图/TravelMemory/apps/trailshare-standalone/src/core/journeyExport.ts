/** `journey.json` 导出（§6.3 / D8）：只缩略图 + 元数据，**不含原图字节**。
 *
 * 与 `.trailshare` 的分工：
 * - `.trailshare` 是**给收件人**的加密包，含原图（只有指定的人能解）；
 * - `journey.json` 是**导回主应用**的明文包，只含缩略图与地点/日期/标签，
 *   用于在主应用里重建一条可浏览的旅程（主应用不会因此拿到原图）。
 *
 * `bundleFromTrip` 只认白名单字段（`thumbnailDataUrl`/`takenAt`/`lat`/`lng`），
 * 且从不读 `image`，所以原图字节**没有路径**进入这个文件。
 */

import {
  bundleFromTrip,
  type JourneyBundle,
  type JourneyPhotoLike,
} from '@travelmemory/journey-transfer';
import { FALLBACK_THUMBNAIL_DATA_URL } from '@travelmemory/photo-source';

import type { Trip } from '../types/share';
import type { TripDraft } from './buildTrip';

/**
 * 按**位置**把草稿里的缩略图/EXIF 元数据对到最终 trip 的照片 id 上。
 *
 * `buildTrip` 会为每张照片重新生成 `ph_xxxxxxxx`，所以两份数据的关联键只能是
 * 结构位置（天→地点→照片的顺序在 `buildTrip` 里是 1:1 保留的）。
 * 这也意味着**不要**在 UI 里对已生成的 trip 重排照片而不重新 build。
 */
export function photoMetaFromDrafts(
  trip: Trip,
  draft: TripDraft,
): Map<string, JourneyPhotoLike> {
  const map = new Map<string, JourneyPhotoLike>();
  trip.days.forEach((day, dayIndex) => {
    day.places.forEach((place, placeIndex) => {
      place.photos.forEach((photo, photoIndex) => {
        const source = draft.days[dayIndex]?.places[placeIndex]?.photos[photoIndex];
        const meta: JourneyPhotoLike = {
          thumbnailDataUrl: source?.thumbnailDataUrl ?? FALLBACK_THUMBNAIL_DATA_URL,
        };
        if (source?.takenAt) meta.takenAt = source.takenAt;
        if (typeof source?.lat === 'number' && Number.isFinite(source.lat)) meta.lat = source.lat;
        if (typeof source?.lng === 'number' && Number.isFinite(source.lng)) meta.lng = source.lng;
        map.set(photo.id, meta);
      });
    });
  });
  return map;
}

/** 生成 bundle。`exportedAt` 缺省 = 当前时刻。 */
export function buildJourneyBundle(
  trip: Trip,
  photoMeta: Map<string, JourneyPhotoLike>,
  exportedAt?: string,
): JourneyBundle {
  return bundleFromTrip(trip, photoMeta, exportedAt ? { exportedAt } : {});
}
