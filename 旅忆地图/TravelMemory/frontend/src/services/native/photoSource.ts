/** 相册来源：Web 真实现 + Native 桩。
 *
 * ## Web 实现能做什么、不能做什么
 *
 * 能：弹系统文件选择器、读像素尺寸、把任意一张图缩成 JPEG ArrayBuffer。
 * 不能：**拿不到 EXIF**。`<input type="file">` 交给前端的 `File` 只有原始字节，
 * 要 EXIF 得自己解析二进制。这条链路 W6-1 已经在后端（pillow / exifread）做好了，
 * 这里刻意**不重复实现**——同一份照片相机端解析一次、后端再解析一次，迟早出现
 * "相册里显示 19:30、地图上是 20:00" 的错位。`takenAt` 留空，等后端回填。
 *
 * ## Native 为什么现在是桩
 *
 * 原生相册要 PhotoKit / MediaStore，必须经过 Capacitor 桥接并在真机上验证 popup。
 * 夜班没人按"允许访问相册"按钮，硬写实现等于提交一段从未运行过的代码。
 * 现在先让它在被调用时明确报错，避免"静默返回空数组"这种最难查的失败形态。
 */

import type { PickPhotosOptions, PhotoAsset, PhotoSource } from '../../types/native';

/** 缩略图默认长边。256 是列表页最常见的一档，再大就浪费 IndexedDB 空间了。 */
const DEFAULT_MAX_EDGE = 256;

/** JPEG 导出质量。低于 0.8 人脸/天空会出现明显色带。 */
const JPEG_QUALITY = 0.82;

function requireDom(action: string): void {
  if (typeof document === 'undefined') {
    throw new Error(`${action} 只能在浏览器环境调用（当前没有 document）`);
  }
}

/** 把位图按长边缩放并编码成 JPEG。
 *
 * 优先走 OffscreenCanvas：它在 worker 里也能跑，不占主线程。但 Safari 要到 16.4
 * 才有 `convertToBlob`，所以必须保留 `<canvas>` → `toBlob` 的降级分支，不能写成
 * "要么全用要么不用"。
 */
async function encodeThumb(bitmap: ImageBitmap, maxEdge: number): Promise<ArrayBuffer> {
  const edge = Number.isFinite(maxEdge) && maxEdge > 0 ? maxEdge : DEFAULT_MAX_EDGE;
  const scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));

  try {
    if (typeof OffscreenCanvas === 'function') {
      const canvas = new OffscreenCanvas(width, height);
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('无法获取 OffscreenCanvas 的 2d 上下文');
      ctx.drawImage(bitmap, 0, 0, width, height);
      const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: JPEG_QUALITY });
      return await blob.arrayBuffer();
    }
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('无法获取 canvas 的 2d 上下文');
    ctx.drawImage(bitmap, 0, 0, width, height);
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (result) => (result ? resolve(result) : reject(new Error('缩略图 JPEG 编码失败'))),
        'image/jpeg',
        JPEG_QUALITY,
      );
    });
    return await blob.arrayBuffer();
  } finally {
    // ImageBitmap 持有 GPU/显存侧的像素副本，不 close 会在批量选图时累积到 OOM
    bitmap.close();
  }
}

/** Web 相册来源。
 *
 * `File` 对象只在 JS 堆里有引用，不能跨页面刷新存活，也不能放进 IndexedDB 之外的地方
 * 长期持有。所以内部用一张 Map 兜住 `assetId -> File` 的映射，让 `readThumb` 之后还能
 * 找到原始像素。**这个实现是有状态的**：不同 `pickPhotos` 的批次会累积在同一个实例里，
 * 页面级请确保通过 `resolveNativeBridge()` 拿同一个单例。
 */
