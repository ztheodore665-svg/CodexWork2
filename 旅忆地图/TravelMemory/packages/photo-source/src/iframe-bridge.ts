/** 主应用相册来源（iframe 嵌入模式的桥）。
 *
 * ## 这个文件刻意不是"完整的 postMessage 桥"
 *
 * §9.1 里的 `initBridge` / `sendToParent` 属于**应用层**（要碰身份、要渲染提示），
 * 放在 apps 里更合适。这里只做一件小而稳定的事：**把"主应用注入进来的照片"
 * 包装成一个 `PhotoSource`**，让两个小应用的 UI 能像对待系统相册一样对待它。
 *
 * 于是 UI 里永远是同一个循环：
 *
 * ```ts
 * for (const source of [createSystemSource(), createLibrarySource()]) {
 *   if (source.available()) renderButton(source.label, () => source.pick());
 * }
 * ```
 *
 * 不需要在每个按钮上写 `if (isEmbedded)`。
 *
 * ## 为什么 `available()` 要同时判两个条件（§12）
 *
 * - `isEmbedded()`：没被嵌入时，父窗口根本不存在，按了也没反应；
 * - `已注入非空`：被嵌入了但主应用还没发 `init`（或用户在主应用里没有照片），
 *   按钮可点但点了空 —— 那和坏了没区别。
 *
 * 缺一不可。
 */

import type { PhotoSource, SystemPhoto } from './types';

/** 主应用注入进来的照片。模块级单例：一个页面只有一个主应用。 */
let injectedPhotos: SystemPhoto[] = [];

/** 判断当前是否被主应用 iframe 嵌入（`?embedded=1`，§6.2）。
 *
 * 用查询串而不是 `window.self !== window.top`：后者在
 * "被任意网站 iframe" 时也为真，会把"从主应用相册选"错开出来。
 */
export function isEmbedded(): boolean {
  if (typeof location === 'undefined') return false;
  try {
    return new URLSearchParams(location.search).get('embedded') === '1';
  } catch {
    return false;
  }
}

/** 粗校验：只认结构上像 `SystemPhoto` 的元素，脏数据静默丢弃。 */
function isSystemPhotoLike(value: unknown): value is SystemPhoto {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === 'string' &&
    typeof record.name === 'string' &&
    typeof record.thumbnailDataUrl === 'string' &&
    (record.bytes instanceof ArrayBuffer || record.bytes === undefined)
  );
}

/**
 * 主应用 `init` / `photos-update` 收到照片后调用。
 *
 * @param photos 照片数组；`null` / `undefined` / 非数组 = 清空。
 */
export function setInjectedPhotos(photos: readonly SystemPhoto[] | null | undefined): void {
  if (!Array.isArray(photos)) {
    injectedPhotos = [];
    return;
  }
  injectedPhotos = photos.filter(isSystemPhotoLike);
}

/** 清空注入的照片（退出嵌入、切回首屏时调用）。 */
export function resetInjectedPhotos(): void {
  injectedPhotos = [];
}

/** 取当前注入的照片副本（UI 显示已选列表用）。 */
export function getInjectedPhotos(): SystemPhoto[] {
  return injectedPhotos.slice();
}

/**
 * 造一个 `library` 来源。
 *
 * `pick()` 在不可用/未注入时抛中文错误而不是返回 `[]`：
 * 这个来源只在 `available()` 为真时才会出现在 UI 上，走到 `pick()` 说明调用方
 * 自己漏判了 `available()` —— 静默返回空数组会让这种 bug 藏得更深。
 */
export function createLibrarySource(): PhotoSource {
  return {
    id: 'library',
    label: '从主应用相册选',
    available: (): boolean => isEmbedded() && injectedPhotos.length > 0,
    pick: async (): Promise<SystemPhoto[]> => {
      if (!isEmbedded()) {
        throw new Error('当前不在主应用嵌入模式，无法从主应用相册选照片。');
      }
      if (injectedPhotos.length === 0) {
        throw new Error('主应用还没有注入相册照片，请稍后再试。');
      }
      return injectedPhotos.slice();
    },
  };
}
