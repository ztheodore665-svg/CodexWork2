/** 照片缩略图：从本机缩略图缓存里取，取不到就降级成占位。
 *
 * ## 为什么不能直接用 `<img src={photo.url}>`
 *
 * 后端**只存元数据**，原图始终留在用户设备上（设计文档 §4：原图永不上云）。
 * 缩略图是导入时前端自己生成、按后端 photo id 存进 IndexedDB 的那一份
 * （见 `TripDrawer.runImport` 里的 `thumbs.put(photoId, 256)`）。
 * 所以这里只能走 `ThumbnailStore.get`，没有第二条路。
 *
 * ## 为什么取不到必须优雅降级
 *
 * 三种情况会取不到，而且都是**正常**的：
 *   1. 换浏览器 / 清了 IndexedDB —— 行程还在，本机的图没了；
 *   2. 演示数据（`seed_photos` 造的 129 张）从来没经过前端导入，没有缩略图；
 *   3. 隐私模式下 IndexedDB 写入被拒。
 * 三种都该显示一个占位图标，而不是一个裂掉的图或者一片空白 ——
 * 后者会被当成"页面坏了"。
 */

import { useEffect, useState } from 'react';
import { ImageOff } from 'lucide-react';

import { resolveNativeBridge } from '../../services/native';
import { THUMB_MAX_EDGE } from './photoImport';

export interface PhotoThumbProps {
  /** 后端 photo id（缩略图缓存用的就是这个键）。 */
  photoId: string;
  /** 无障碍描述；缺省时用「照片缩略图」。 */
  alt?: string;
  className?: string;
}

/** 缩略图 objectURL 的缓存。同一个 photoId 只建一次 URL，
 *  否则列表每重渲染一次就 `createObjectURL` 一遍，内存泄漏得无声无息。 */
const urlCache = new Map<string, string>();

/**
 * 取（或建）一张缩略图的 objectURL。
 *
 * 抽成公开函数是为了让**回放层复用同一份缓存**：回放一次要取几十张，
 * 若各自 createObjectURL，同一个 photoId 会建出一串 URL，内存无声泄漏。
 * URL 的生命周期交给 `clearThumbUrlCache()`（导入新批次时统一 revoke），
 * 调用方不要自己 revoke —— 别的组件可能还在用同一个 URL。
 *
 * 取不到一律返回 `null`，不抛异常：换过浏览器 / 清过 IndexedDB /
 * 演示数据从没导入过照片，都是正常状态，调用方退回渐变占位即可。
 */
export async function getOrCreateThumbUrl(photoId: string): Promise<string | null> {
  const cached = urlCache.get(photoId);
  if (cached) return cached;
  try {
    const bytes = await resolveNativeBridge().thumbs.get(photoId, THUMB_MAX_EDGE);
    if (!bytes || bytes.byteLength === 0) return null;
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
    urlCache.set(photoId, objectUrl);
    return objectUrl;
  } catch {
    return null;
  }
}

export function PhotoThumb({ photoId, alt = '照片缩略图', className }: PhotoThumbProps) {
  const [url, setUrl] = useState<string | null>(() => urlCache.get(photoId) ?? null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getOrCreateThumbUrl(photoId).then((objectUrl) => {
      if (cancelled) return;
      if (objectUrl) setUrl(objectUrl);
      else setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [photoId]);

  if (url) {
    return <img src={url} alt={alt} className={className} loading="lazy" />;
  }

  return (
    <span
      className={`flex items-center justify-center bg-muted text-muted-foreground ${className ?? ''}`}
      role="img"
      aria-label={failed ? '无缩略图' : '缩略图加载中'}
      title={failed ? '本机没有这张照片的缩略图（可能换过浏览器或清过缓存）' : undefined}
    >
      <ImageOff className="h-4 w-4" />
    </span>
  );
}

/** 清掉 objectURL 缓存。导入新批次后调用，避免缓存无限增长。
 *  **只在用户主动操作时调**（比如切到照片库 Tab），不要在 render 里调。 */
export function clearThumbUrlCache(): void {
  for (const url of urlCache.values()) URL.revokeObjectURL(url);
  urlCache.clear();
}