export function createWebPhotoSource(): PhotoSource {
  const assets = new Map<string, File>();
  // 自增序号保证同一实例内 id 唯一：同一个文件连选两次，名字与 lastModified 都一样
  let seq = 0;

  function makeId(file: File): string {
    seq += 1;
    return `web:${seq}:${file.name}:${file.lastModified}`;
  }

  function remember(file: File): { asset: PhotoAsset; file: File } | null {
    if (file.type && !file.type.startsWith('image/')) return null;
    const id = makeId(file);
    assets.set(id, file);
    const asset: PhotoAsset = {
      id,
      width: 0,
      height: 0,
      // takenAt 留空是有意的，理由见文件头「Web 实现能做什么」。
      locationSource: 'none',
    };
    return { asset, file };
  }

  function fillSize(asset: PhotoAsset, file: File): Promise<void> {
    return createImageBitmap(file).then(
      (bitmap) => {
        asset.width = bitmap.width;
        asset.height = bitmap.height;
        bitmap.close();
      },
      () => {
        // 拿不到尺寸不该让整次选图失败：列表排版会兜，但 UA 若 decode 不了就没救了
        asset.width = 0;
        asset.height = 0;
      },
    );
  }

  return {
    platform: 'web',

    /** 把页面已有的 `File[]` 并入当前批次。
     *
     * 与 `pickPhotos` 的唯一差别是"文件从哪来"，入池逻辑完全复用 `remember`，
     * 所以 `readThumb` 之后照样能按 assetId 找回原始像素。
     *
     * 刻意**不在这里解尺寸**（`fillSize`）：`adoptFiles` 是同步的，而
     * `createImageBitmap` 必须 await。导入链路只需要 `readThumb`（它自己会解码），
     * 为了一个没人读的 width/height 去把整批图解码一遍是纯浪费。
     */
    adoptFiles(files: readonly File[]): PhotoAsset[] {
      return files
        .map(remember)
        .filter((entry): entry is { asset: PhotoAsset; file: File } => entry !== null)
        .map((entry) => entry.asset);
    },

    async pickPhotos(options?: PickPhotosOptions): Promise<PhotoAsset[]> {
      requireDom('pickPhotos');

      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.multiple = true;
      // 藏起来但仍要挂载：部分浏览器不处理未插入文档的 input.click()
      input.style.display = 'none';
      document.body.appendChild(input);

      const cleanup = (): void => {
        input.remove();
      };

      try {
        const fileList = await new Promise<File[] | null>((resolve) => {
          // 'cancel' 事件（Chrome 113+/Safari 16.4+）是唯一可靠的"用户按了取消"信号。
          // 老浏览器只会静默关闭选择器，此时靠 focus 回来作为兜底。
          let settled = false;
          const settle = (files: File[] | null): void => {
            if (settled) return;
            settled = true;
            window.removeEventListener('focus', onFocus);
            resolve(files);
          };
          const onFocus = (): void => settle(null);
          input.addEventListener('change', () => {
            settle(Array.from(input.files ?? []));
          });
          input.addEventListener('cancel', () => settle(null));
          window.addEventListener('focus', onFocus);
          input.click();
        });

        // 用户取消是正常流程，必须 resolve 空数组而不是抛错，
        // 否则调用方每次都要 try/catch 一个"什么都不做"的分支。
        if (!fileList || fileList.length === 0) return [];

        const picked = fileList
          .map(remember)
          .filter((entry): entry is { asset: PhotoAsset; file: File } => entry !== null);

        const limit = options?.limit ?? 0;
        const limited = limit > 0 ? picked.slice(0, limit) : picked;
        // 只给真正要返回的那些解尺寸：createImageBitmap 要解码原始图，
        // 用户选了 200 张但只允许用 9 张时，多解码 191 次是纯浪费
        await Promise.all(limited.map((entry) => fillSize(entry.asset, entry.file)));
        return limited.map((entry) => entry.asset);
      } finally {
        cleanup();
      }
    },

    async readThumb(assetId: string, maxEdge: number = DEFAULT_MAX_EDGE): Promise<ArrayBuffer> {
      requireDom('readThumb');
      const file = assets.get(assetId);
      if (!file) {
        throw new Error(
          `读不到缩略图：assetId ${assetId} 不在当前相册批次里（页面刷新后会失效）`,
        );
      }
      const bitmap = await createImageBitmap(file);
      return encodeThumb(bitmap, maxEdge);
    },
  };
}

/** Native 相册来源（未接入的占位实现）。
 *
 * 明早接真机时要替换的内容：
 *   1. `npm i @capacitor/core @capacitor/camera`（若要多选照片，用 `@capacitor-community/photoviewer`
 *      或自建 PhotoKit / MediaStore 插件，`Camera` 插件一次只能拿一张）
 *   2. iOS：在 `Info.plist` 补 `NSPhotoLibraryUsageDescription`，否则调用会直接被系统 kill
 *   3. Android 13+：用 Photo Picker 可以**不申请** READ_MEDIA_IMAGES；
 *      若要自己扫 MediaStore 才需要权限 + `<uses-permission>` 声明
 *   4. `assetId` 必须是平台的稳定标识（iOS 用 PHAsset.localIdentifier，Android 用 MediaStore._ID），
 *      不要用 URI------URI 在系统清理相册缓存后会变
 *   5. EXIF 在原生侧是拿得到的（PhotoKit/ExifInterface），此时应把 `takenAt` / `lat` / `lng`
 *      真的填上，`coordSystem` 按 EXIF 原始值填 `'WGS84'`
 */
export function createNativePhotoSource(): PhotoSource {
  const NOT_READY = '原生相册未接入：请先集成 Capacitor 并实现该桥接';
  return {
    platform: 'ios',
    async pickPhotos(_options?: PickPhotosOptions): Promise<PhotoAsset[]> {
      throw new Error(NOT_READY);
    },
    async readThumb(_assetId: string, _maxEdge?: number): Promise<ArrayBuffer> {
      throw new Error(NOT_READY);
    },
    /** 原生端没有 `File`：真机上应由桥接层把平台 asset 标识符直接交给业务，
     *  走不到这个"先有 File 再换 asset"的反向路径。抛错而不是静默返回空数组，
     *  跟本桩其余方法的取向一致。 */
    adoptFiles(_files: readonly File[]): PhotoAsset[] {
      throw new Error(NOT_READY);
    },
  };
}
