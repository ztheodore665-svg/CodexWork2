/** 系统相册来源：`<input type="file">` + EXIF + 缩略图。
 *
 * ## 流程
 *
 * ```
 * 用户点按钮 → 隐藏 input.click() → 用户选文件 → change
 *   → 每个 File: 读字节（ArrayBuffer） → 读 EXIF（时间 + 坐标） → 压缩略图
 *   → SystemPhoto（纯内存对象，不写任何存储）
 * ```
 *
 * ## 让测试能跑的设计
 *
 * 真实的"自动生成缩略图"要解码图片（`createImageBitmap` 或 `<img>` + objectURL），
 * jsdom 两样都不会真的完成。所以解码器与 canvas 全部走依赖注入：
 * 测试只验证"流程跑通、字段结构对"，不验证像素。
 *
 * ## 用户取消
 *
 * 取消文件选择器在浏览器里**没有可靠的统一事件**：
 * - 现代 Chrome / Safari 支持 `cancel` 事件；
 * - 老浏览器要在 `window.focus` 之后回探 `input.files.length`。
 *
 * 两条路都接上，先到先算，最终都 resolve `[]`（不抛错）。
 */

import { readExifMeta, type ExifMeta } from './exif';
import {
  FALLBACK_THUMBNAIL_DATA_URL,
  THUMBNAIL_MAX_EDGE,
  compressToThumbnail,
  type ThumbnailSourceLike,
} from './thumbnail';
import type { PhotoSource, PickOptions, SystemPhoto } from './types';

/** 等 `<img>` 解码的上限。超时就当解不出来（用兜底缩略图），不让 UI 一直转圈。 */
const IMAGE_DECODE_TIMEOUT_MS = 3_000;
/** `window.focus` 之后等多久回探 `input.files`，判断用户是不是取消了。 */
const CANCEL_PROBE_DELAY_MS = 500;

/** 单文件读取流程的依赖注入点。 */
export interface PhotoFileDeps {
  /** 读原始字节。缺省用 `Blob.arrayBuffer()`，退化到 `FileReader`。 */
  readBytes?: (file: Blob) => Promise<ArrayBuffer>;
  /** 读 EXIF。缺省 `readExifMeta`。 */
  readExif?: (bytes: ArrayBuffer) => Promise<ExifMeta>;
  /** 生成缩略图。缺省走 `createImageBitmap` / `<img>` + canvas。 */
  createThumbnail?: (file: Blob, maxEdge: number) => Promise<string>;
  /** 生成运行时 id。缺省 `createRuntimePhotoId()`。 */
  createId?: () => string;
  /** 缩略图长边上限。缺省 480。 */
  maxEdge?: number;
}

/** `pickFromSystem` 的额外注入点（测试用）。 */
export interface PickSystemDeps extends PhotoFileDeps {
  /** 建 input 元素。缺省建一个隐藏的 `multiple accept="image/*"`。 */
  createInput?: () => HTMLInputElement;
  /** 等用户选完。缺省 `openPicker`（change / cancel / focus 回探三路监听）。 */
  waitForSelection?: (input: HTMLInputElement) => Promise<File[]>;
}

/** 运行时照片 id：`p_` + 16 个 hex。**不持久化**，刷新即变（§6.1）。 */
export function createRuntimePhotoId(): string {
  const bytes = new Uint8Array(8);
  const crypto_ = (globalThis as { crypto?: Crypto }).crypto;
  if (crypto_ && typeof crypto_.getRandomValues === 'function') {
    crypto_.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  let hex = '';
  for (const byte of bytes) hex += byte.toString(16).padStart(2, '0');
  return `p_${hex}`;
}

/** 读字节：优先 `Blob.arrayBuffer()`，老环境退回 `FileReader`。 */
async function defaultReadBytes(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer();
  return await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (result instanceof ArrayBuffer) resolve(result);
      else reject(new Error('读取文件失败：结果不是 ArrayBuffer'));
    };
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'));
    reader.readAsArrayBuffer(file);
  });
}

