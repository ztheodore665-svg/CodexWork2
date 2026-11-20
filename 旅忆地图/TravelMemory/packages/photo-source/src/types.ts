/** 照片来源的对外类型（§12）。
 *
 * ## 这个包为什么存在
 *
 * 两个独立小应用（Track Studio / TrailShare Standalone）都有"选照片"这一步，
 * 而且都有**两个入口**：
 *
 * | 入口 | 单独打开 | 被主应用 iframe 嵌入 |
 * |---|---|---|
 * | 系统相册 | ✅ 唯一入口 | ✅ 仍可用 |
 * | 主应用相册 | ❌ 置灰 | ✅ 主应用 postMessage 注入 |
 *
 * 两个应用各写一遍 `SystemPhoto` 与"读 EXIF + 压缩略图"的流程，必然出现
 * "同一个文件在 A 里 takenAt 是 UTC、在 B 里是本地时间"这种偏差。
 * 所以抽成这个包：**类型与读取流程只有一份**。
 *
 * ## 与 `@travelmemory/journey-transfer` 的关系
 *
 * 零依赖、单向：`photo-source` 不认识 `journey-transfer`，反之亦然。
 * `SystemPhoto` 在结构上满足对方的 `JourneyPhotoLike`（多了 `bytes` / `name`），
 * 于是导出侧直接把 `Map<string, SystemPhoto>` 传进去就能用，不需要转换层。
 * 两份 `MAX_THUMBNAIL_BYTES` 是刻意的复制，见下面的注释。
 */

/** 单张缩略图 base64 解码后的字节上限（§6.3）。
 *
 * 与 `@travelmemory/journey-transfer` 里的同名常量**必须一致**：两个包零依赖（§5），
 * 无法互相 import，只能各持一份副本。改这里时记得同步那边，否则会出现
 * "缩略图生成时合格、导出时被丢"的诡异现象。 */
export const MAX_THUMBNAIL_BYTES = 200_000;

/** 一张已经读进内存的照片。**不持久化**：关掉页面即丢（§6.1）。 */
export interface SystemPhoto {
  /** 运行时生成，不持久。形如 `p_1a2b3c4d5e6f7a8b`。 */
  id: string;
  /** 原始文件字节。只在内存里活着，进 `journey.json` 的永远只有缩略图。 */
  bytes: ArrayBuffer;
  name: string;
  /** ISO UTC（B14）。EXIF 里的本地墙钟在读取时已换算成 UTC；读不到就不填。 */
  takenAt?: string;
  /** WGS84。读不到就不填。 */
  lat?: number;
  /** WGS84。读不到就不填。 */
  lng?: number;
  /** base64 data URL，解码后 ≤ `MAX_THUMBNAIL_BYTES`。 */
  thumbnailDataUrl: string;
}

/** 一个可切换的照片来源。UI 只认这个接口，不关心照片从哪来。 */
export interface PhotoSource {
  id: 'system' | 'library';
  /** 按钮文案（中文，A6）。 */
  label: string;
  /** 是否可用。`false` 时 UI 置灰并显示提示文案。 */
  available(): boolean;
  /** 让用户挑照片。用户取消时 resolve `[]`，**不抛错**。 */
  pick(): Promise<SystemPhoto[]>;
}

/** 选择参数。
 *
 * `limit` —— 最多选几张。缺省 = 不限。
 * `maxEdge` —— 缩略图长边上限（px）。缺省 = 480（B2）。
 *
 * TODO(歧义): §12 只定义了 `SystemPhoto` 与 `PhotoSource`，没定义 `PickOptions`。
 *  按最小实现给出这两个字段；将来需要"只允许 png"之类的约束再加。
 */
export interface PickOptions {
  limit?: number;
  maxEdge?: number;
}
