/** 回放层的缩略图批量加载。
 *
 * 主应用的照片缩略图**不在后端**：后端只存元数据，原图留在设备上，256px 缩略图
 * 是导入时前端生成、按后端 photo id 存进 IndexedDB 的（见 `PhotoThumb.tsx` 与
 * `photoImport.ts` 的 `assignThumbs`）。所以这里复用 `getOrCreateThumbUrl`，
 * 与相册列表共享同一份 objectURL 缓存，不新建第二条数据通道。
 *
 * 为什么返回 `photoId → url` 的映射而不是改 `Journey`：
 * `buildReplayJourney` 是同步纯函数（12 条测试覆盖），把它改成 async 会波及调用链；
 * 而且 `journey` 是主循环 `useEffect` 的依赖，重建它会把播放进度重置回 0。
 * 映射交给 `<PhotoCards thumbs={...}>` 后，缩略图到达只触发一次 React 重渲染，
 * 主循环原封不动。
 */

import { getOrCreateThumbUrl } from '../PhotoThumb';

/** 批量取缩略图。取不到的照片不会出现在 Map 里，卡片自动走 gradient 兜底。 */
export async function loadReplayThumbs(
  photoIds: readonly string[],
): Promise<ReadonlyMap<string, string>> {
  const out = new Map<string, string>();
  await Promise.all(
    photoIds.map(async (id) => {
      try {
        const url = await getOrCreateThumbUrl(id);
        if (url) out.set(id, url);
      } catch {
        // 单张读失败不该让整批缩略图一起消失。
        // getOrCreateThumbUrl 内部已经兜了一层，这里是批量场景的第二道保险。
      }
    }),
  );
  return out;
}