/** 尽力解码图片以便缩放。任何一步不可用都返回 `null`（调用方退回兜底缩略图）。 */
async function decodeToDrawable(file: Blob): Promise<ThumbnailSourceLike | null> {
  const createImageBitmap_ = (
    globalThis as { createImageBitmap?: (source: Blob) => Promise<ImageBitmap> }
  ).createImageBitmap;
  if (typeof createImageBitmap_ === 'function') {
    try {
      // ImageBitmap 的 width/height 就是像素尺寸，满足 ThumbnailSourceLike。
      return (await createImageBitmap_(file)) as unknown as ThumbnailSourceLike;
    } catch {
      // 解不出来就继续试 <img>，不 return。
    }
  }
  return await decodeViaImageElement(file);
}

/** 用 `URL.createObjectURL` + `<img>` 解码。jsdom 里 `onload` 不会触发，靠超时退出。 */
async function decodeViaImageElement(file: Blob): Promise<ThumbnailSourceLike | null> {
  if (typeof Image === 'undefined' || typeof URL === 'undefined') return null;
  if (typeof URL.createObjectURL !== 'function') return null;
  let objectUrl = '';
  try {
    objectUrl = URL.createObjectURL(file);
  } catch {
    return null;
  }
  try {
    const image = new Image();
    const loaded = await new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), IMAGE_DECODE_TIMEOUT_MS);
      image.onload = () => {
        clearTimeout(timer);
        resolve(true);
      };
      image.onerror = () => {
        clearTimeout(timer);
        resolve(false);
      };
      image.src = objectUrl;
    });
    return loaded ? (image as unknown as ThumbnailSourceLike) : null;
  } finally {
    try {
      URL.revokeObjectURL(objectUrl);
    } catch {
      // revoke 失败无关紧要，忽略。
    }
  }
}

/** 缺省缩略图流程：解码 → 缩放压 JPEG → 循环降质。失败给 1×1 PNG，不抛。 */
async function defaultCreateThumbnail(file: Blob, maxEdge: number): Promise<string> {
  const drawable = await decodeToDrawable(file);
  if (!drawable) return FALLBACK_THUMBNAIL_DATA_URL;
  try {
    return await compressToThumbnail(drawable, { maxEdge });
  } catch {
    return FALLBACK_THUMBNAIL_DATA_URL;
  }
}

/** 从 `Blob` / `File` 上取文件名。`Blob` 没有 `name`，给个中性兜底。 */
function fileNameOf(file: File | Blob): string {
  const name = (file as File).name;
  return typeof name === 'string' && name.length > 0 ? name : 'photo';
}

/**
 * 把一个文件读成 `SystemPhoto`（不做任何持久化）。
 *
 * EXIF 与缩略图任何一步失败都只影响对应字段，绝不让整张照片失败 ——
 * 照片本身（`bytes`）已经读到了，就能用于轨迹回放。
 *
 * @param file 用户选中的文件
 * @param deps 依赖注入（测试用）
 */
export async function readPhotoFile(file: File | Blob, deps: PhotoFileDeps = {}): Promise<SystemPhoto> {
  const readBytes = deps.readBytes ?? defaultReadBytes;
  const readExif = deps.readExif ?? readExifMeta;
  const createThumbnail = deps.createThumbnail ?? defaultCreateThumbnail;
  const createId = deps.createId ?? createRuntimePhotoId;
  const maxEdge = deps.maxEdge ?? THUMBNAIL_MAX_EDGE;

  const bytes = await readBytes(file);

  let meta: ExifMeta = {};
  try {
    meta = await readExif(bytes);
  } catch {
    meta = {};
  }

  let thumbnailDataUrl: string;
  try {
    thumbnailDataUrl = await createThumbnail(file, maxEdge);
  } catch {
    thumbnailDataUrl = FALLBACK_THUMBNAIL_DATA_URL;
  }

  const photo: SystemPhoto = {
    id: createId(),
    bytes,
    name: fileNameOf(file),
    thumbnailDataUrl,
  };
  if (meta.takenAt !== undefined) photo.takenAt = meta.takenAt;
  if (meta.lat !== undefined) photo.lat = meta.lat;
  if (meta.lng !== undefined) photo.lng = meta.lng;
  return photo;
}

