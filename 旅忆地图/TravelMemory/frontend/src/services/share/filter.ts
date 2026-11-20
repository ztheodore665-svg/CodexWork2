/** 可见性四层层叠过滤：**屏蔽 = 不打包**。
 *
 * ## 为什么必须在 pack 之前过滤
 *
 * 另一种做法是"全打包 + 打隐藏标记"。那样包里会留下**存在但不可见**的痕迹：
 * 收件人虽然看不到内容，却能数出隐藏了多少条、在哪个位置 —— 相当于把"我刻意
 * 不给你看的东西的分布"泄漏出去（规范 §13.9）。所以屏蔽的语义是
 * **从输入里彻底删掉**，包里连一个字节都不留。
 *
 * ## 四层的顺序与短路
 *
 * ```
 * Day（隐藏 → 整天下面的地点全丢）
 *   └ Place（隐藏 → 这个地点丢；可见照片数为 0 → 也丢）
 *       └ Photo（隐藏 → 这张丢）
 *           └ Caption（隐藏 → 照片保留，配文不打包）
 * ```
 *
 * 最后一条是唯一"部分丢弃"的层：配文单独屏蔽时照片仍然打包，
 * 因为对收件人来说"有图无字"是可接受的降级，而"无图有字"没有意义。
 *
 * ## 纯函数
 *
 * 本文件不碰 IndexedDB、不碰网络、不读 `Date.now()` —— 输入相同必得相同输出，
 * 打包前的"可见 X 个地点 / Y 张照片"预览与实际打包结果因此永远一致。
 */

import type { FilterPhoto, FilterPlace, FilterResult, Place, Trip, VisibilityState } from '../../types/share';

/** 空可见性：什么都不屏蔽。缺记录时用它，避免到处判空。 */
export function emptyVisibility(tripId: string, groupId: string): VisibilityState {
  return {
    id: `${tripId}::${groupId}`,
    tripId,
    groupId,
    hiddenDayIds: [],
    hiddenPlaceIds: [],
    hiddenPhotoIds: [],
    hiddenCaptionPhotoIds: [],
  };
}

/**
 * 按一组屏蔽状态过滤一次旅程。
 *
 * @param trip       完整旅程（含被屏蔽的部分）
 * @param visibility 该组的屏蔽状态；`undefined` 视为全可见
 */
export function applyVisibility(trip: Trip, visibility?: VisibilityState): FilterResult {
  const hiddenDays = new Set(visibility?.hiddenDayIds ?? []);
  const hiddenPlaces = new Set(visibility?.hiddenPlaceIds ?? []);
  const hiddenPhotos = new Set(visibility?.hiddenPhotoIds ?? []);
  const hiddenCaptions = new Set(visibility?.hiddenCaptionPhotoIds ?? []);

  const places: FilterPlace[] = [];

  for (const day of trip.days ?? []) {
    if (hiddenDays.has(day.id)) continue; // Day 层：整天下面的地点全丢

    for (const place of day.places ?? []) {
      if (hiddenPlaces.has(place.id)) continue; // Place 层

      const photos: FilterPhoto[] = [];
      for (const photo of place.photos ?? []) {
        if (hiddenPhotos.has(photo.id)) continue; // Photo 层
        photos.push({ photo, includeCaption: !hiddenCaptions.has(photo.id) }); // Caption 层
      }

      // 一个地点零可见照片就整条不打包：否则会出现空地点卡（规范 §13.10）
      if (photos.length === 0) continue;

      places.push({ place, date: day.date, photos });
    }
  }

  return { tripId: trip.id, places };
}

/** 可见地点数（组头实时预览用）。 */
export function countVisiblePlaces(result: FilterResult): number {
  return result.places.length;
}

/** 可见照片数（组头实时预览用）。 */
export function countVisiblePhotos(result: FilterResult): number {
  return result.places.reduce((sum, p) => sum + p.photos.length, 0);
}

/** 还会打包的配文数（组头预览用；屏蔽掉的配文不计）。 */
export function countVisibleCaptions(result: FilterResult): number {
  return result.places.reduce(
    (sum, p) => sum + p.photos.filter((f) => f.includeCaption && Boolean(f.photo.caption)).length,
    0,
  );
}

/** 某个 place 在当前屏蔽状态下是否还会被打包（可见性树的父级联动用）。 */
export function isPlaceVisible(trip: Trip, placeId: string, visibility?: VisibilityState): boolean {
  const result = applyVisibility(trip, visibility);
  return result.places.some((p: FilterPlace) => (p.place as Place).id === placeId);
}
