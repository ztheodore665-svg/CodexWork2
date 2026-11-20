/** 回放专用的高德底图控制。
 *
 * 与地图页的 `amap.ts` 分工：那边负责**加载 SDK**（含 key 校验、去重、超时），
 * 这边只负责**按回放的用法建一个"只读"地图实例**，不重复实现加载逻辑。
 *
 * ## 为什么相机是"只读"的
 *
 * 回放时相机由内核的阻尼跟随驱动，每 40ms 下发一次中心。用户拖动会与它抢控制权，
 * 所以这里把 drag/zoom/scrollWheel 全关掉——地图只当底图，交互留给播放器。
 *
 * ## `immediately = true` 不是可选优化
 *
 * 高德 `setCenter(center, immediately)` 的 `immediately` 默认 **false**，即内部起一段
 * 过渡动画。逐帧/高频调用时每帧都会新起一段 tween、彼此打断，表现就是底图抽帧、
 * 中心跳变。必须显式传 true。
 */

import { loadAmap } from '../amap';

/** 只声明回放用到的两个方法，避免把 `amap.ts` 的 `AmapMap` 扩成 `any` 形状。 */
interface ReplayMapInstance {
  setCenter(center: [number, number], immediately?: boolean): void;
  destroy(): void;
}

interface ReplayAmapNamespace {
  Map: new (container: HTMLElement, options?: Record<string, unknown>) => ReplayMapInstance;
}

export interface ReplayMapController {
  setCenter(lng: number, lat: number, immediately: boolean): void;
  destroy(): void;
}

/**
 * 建一个回放用底图。
 *
 * 失败一律 reject —— 由调用方降级成内核自绘的经纬格网（PROJECT.md 第 8 条：
 * 依赖失败必须降级而不是抛错给用户）。
 */
export async function createReplayMap(
  host: HTMLElement,
  center: { lng: number; lat: number },
  zoom: number,
  jsKey: string,
  securityCode: string,
): Promise<ReplayMapController> {
  const namespace = (await loadAmap(jsKey, securityCode)) as unknown as ReplayAmapNamespace;

  const map = new namespace.Map(host, {
    zoom,
    center: [center.lng, center.lat],
    zooms: [3, 18],
    viewMode: '2D',
    mapStyle: 'amap://styles/dark',
    // 关掉内部缓动：相机由我们每 40ms 推一次，高德自己再补动画只会打架
    animateEnable: false,
    jogEnable: false,
    // 回放期不允许用户改视野（与阻尼相机会抢控制权）
    dragEnable: false,
    zoomEnable: false,
    doubleClickZoom: false,
    scrollWheel: false,
    rotateEnable: false,
    pitchEnable: false,
    keyboardEnable: false,
  });

  return {
    setCenter(lng: number, lat: number, immediately: boolean): void {
      map.setCenter([lng, lat], immediately);
    },
    destroy(): void {
      try {
        map.destroy();
      } catch {
        /* 某些版本重复 destroy 会抛错，忽略即可 */
      }
    },
  };
}
