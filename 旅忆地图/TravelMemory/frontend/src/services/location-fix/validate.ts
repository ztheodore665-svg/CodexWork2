/** 照片位置补全的**纯逻辑**（W12-2b，主应用侧）。
 *
 * ## 为什么单独一个文件
 *
 * 「哪些照片待补」「点完之后剩几张」「坐标合不合法」都是**决策**，
 * 必须有测试锁死；而 `LocationEditor.tsx` 一 import 就摸高德与 IndexedDB，
 * 在 Node（vitest）里跑不起来。分开之后，纯逻辑这一层可以在 node 环境里直接断言。
 *
 * ## 与 Track Studio 那份的关系
 *
 * `apps/track-studio/src/pages/location/locationFix.ts` 存了同形状的一份。
 * 两边**刻意不共享**：track-studio 是零后端小应用（会被单独部署、单独打包），
 * 主应用这边吃后端 API（`PhotoListItem` 带 `hasLocation` / `caption`）。
 * 强行合并会让小应用被迫依赖主应用的网络层与类型，阶段 9 的"可独立部署"承诺就废了。
 * 重复的是 20 行校验代码，换来的是两边构建产物互不影响。
 *
 * ## 红线
 *
 * 主应用的 `PhotoListItem` 坐标是 **WGS84**（`coordSystem`），高德点击给的是
 * GCJ-02 —— 转换必须发生在调用方的点击回调里（`gcj02ToWgs84`），**不在这里**。
 * 本文件只认数字合不合法。
 */

import type { PhotoListItem } from '../api/photos';

/** 待补全的一张照片（两处共用形状）。 */
export interface LocationFixItem {
  photoId: string;
  name: string;
  /** 缩略图 dataURL。主应用侧恒为空串：缩略图在 IndexedDB 里，
   *  只能通过 `getOrCreateThumbUrl(photoId)` 异步取（见 `LocationEditor.tsx`）。 */
  thumbnailDataUrl: string;
  lat?: number;
  lng?: number;
}

/** 纬度上下限（度）。 */
const LAT_LIMIT = 90;
/** 经度上下限（度）。 */
const LNG_LIMIT = 180;

/**
 * 经纬度是否可用（类型 + 有限性 + 范围）。
 *
 * 为什么范围也要前端验一次：后端 `PATCH /photos/{id}/location` 会 422，
 * 但那时用户已经点完地图、等了一个来回。越界坐标在高德上**静默不渲染**，
 * 表现为"补了但地图上什么都没有"，这种失败没有任何报错。
 *
 * @param lat 纬度（`unknown`：上游可能给 `null` / 字符串 / `NaN`）
 * @param lng 经度
 */
export function isValidLatLng(lat: unknown, lng: unknown): boolean {
  if (typeof lat !== 'number' || typeof lng !== 'number') return false;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  if (lat < -LAT_LIMIT || lat > LAT_LIMIT) return false;
  if (lng < -LNG_LIMIT || lng > LNG_LIMIT) return false;
  return true;
}

/**
 * 待补列表：后端明确说 `hasLocation === false` 的照片。
 *
 * **不拿 `lat` 判空**：只有 lat 没有 lng 是可能的脏数据，判空会让 UI
 * 显示"已定位"而地图上缺一个点（同 `PhotoListItem.hasLocation` 的注释）。
 *
 * @param photos 照片库
 */
export function selectPendingPhotos(photos: readonly PhotoListItem[]): PhotoListItem[] {
  return photos.filter((photo) => !photo.hasLocation);
}

/** 待补数量（给按钮上的计数用）。 */
export function pendingCount(photos: readonly PhotoListItem[]): number {
  return selectPendingPhotos(photos).length;
}

/**
 * 从待补列表里移除一张（补标成功后的本地更新，纯函数）。
 *
 * 找不到 `photoId` 时返回内容不变的副本 —— 调用方不需要先判存在。
 *
 * @param photos  待补列表
 * @param photoId 已补标成功的照片
 */
export function removePhotoById(
  photos: readonly PhotoListItem[],
  photoId: string,
): PhotoListItem[] {
  return photos.filter((photo) => photo.id !== photoId);
}

/** 列表里显示的名字：有 caption 用 caption，否则用 id（主应用没有文件名）。 */
export function displayNameOf(photo: PhotoListItem): string {
  const caption = typeof photo.caption === 'string' ? photo.caption.trim() : '';
  return caption.length > 0 ? caption : photo.id;
}

/**
 * `PhotoListItem[]` → `LocationFixItem[]`（只保留待补的）。
 *
 * @param photos 照片库
 */
export function toLocationFixItems(photos: readonly PhotoListItem[]): LocationFixItem[] {
  // 待补项按定义**没有**可用坐标（`hasLocation === false`），所以这里不塞 lat/lng：
  // 塞半截坐标只会让 UI 误以为"这张已经补好了"。
  return selectPendingPhotos(photos).map((photo) => ({
    photoId: photo.id,
    name: displayNameOf(photo),
    thumbnailDataUrl: '',
  }));
}
