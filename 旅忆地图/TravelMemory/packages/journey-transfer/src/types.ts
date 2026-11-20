/** `journey.json` 的线格式类型与运行时白名单。
 *
 * ## 为什么这个包不 import 主应用的 `Trip` 类型（§11.2 / B13）
 *
 * `JourneyBundle` 是**跨应用的线格式**：主应用（`frontend/`）把它序列化成 JSON，
 * 独立小应用（`apps/trailshare-standalone/`）把它写进文件，再由主应用导回。
 * 一旦这里 import `frontend/src/types/share.ts` 的 `Trip`，两个后果立刻出现：
 *
 * 1. 包之间出现反向依赖（packages → frontend），构建顺序被锁死；
 * 2. 主应用改一个 `Place` 字段，这个包跟着变异步编译错误，而**线格式本身没变**。
 *
 * 所以这里用 `unknown` + 运行时校验（见 `export.ts`）。代价是校验代码要手写，
 * 收益是两端的耦合只发生在这份类型定义上。
 *
 * ## 关于原始字节
 *
 * `JourneyBundle` **不含原图字节**（§6.3）：只有缩略图 data URL 与元数据。
 * `JourneyPhoto` 里刻意没有 `bytes` / `image` 字段 —— 想往里写字节必须先改类型，
 * 这是防止"顺手把 ArrayBuffer 塞进去"的最后一道闸。
 */

/** 线格式版本。改这个常量 = 放弃对旧文件的兼容，必须同步改 `tripFromBundle` 的报错文案。 */
export const JOURNEY_BUNDLE_VERSION = 'journey-2026.1' as const;

/** 单张缩略图 base64 解码后的字节上限（§6.3）。
 *
 * 与 `@travelmemory/photo-source` 里的同名常量**必须一致**：两个包零依赖（§5），
 * 无法互相 import，只能各自持有一份副本。改这里时记得同步那边。 */
export const MAX_THUMBNAIL_BYTES = 200_000;

/** 六类标签的运行时白名单。顺序与主应用 `ALL_TAGS` 一致（顺序是协议的一部分）。 */
export const TAG_VALUES = [
  'heritage',
  'nature',
  'watertown',
  'metropolis',
  'food',
  'leisure',
] as const;

/** 三档强度的运行时白名单。 */
export const INTENSITY_VALUES = ['low', 'mid', 'high'] as const;

export type JourneyTag = (typeof TAG_VALUES)[number];
export type JourneyIntensity = (typeof INTENSITY_VALUES)[number];

export interface JourneyPhoto {
  id: string;
  /** base64 data URL，解码后 ≤ `MAX_THUMBNAIL_BYTES`。 */
  thumbnailDataUrl: string;
  caption?: string;
  /** ISO UTC（B14）。EXIF 里的本地墙钟在导入时已换算成 UTC。 */
  takenAt?: string;
  /** WGS84。 */
  lat?: number;
  /** WGS84。 */
  lng?: number;
}

export interface JourneyPlace {
  id: string;
  name: string;
  /** "上午"/"下午"之类的自由文本。 */
  label?: string;
  tag: JourneyTag;
  intensity: JourneyIntensity;
  photos: JourneyPhoto[];
}

export interface JourneyDay {
  id: string;
  /** `YYYY-MM-DD`（B14），本地日期，不带时区。 */
  date: string;
  places: JourneyPlace[];
}

export interface JourneyBundle {
  version: typeof JOURNEY_BUNDLE_VERSION;
  title: string;
  days: JourneyDay[];
  /** ISO UTC 时间戳。 */
  exportedAt: string;
}

/** 导出侧需要的最小照片形状。
 *
 * 定义在这里而不是 import `@travelmemory/photo-source` 的 `SystemPhoto`，
 * 是为了让「结构上兼容」代替「包依赖」：`SystemPhoto` 有 `bytes` 等多余字段，
 * 在 TS 里可以原样赋给 `JourneyPhotoLike`，于是调用方不需要做任何转换，
 * 而本包对 photo-source 一无所知（§5 依赖方向：两者互不依赖）。
 */
export interface JourneyPhotoLike {
  thumbnailDataUrl: string;
  takenAt?: string;
  lat?: number;
  lng?: number;
}
