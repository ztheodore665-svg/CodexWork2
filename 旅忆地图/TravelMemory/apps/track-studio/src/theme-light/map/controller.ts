/** 底图控制器的入口。
 *
 * ## 为什么不直接 `import { createAmapController } from '@travelmemory/amap-loader'`
 *
 * 任务书 §7.1.2 写的那个 import 在仓库里**不存在**：`@travelmemory/amap-loader` 只导出
 * `loadAmapScript / isAmapReady / resetAmapLoaderForTest`（它是脚本加载原语）。
 * 真正的"控制器"适配层（相机标定、25Hz 中心下发、8s 超时降级、whenReady 4s 上限）
 * 在 showcase 的 `src/map/baseMap.ts` 里，且它依赖 `@travelmemory/geo` 的墨卡托与
 * `@travelmemory/trail` 的解析投影。
 *
 * 按 §7.1.2 的"缺什么就在 app 里补适配层，不要改 packages / showcase"，
 * 这里采用：**整份复制 baseMap.ts**（唯一允许的"复制既有代码"），再包一层
 * `initMap`。复制后的逻辑一行未改 —— 那 8s / 4s / 25Hz 都是阶段 9 的实测结论。
 */

import { createBaseMap, type MapController } from './baseMap';
import { readAmapKey } from '../../env';

export type { MapController };
export type { MapKind, CoordinateSystem, MapView } from './baseMap';

/** 高德 JS API Key（来自 `../env`，那里是唯一读 env 的地方）。 */
export { readAmapKey };

export interface InitMapOptions {
  center: { lng: number; lat: number };
  zoom: number;
  /** 播放态一律 false：相机由我们的主循环驱动，开交互会和阻尼抢控制权。 */
  interactive?: boolean;
  /**
   * 降级时是否由底图层自己画示意格网。默认 `false`（见下方 `initMap` 的说明）。
   *
   * 阶段 14 / W14-2：浅色播放页不再调 `packages/trail` 的 `Renderer`（那份是暗场
   * 发光配色，且本轮禁止改内核），轨迹改由 `ui/PlayStage.tsx` 自绘，于是
   * "格网由渲染器统一画"这个前提不成立 —— 播放页必须传 `true`，
   * 否则没配 Key 的用户看到的是一片纯色背景加一条轨迹，没有任何方位参照。
   */
  paintFallback?: boolean;
}

/**
 * 初始化底图。**永远不 reject**：没有 Key / 加载失败 / 8s 超时都会返回降级格网控制器，
 * 轨迹与播放不受影响（§7.1.5 / D6）。
 *
 * `paintFallback` 默认 `false` 是刻意的：深色版 / 旧的播放页把降级格网交给
 * `packages/trail` 的渲染器统一画，底图这一层再画一遍会出现双层网格摩尔纹。
 * 浅色播放页例外，见 `InitMapOptions.paintFallback`。
 *
 * @param container 地图容器（调用方负责尺寸）
 * @param options 初始中心与缩放
 */
export async function initMap(container: HTMLElement, options: InitMapOptions): Promise<MapController> {
  return createBaseMap(container, {
    center: options.center,
    zoom: options.zoom,
    interactive: options.interactive ?? false,
    paintFallback: options.paintFallback ?? false,
  });
}
