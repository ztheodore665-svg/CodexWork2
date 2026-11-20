/** 照片位置补全的**纯逻辑**（W12-2a，Track Studio 侧）。
 *
 * ## 为什么单独一个文件
 *
 * 「哪些照片缺坐标」「把点选结果写回哪一张」「写回后还剩几张」这三件事是**决策**，
 * 而不是渲染。抽成纯函数后可以在 Node 里直接断言，不必挂 jsdom + 一张真地图；
 * `LocationEditor.tsx` 只负责把地图点击事件接进来。
 *
 * ## 与主应用那份的关系
 *
 * 主应用（`frontend/src/services/location-fix/validate.ts`）存了同形状的一份。
 * 两边**刻意不共享**：track-studio 是零后端小应用、主应用吃后端 API，两边一旦
 * 反向依赖就会让小应用被迫打包主应用的网络层（阶段 9 的独立部署承诺就没了）。
 * 代价是 20 行重复代码，收益是两边的构建产物互不影响。
 *
 * ## 红线
 *
 * 坐标一律 **WGS84**（存储口径）。地图给的是 GCJ-02 时，转换发生在 `LocationEditor`
 * 的点击回调里（用 `@travelmemory/geo` 的 `gcj02ToWgs84`），**不在这里** ——
 * 这个文件不认识任何地图坐标系，只认数字合不合法。
 */

/** 待补全的一张照片（两处共用形状）。 */
export interface LocationFixItem {
  photoId: string;
  name: string;
  /** 缩略图 dataURL；空串表示没有（调用方退回占位图）。 */
  thumbnailDataUrl: string;
  lat?: number;
  lng?: number;
}

/** 纬度上下限（度）。 */
const LAT_LIMIT = 90;
/** 经度上下限（度）。 */
const LNG_LIMIT = 180;

/**
 * 经纬度是否可用。
 *
 * 全量校验（类型 + 有限性 + 范围），因为 UI 上一条脏数据会**静默**变成
 * 「补标了但轨迹上什么都没有」—— 高德对越界坐标不渲染也不报错。
 *
 * @param lat 纬度，接受 `unknown`：调用方可能拿到 `undefined`/字符串/`NaN`
 * @param lng 经度
 */
export function isValidLatLng(lat: unknown, lng: unknown): boolean {
  if (typeof lat !== 'number' || typeof lng !== 'number') return false;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  if (lat < -LAT_LIMIT || lat > LAT_LIMIT) return false;
  if (lng < -LNG_LIMIT || lng > LNG_LIMIT) return false;
  return true;
}

/** 一张照片是否已带可用坐标（缺失、字符串、`NaN`、越界都算"没有"）。 */
function hasCoords(photo: { lat?: number; lng?: number }): boolean {
  return isValidLatLng(photo.lat, photo.lng);
}

/**
 * 把点选结果写回一张照片，返回**新数组**（纯函数，不改入参）。
 *
 * 非法坐标 / 找不到 `photoId` 一律返回内容不变的副本 —— 调用方可以无脑
 * `setPhotos(prev => applyLocation(prev, ...))`，不需要自己再判一次。
 *
 * @param photos  当前照片列表（`SystemPhoto[]` 即可：只要带 `id` 与可选 `lat/lng`）
 * @param photoId 目标照片
 * @param lat     纬度（WGS84）
 * @param lng     经度（WGS84）
 */
export function applyLocation<T extends { id: string; lat?: number; lng?: number }>(
  photos: readonly T[],
  photoId: string,
  lat: number,
  lng: number,
): T[] {
  const next = photos.slice();
  if (!isValidLatLng(lat, lng)) return next;
  if (typeof photoId !== 'string' || photoId.length === 0) return next;
  const index = next.findIndex((photo) => photo.id === photoId);
  if (index < 0) return next;
  next[index] = { ...next[index], lat, lng };
  return next;
}

/** 仍缺坐标的照片 id（顺序与入参一致）。 */
export function pendingLocationIds<T extends { id: string; lat?: number; lng?: number }>(
  photos: readonly T[],
): string[] {
  return photos.filter((photo) => !hasCoords(photo)).map((photo) => photo.id);
}

/**
 * 下一张该补的照片 id。
 *
 * 顺序规则（与左栏列表一致，补起来不会"跳来跳去"）：
 *  1. 当前这张若**仍**缺坐标（补标失败 / 还没点地图）→ 继续选它；
 *  2. 否则取列表里**它后面**的第一张待补；
 *  3. 后面没有了就回绕到第一张待补。
 *
 * @param photos    当前照片列表
 * @param currentId 刚刚处理完的那张（`null` = 还没选过）
 * @returns 没有待补项时返回 `null`
 */
export function nextPendingId<T extends { id: string; lat?: number; lng?: number }>(
  photos: readonly T[],
  currentId: string | null,
): string | null {
  const pending: { id: string; index: number }[] = [];
  photos.forEach((photo, index) => {
    if (!hasCoords(photo)) pending.push({ id: photo.id, index });
  });
  if (pending.length === 0) return null;
  if (currentId === null) return pending[0].id;
  if (pending.some((item) => item.id === currentId)) return currentId;

  const currentIndex = photos.findIndex((photo) => photo.id === currentId);
  if (currentIndex < 0) return pending[0].id;
  const after = pending.find((item) => item.index > currentIndex);
  return (after ?? pending[0]).id;
}

/** 待补列表（`LocationFixItem[]`）：只有缺坐标的照片会进来。 */
export function toLocationFixItems(
  photos: readonly { id: string; name: string; thumbnailDataUrl: string; lat?: number; lng?: number }[],
): LocationFixItem[] {
  return photos
    .filter((photo) => !hasCoords(photo))
    .map((photo) => {
      // 只有 lat 没有 lng 属于脏数据：算"缺坐标"，且**不把半截坐标带出去**
      // （半截坐标一旦被 UI 当成"已选"，用户会以为这张已经补好了）
      const usable = isValidLatLng(photo.lat, photo.lng);
      return {
        photoId: photo.id,
        name: photo.name,
        thumbnailDataUrl: photo.thumbnailDataUrl ?? '',
        ...(usable ? { lat: photo.lat as number, lng: photo.lng as number } : {}),
      };
    });
}

/** 地图初始视野：已定位照片的重心；一张都没有时看全国（让用户自己找）。 */
export interface InitialView {
  center: { lng: number; lat: number };
  zoom: number;
}

/** 全国视野的中心（与主应用 `DEFAULT_MAP_CENTER` 同值，各自声明互不依赖）。 */
const FALLBACK_CENTER = { lng: 104.195, lat: 35.861 };
/** 有参考点时的缩放：能看清区县与主要道路。 */
const LOCATED_ZOOM = 11;
/** 一张参考点都没有时的缩放：中国全貌。 */
const EMPTY_ZOOM = 4;

/**
 * 计算补全页地图的初始视野。
 *
 * @param photos 全部照片（含已定位的：它们能告诉我们"大概在哪座城市"）
 */
export function initialViewOf(photos: readonly { lat?: number; lng?: number }[]): InitialView {
  let sumLng = 0;
  let sumLat = 0;
  let count = 0;
  for (const photo of photos) {
    if (!isValidLatLng(photo.lat, photo.lng)) continue;
    sumLng += photo.lng as number;
    sumLat += photo.lat as number;
    count += 1;
  }
  if (count === 0) return { center: { ...FALLBACK_CENTER }, zoom: EMPTY_ZOOM };
  return { center: { lng: sumLng / count, lat: sumLat / count }, zoom: LOCATED_ZOOM };
}
