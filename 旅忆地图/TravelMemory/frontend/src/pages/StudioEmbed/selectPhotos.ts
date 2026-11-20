/** 按模式挑出"要传给小应用的照片"（阶段 11）。
 *
 * 抽成纯函数而不是写在组件里，是为了让"share 模式传哪几张、什么顺序"这条规则
 * 能被单测钉住 —— 它是本阶段唯一一处**会猜用户意图**的逻辑：
 * track 模式要的是整个照片库（用户自己再在小应用里挑），
 * share 模式要的是这趟行程里的照片（顺序就是用户排列的顺序）。
 *
 * 顺序为什么重要：`assignTripPhotos` 的 `ord` 就是传入顺序，
 * 小应用按传入顺序展开成轨迹点，顺序错了回放会走回头路。
 */

import type { PhotoListItem } from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import type { StudioMode } from '../../services/studio-bridge/types';

/**
 * 挑照片。
 *
 * @param mode `track` = 全部照片库；`share` = 仅该行程的照片
 * @param trip share 模式的行程（`track` 模式可不传）
 * @param library 主应用的照片库（后端 `GET /api/v1/photos` 的结果）
 * @returns 副本数组（调用方可以随便排序/过滤，不会改到 props）
 *
 * share 模式下有两种照片会被丢掉，都不算错误：
 * 1. `trip.items` 里存在但照片库里没有的（照片被删了、行程是旧快照）；
 * 2. 同一张照片在行程里出现多次（只保留第一次，`postMessage` 的 transfer 不能被同一
 *    `ArrayBuffer` 移交两次；一张照片进两个地点是可能的人为操作）。
 */
export function selectStudioPhotos(
  mode: StudioMode,
  trip: TripView | undefined,
  library: readonly PhotoListItem[],
): PhotoListItem[] {
  if (mode === 'track') return [...library];

  const byId = new Map<string, PhotoListItem>();
  for (const photo of library) {
    // 照片库理论上不会重复 id；万一重复，先出现的那条为准（与列表渲染一致）
    if (!byId.has(photo.id)) byId.set(photo.id, photo);
  }

  const out: PhotoListItem[] = [];
  const used = new Set<string>();
  for (const item of trip?.items ?? []) {
    const photoId = item.photoId;
    if (typeof photoId !== 'string' || photoId.length === 0) continue;
    if (used.has(photoId)) continue;
    const photo = byId.get(photoId);
    if (!photo) continue;
    used.add(photoId);
    out.push(photo);
  }
  return out;
}
