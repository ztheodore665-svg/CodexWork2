/** 每段交通工具的编辑模型（W14-3a）。
 *
 * ## 为什么单独抽一个纯函数
 *
 * 「哪两张照片之间算一段」这件事同时被两处消费：补点页的 `<select>`（要渲染行）
 * 与应用侧（要喂给 `toTrailPhotos({ transports })`）。如果各写一遍，最常见的
 * 偏差是**下标错一位** —— UI 上第 1 段写着"火车"，播放时却是第 2 段火车。
 * 这种 bug 在界面上没有任何异常表现，只能靠单测钉住，所以规则放在这里、
 * 用 `transportPlan.test.ts` 直接断言。
 *
 * ## 规则（照抄浅色版 `ui/EditPanel.tsx` 的语义）
 *
 * 1. 只认**带坐标**的照片：没有 GPS 的照片不进轨迹（`packages/trail` 会跳过），
 *    给它配"到下一段"的交通工具是无意义的，也会让用户以为它画在线上；
 * 2. 按 `takenAt` **升序**排序（轨迹也是这个顺序；排序口径与 `toTrailPhotos` 一致）；
 * 3. 相邻两张都带坐标才算一段，最后一张之后没有段；
 * 4. 段的 key 用**出发照片的 id** —— 与 `Photo.transportToNext` 的存放位置一致
 *    （`core/photoImport.ts` 就是 `transports.get(photo.id)`），
 *    所以这个 map 可以不转换直接交给 `toTrailPhotos`。
 */

import type { TransportMode } from '@travelmemory/trail';
import type { SystemPhoto } from '@travelmemory/photo-source';

import { DEFAULT_TRANSPORT } from '@travelmemory/trail';

/** 一段路：从 `fromId` 到 `toId`。 */
export interface TransportSegment {
  /** 段的稳定标识：出发照片的 id（= `Photo.transportToNext` 的索引键）。 */
  key: string;
  /** 出发照片 id。 */
  fromId: string;
  /** 到达照片 id。 */
  toId: string;
  /** 段在行程里的序号（0 起），用于 UI 上的「第 N 段」。 */
  index: number;
  /** 出发地的展示名（缩略图侧栏用）。 */
  fromName: string;
  /** 到达地的展示名。 */
  toName: string;
  /** 当前生效的交通工具（用户没选过就是默认自驾）。 */
  mode: TransportMode;
}

/** 一张照片是否有可用坐标（与 `core/validate` 的判定口径一致：有限数字）。 */
function hasCoords(photo: SystemPhoto): boolean {
  const { lat, lng } = photo;
  return (
    typeof lat === 'number' &&
    Number.isFinite(lat) &&
    typeof lng === 'number' &&
    Number.isFinite(lng)
  );
}

/** `takenAt` → 毫秒；解析不出来按 0（与补点页的排序兜底一致）。 */
function timeOf(photo: SystemPhoto): number {
  const parsed = Date.parse(photo.takenAt ?? '');
  return Number.isFinite(parsed) ? parsed : 0;
}

/** 只保留带坐标的照片，按拍摄时间升序（时间相同保持原顺序，便于复现）。 */
export function locatedPhotosInOrder(photos: readonly SystemPhoto[]): SystemPhoto[] {
  return photos
    .map((photo, index) => ({ photo, index, time: timeOf(photo) }))
    .filter((row) => hasCoords(row.photo))
    .sort((a, b) => (a.time === b.time ? a.index - b.index : a.time - b.time))
    .map((row) => row.photo);
}

/**
 * 「相邻照片对 + 用户选过的模式」→ 每段一条记录。
 *
 * @param photos 当前内存里的照片（顺序无所谓，内部会排）
 * @param transports 用户选过的模式，按**出发照片 id** 索引
 */
export function buildTransportSegments(
  photos: readonly SystemPhoto[],
  transports: Readonly<Record<string, TransportMode>> = {},
): TransportSegment[] {
  const ordered = locatedPhotosInOrder(photos);
  const segments: TransportSegment[] = [];
  for (let index = 0; index + 1 < ordered.length; index += 1) {
    const from = ordered[index];
    const to = ordered[index + 1];
    segments.push({
      key: from.id,
      fromId: from.id,
      toId: to.id,
      index,
      fromName: nameOf(from),
      toName: nameOf(to),
      mode: transports[from.id] ?? DEFAULT_TRANSPORT,
    });
  }
  return segments;
}

/** 文件名 → 展示名（去掉扩展名；与 `core/photoImport` 的 `placeNameFromFileName` 同口径）。 */
function nameOf(photo: SystemPhoto): string {
  const trimmed = typeof photo.name === 'string' ? photo.name.trim() : '';
  const dot = trimmed.lastIndexOf('.');
  const base = dot > 0 ? trimmed.slice(0, dot) : trimmed;
  return base.trim().length > 0 ? base.trim() : '未命名';
}