/** 建一个隐藏的多选图片 input。挂在 `document.body` 上才有 `click()` 效果。 */
function createHiddenFileInput(): HTMLInputElement {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.accept = 'image/*';
  input.style.position = 'fixed';
  input.style.left = '-9999px';
  input.style.opacity = '0';
  input.setAttribute('aria-hidden', 'true');
  input.setAttribute('data-travelmemory', 'system-picker');
  return input;
}

/**
 * 打开系统选择器并等用户选完。
 *
 * @returns 选中的文件；取消时 `[]`。
 */
export function openPicker(input: HTMLInputElement): Promise<File[]> {
  return new Promise<File[]>((resolve) => {
    let settled = false;
    const detach = (): void => {
      input.removeEventListener('change', onChange);
      input.removeEventListener('cancel', onCancel);
      if (typeof window !== 'undefined') window.removeEventListener('focus', onWindowFocus);
      input.remove();
    };
    const finish = (files: File[]): void => {
      if (settled) return;
      settled = true;
      detach();
      resolve(files);
    };
    function onChange(): void {
      finish(Array.from(input.files ?? []));
    }
    function onCancel(): void {
      finish([]);
    }
    function onWindowFocus(): void {
      // 焦点回到窗口说明选择器关了；files 为空就是取消。
      // 延迟一点再回探，避开 "focus 早于 change" 的时序竞争。
      setTimeout(() => {
        if (input.files === null || input.files.length === 0) finish([]);
      }, CANCEL_PROBE_DELAY_MS);
    }

    input.addEventListener('change', onChange);
    input.addEventListener('cancel', onCancel);
    if (typeof window !== 'undefined') window.addEventListener('focus', onWindowFocus);

    if (typeof document !== 'undefined' && document.body) document.body.appendChild(input);
    input.click();
  });
}

/** 把 `limit` 归一化成"最多几张"。非正数 / 非法值 = 不限。 */
function normalizeLimit(limit: number | undefined): number {
  return typeof limit === 'number' && Number.isFinite(limit) && limit > 0
    ? Math.floor(limit)
    : Number.POSITIVE_INFINITY;
}

/**
 * 从系统相册选照片（§7.1.3）。
 *
 * @param options `limit` 最多几张、`maxEdge` 缩略图长边上限
 * @param deps 依赖注入（测试用）
 * @returns 内存里的照片数组；用户取消时 `[]`。单张读取失败只跳过那一张。
 */
export async function pickFromSystem(
  options: PickOptions = {},
  deps: PickSystemDeps = {},
): Promise<SystemPhoto[]> {
  if (typeof document === 'undefined') return [];
  const input = deps.createInput?.() ?? createHiddenFileInput();
  const waitForSelection = deps.waitForSelection ?? openPicker;

  let files: File[] = [];
  try {
    files = await waitForSelection(input);
  } catch {
    return [];
  }
  if (!Array.isArray(files) || files.length === 0) return [];

  const limit = normalizeLimit(options.limit);
  const picked = Number.isFinite(limit) ? files.slice(0, limit) : files;
  const photos: SystemPhoto[] = [];
  for (const file of picked) {
    try {
      photos.push(await readPhotoFile(file, deps));
    } catch {
      // 单张失败不影响其余照片。
    }
  }
  return photos;
}

/**
 * 构造 `PhotoSource`（系统相册）。
 *
 * `available()` 恒为 `true`：`<input type="file">` 在任何浏览器里都有。
 * 唯一例外是没有 DOM 的环境（单测 / SSR），那时候 `pick()` 直接返回 `[]`。
 */
export function createSystemSource(
  options: PickOptions = {},
  deps: PickSystemDeps = {},
): PhotoSource {
  return {
    id: 'system',
    label: '从系统相册选',
    available: (): boolean => typeof document !== 'undefined',
    pick: () => pickFromSystem(options, deps),
  };
}
