/** 照片导入的**纯状态机**（阶段 6 UI 层，不碰 DOM、不碰网络）。
 *
 * ## 为什么把导入状态抽成纯函数
 *
 * 「导入照片」这个动作有四个态（idle / uploading / done / error）、三个计数器、
 * 一条要给用户看的中文提示，而且**失败之后必须能再来一次**。写在组件里就是一堆
 * `setState({ ...prev, ... })` 散落在 `try/catch` 的两个分支里，最容易出的 bug 是
 * 失败后再点一次按钮卡在 error 态不动（忘了把 status 拨回 uploading）。
 * 抽成 `reduceImportState` 之后，这条"不会卡死"的规则有了自己的测试。
 *
 * ## 组件里剩下的那部分副作用
 *
 * 本文件只管状态是怎么变的。真正弹选择器、读缩略图字节、发 multipart、
 * 弹 toast 的都在 `MapActionPanel.tsx` —— 那里必须碰 DOM，也就没法进单测，
 * 所以才要把能测的都搬到这里。
 */

import type { ImportedPhoto, ImportPhotosResult } from '../../services/api/photos';

/** 导入进度快照。UI 直接拿它渲染按钮文案与提示条。 */
export interface ImportOutcome {
  status: 'idle' | 'uploading' | 'done' | 'error';
  /** 给用户看的一句中文。success 时是「已导入 N 张照片」，error 时是「导入失败：…」。 */
  message: string;
  imported: number;
  duplicates: number;
  failed: number;
  /** 后端返回的照片 id，顺序与 `ImportPhotosResult.photos` 一致。 */
  photoIds: string[];
}

export type ImportEvent =
  | { type: 'start' }
  | { type: 'success'; result: ImportPhotosResult }
  | { type: 'error'; message: string };

/** 初始态。导出成常量而不是让调用方手写对象字面量：
 *  `useState<ImportOutcome>(IDLE_IMPORT)` 与"复位"共用同一份，避免两处漂移。 */
export const IDLE_IMPORT: ImportOutcome = {
  status: 'idle',
  message: '',
  imported: 0,
  duplicates: 0,
  failed: 0,
  photoIds: [],
};

/** 给 `success` 事件配的提示语。
 *
 * 三个计数器都报：只说 imported 的话，用户会以为另外 4 张"丢了"，
 * 其实是重复（库里已经有了）或后端解析失败。这两件事用户需要知道，但不需要弹窗打断。
 */
export function describeImportSuccess(result: ImportPhotosResult): string {
  const parts = [`已导入 ${result.imported} 张照片`];
  if (result.duplicates > 0) parts.push(`跳过 ${result.duplicates} 张重复`);
  if (result.failed > 0) parts.push(`${result.failed} 张解析失败`);
  return parts.join('，');
}

/** 失败提示。
 *
 * 消息里**已经含**「导入失败」时不重复加：`api/photos.ts` 抛的是
 * 「照片导入失败：HTTP 404 —— …」，直接拼前缀会得到"导入失败：照片导入失败：…"
 * 这种话痨文案。判断用 `includes` 而不是 `startsWith`，就是为了接住这一类
 * 各客户端自己带的前缀。
 */
export function describeImportFailure(message: string): string {
  const text = message.trim();
  if (text.length === 0) return '导入失败：未知错误';
  return text.includes('导入失败') ? text : `导入失败：${text}`;
}

/**
 * 导入状态机。
 *
 * - `start`：无条件回到 uploading 并清零。**这是"失败后能重试"的全部实现** ——
 *   不读 `prev.status`，所以从 error 也能走出来。
 * - `success`：落到 done，计数与 photoIds 全部来自后端返回值（前端不自己数文件数，
 *   后端会把重复与失败的分开）。
 * - `error`：落到 error，计数清零（保留上一次的计数只会让人误会"这次导进去了一部分"）。
 */
export function reduceImportState(prev: ImportOutcome, event: ImportEvent): ImportOutcome {
  void prev; // 三个分支都不依赖上一次的状态，显式标注意图而不是删掉参数
  switch (event.type) {
    case 'start':
      return { ...IDLE_IMPORT, status: 'uploading', message: '上传中…' };
    case 'success':
      return {
        status: 'done',
        message: describeImportSuccess(event.result),
        imported: event.result.imported,
        duplicates: event.result.duplicates,
        failed: event.result.failed,
        photoIds: event.result.photos.map((photo) => photo.id),
      };
    case 'error':
      return { ...IDLE_IMPORT, status: 'error', message: describeImportFailure(event.message) };
  }
}

/** 缩略图长边。与 `ThumbnailStore` 的 key 一起用：`${photoId}:256`。 */
export const THUMB_MAX_EDGE = 256;

/** 一张"要存进缩略图库"的缩略图。 */
export interface ThumbAssignment {
  /** **后端 photo id**（不是相册 assetId）—— 后面「生成信物」按它取图。 */
  photoId: string;
  bytes: ArrayBuffer;
}

/**
 * 把「按上传文件下标排好的缩略图」对上后端返回的照片。
 *
 * ## 为什么不能直接用下标
 *
 * 后端 `photos` 只含**新导入**的照片：重复的（`duplicates`）和解析失败的（`failed`）
 * 都不在里面。一批里只要混进 1 张重复，后面的下标就整体错位 —— 缩略图会张冠李戴，
 * 而错位在 UI 上的表现只是"某张图看着不对"，几乎不会被当成 bug 报上来。
 *
 * ## 那用什么配对
 *
 * `ImportedPhoto.hash` 是后端算的内容指纹（sha256 全串；Web 端 `assetId` 就是它的
 * 前 32 位）。前端对同一份字节算一遍 sha256 就能精确对上，重复/失败都不影响。
 *
 * 指纹拿不到时（`crypto.subtle` 只在安全上下文存在，局域网 http 下是 undefined）
 * 才退回下标，且**只在 `photos.length === 文件数` 这一种必然不会错位的情形下退**。
 */
export function assignThumbs(
  photos: readonly ImportedPhoto[],
  hashes: readonly (string | null)[],
  thumbs: readonly (ArrayBuffer | null)[],
): ThumbAssignment[] {
  const byHash = new Map<string, ArrayBuffer>();
  const limit = Math.min(hashes.length, thumbs.length);
  for (let i = 0; i < limit; i += 1) {
    const hash = hashes[i];
    const bytes = thumbs[i];
    if (hash && bytes) byHash.set(hash, bytes);
  }

  // 没有重复也没有失败时，photos 与 files 一一对应，下标是可靠的
  const indexSafe = photos.length === thumbs.length;

  const out: ThumbAssignment[] = [];
  photos.forEach((photo, index) => {
    const hit = photo.hash ? byHash.get(photo.hash) : undefined;
    const bytes = hit ?? (indexSafe ? thumbs[index] : undefined);
    if (bytes) out.push({ photoId: photo.id, bytes });
  });
  return out;
}

/** 过滤出真正的图片文件。
 *
 * 为什么看 `type` 而不是扩展名：`<input accept="image/*">` 只是给选择器的**提示**，
 * 用户切到"所有文件"照样能选到 .pdf；而 `File.type` 是浏览器按内容嗅探出来的。
 * `type` 为空串的老浏览器/特殊来源一律放行，交给后端 `failed` 计数器兜底。
 */
export function filterImageFiles(files: readonly File[]): File[] {
  return files.filter((file) => !file.type || file.type.startsWith('image/'));
}
