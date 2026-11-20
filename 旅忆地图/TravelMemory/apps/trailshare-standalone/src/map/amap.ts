/** 高德 JS API 的加载适配层（**只给 embed 入口用**）。
 *
 * ## 为什么只有这么薄一层
 *
 * 脚本加载的全部机制（并发去重、超时、script 复用、Node 守卫、**安全密钥在插脚本
 * 之前写进 `window._AMapSecurityConfig`**）都在 `@travelmemory/amap-loader` 里，
 * 主应用与 Track Studio 共用同一份。这里只补两样东西：
 *  1. 本应用自己需要的最小类型（`Map` / `LngLat` / `Marker` / `Circle`）；
 *  2. 超时值（iframe 内嵌场景给 15s，与主应用一致；展示站那边是 8s）。
 *
 * ## 为什么这里必须能被动态 import
 *
 * `index.html`（standalone）的 CSP **不含任何地图域名**。只要这份模块被静态打进
 * 共享 chunk，standalone 的产物里就会多出一串 `webapi.amap.com` 字符串 ——
 * 虽然不会真的发请求，但"零网络"的证据链会变模糊。所以 `ui/PlaceMap.tsx` 用
 * `await import('../map/amap')` 动态加载：有 Key 且真的要用地图时才拉这个 chunk。
 *
 * ## 安全密钥字段名
 *
 * 官方字段是 `securityJsCode`（JS API 2.0 文档），不是 `securityCode`。
 * 写错等于没配，且**不会报错** —— 见 loader 的文件头注释。
 */

import { loadAmapScript } from '@travelmemory/amap-loader';

/** 经纬度（高德口径：GCJ-02）。字段顺序刻意为 `(lng, lat)`，与高德 API 一致。 */
export interface AmapLngLat {
  lng: number;
  lat: number;
}

/** 最小化的覆盖物形状：只声明我们真的调用的方法。 */
export interface AmapOverlayInstance {
  setMap(map: AmapMapInstance | null): void;
  on(event: 'click', handler: () => void): void;
}

/** 最小化的地图实例形状。 */
export interface AmapMapInstance {
  add(overlay: AmapOverlayInstance | AmapOverlayInstance[]): void;
  remove(overlay: AmapOverlayInstance | AmapOverlayInstance[]): void;
  setCenter(center: [number, number]): void;
  setZoom(zoom: number): void;
  getZoom(): number;
  lngLatToContainer?(position: [number, number] | AmapLngLat): { x: number; y: number };
  on(event: 'click', handler: (event: { lnglat?: AmapLngLat }) => void): void;
  on(event: 'complete', handler: () => void): void;
  destroy(): void;
}

/** 高德命名空间（只声明用到的部分，不引官方 d.ts——它会拖来一堆 never 校验）。 */
export interface AmapNamespace {
  Map: new (container: HTMLElement, options?: Record<string, unknown>) => AmapMapInstance;
  LngLat: new (lng: number, lat: number) => AmapLngLat;
  Marker: new (options: Record<string, unknown>) => AmapOverlayInstance;
  Circle: new (options: Record<string, unknown>) => AmapOverlayInstance;
}

declare global {
  interface Window {
    AMap?: AmapNamespace;
  }
}

/** iframe 内嵌场景给 15 秒（与主应用一致）；超时不算致命，调用方降级成示意格网。 */
export const AMAP_LOAD_TIMEOUT_MS = 15000;

/**
 * 加载高德 JS API。
 *
 * 失败一律 reject（共享层的统一契约），由 `PlaceMap` 兜成降级示意 ——
 * UI 的其余部分（地点列表、验签报告）永远不受地图影响。
 *
 * @param key          高德 Web 端 JS Key（来自 `src/env.ts`）
 * @param securityCode 安全密钥（可选；loader 会在插 script **之前**注入）
 */
export function loadAmap(key: string, securityCode = ''): Promise<AmapNamespace> {
  return loadAmapScript<AmapNamespace>({
    key,
    securityJsCode: securityCode,
    timeoutMs: AMAP_LOAD_TIMEOUT_MS,
  });
}
