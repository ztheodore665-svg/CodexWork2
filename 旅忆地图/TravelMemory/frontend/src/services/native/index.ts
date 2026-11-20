/** 端能力总入口：按运行环境挑选实现。
 *
 * ## 为什么要包一层 resolve
 *
 * 业务代码如果直接 `createWebPhotoSource()`，接真机那天就得全局替换。
 * 这里统一出口，上层只写 `resolveNativeBridge().photos.pickPhotos(...)`，
 * 换端只动 `services/native/` 一个目录。
 *
 * ## 为什么 Node（测试）环境不抛错
 *
 * 一个直觉的实现是"没有 window 就 throw"。但这样 vitest 连 import 都做不到------
 * 模块顶层直接炸。所以 Node 环境照样返回一个 `platform: 'web'` 的 bridge，
 * 真正的报错推迟到**调用 DOM 能力的那一刻**（见 photoSource 里的 requireDom），
 * 错误信息明说"只能在浏览器环境调用"。模块可导入、能力不可用的边界从这里划。
 *
 * ## 为什么结果要缓存
 *
 * `PhotoSource` 是有状态的：`pickPhotos` 把选中的 `File` 存在实例内部 Map 里，
 * 之后 `readThumb` 要靠 assetId 找回原始像素。如果每次 resolve 都 new 一个，
 * 第二次调用就会报"assetId 不在当前批次"。所以两端各缓存一份单例。
 */

import type { NativeBridge } from '../../types/native';
import { createNativePermissions, createWebPermissions } from './permissions';
import { createNativePhotoSource, createWebPhotoSource } from './photoSource';
import {
  createIndexedDbThumbnailStore,
  createMemoryThumbnailStore,
  createNativeThumbnailStore,
} from './thumbnailStore';

/** 测试注入 / 真机热替换用的覆盖实例。优先级最高。 */
let override: NativeBridge | null = null;

let webBridge: NativeBridge | null = null;
let nativeBridge: NativeBridge | null = null;

function hasDom(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function isCapacitorNative(): boolean {
  if (typeof window === 'undefined') return false;
  const capacitor = (window as Window & {
    Capacitor?: { isNativePlatform?: () => boolean };
  }).Capacitor;
  return capacitor?.isNativePlatform?.() === true;
}

function createWebBridge(): NativeBridge {
  const thumbs =
    typeof window !== 'undefined' && typeof window.indexedDB !== 'undefined'
      ? createIndexedDbThumbnailStore()
      : createMemoryThumbnailStore();
  return {
    platform: 'web',
    photos: createWebPhotoSource(),
    permissions: createWebPermissions(),
    thumbs,
  };
}

function createNativeBridge(): NativeBridge {
  return {
    platform: 'ios',
    photos: createNativePhotoSource(),
    permissions: createNativePermissions(),
    thumbs: createNativeThumbnailStore(),
  };
}

/** 取当前环境的端能力桥接。 */
export function resolveNativeBridge(): NativeBridge {
  if (override) return override;
  if (isCapacitorNative()) {
    nativeBridge ??= createNativeBridge();
    return nativeBridge;
  }
  webBridge ??= createWebBridge();
  return webBridge;
}

/** 覆盖默认桥接，供单测注入假实现、或真机接入时塞真实实现。 */
export function setNativeBridge(bridge: NativeBridge | null): void {
  override = bridge;
}

/** 回到按环境解析的默认行为。 */
export function resetNativeBridge(): void {
  override = null;
}

/** 当前是否有 DOM------上层用它决定要不要渲染"选照片"按钮，比 try/catch 优雅。 */
export function canUseDomNative(): boolean {
  return hasDom();
}
