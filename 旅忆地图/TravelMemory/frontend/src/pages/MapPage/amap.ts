/** 高德 JS API 的加载器 —— 主应用这一侧的「策略层」。
 *
 * ## 为什么这里变得这么薄
 *
 * 加载机制（去重、超时、script 复用、安全密钥、Node 守卫）已经抽到
 * `@travelmemory/amap-loader`，展示站共用同一份。这里只留主应用专属的三样东西：
 * 自己需要的命名空间形状、分享圈样式、以及「加载失败要抛给 UI」的契约。
 *
 * 合并前两份加载器已经漂移出真 bug：这里把安全密钥写成 `securityCode`，
 * 而高德官方字段是 `securityJsCode`（JS API 2.0「安全密钥使用」）——
 * 也就是说**主应用配了 `VITE_AMAP_SECURITY_CODE` 也不会生效**。共享层用的是正确字段。
 *
 * 别被 grep 误导：源码里仍有若干 `securityCode` 字样，那是 React 组件 prop /
 * 函数参数的名字（如 `MapCanvas` 的 `securityCode?: string`），不是写进
 * `_AMapSecurityConfig` 的键。只有后者会被高德读取，真机验过是 `["securityJsCode"]`。
 *
 * ## 为什么不用 index.html 里写死 <script>
 *
 * 1. **key 没配时不能白屏**：写死脚本会让缺 key 的部署永远转圈，加载器能给出明确错误，
 *    UI 才有机会显示"未配置高德 Key"的占位。
 * 2. **测试环境不能炸**：本模块在 Node（vitest）里也会被 import，
 *    所以所有 `window` 访问都必须惰性 + 守卫（规范 §2.3 分层边界）。
 * 3. **重复加载要去重**：React StrictMode 会双调用 effect，
 *    两个并发 `loadAmap()` 必须共享同一个 Promise。
 *
 * ## key 从哪来
 *
 * `VITE_AMAP_JS_KEY`（见 `frontend/.env.example`）。
 * **这是前端 key，会被打进产物** —— 高德 JS key 设计上就在浏览器里公开使用，
 * 靠 referer 白名单限制；但任何人仍能从产物里读到它，别把它当机密。
 */

import {
  isAmapReady as isLoaderReady,
  loadAmapScript,
  resetAmapLoaderForTest as resetLoaderForTest,
} from '@travelmemory/amap-loader';

/** `window.AMap` 的最小形态。不引官方 d.ts（FullType 会拖来一堆 never 校验），
 *  只声明用到的部分。展示站那份形状不同（要 LngLat），各自声明互不干扰。 */
export interface AmapNamespace {
  Map: new (container: HTMLElement, options?: Record<string, unknown>) => AmapMap;
  Marker: new (options: Record<string, unknown>) => AmapOverlay;
  Circle: new (options: Record<string, unknown>) => AmapOverlay;
}
export interface AmapMap {
  setCenter(center: [number, number]): void;
  setZoom(zoom: number): void;
  getZoom(): number;
  /** 同时设缩放与中心。**一步做完**而不是先 setZoom 再 setCenter：
   *  高德内部会为两次调用各排一次重绘，动画中间态会被用户看到"先缩后跳"。 */
  setZoomAndCenter(zoom: number, center: [number, number]): void;
  setFitView(overlays: AmapOverlay[], immediately?: boolean, avoid?: [number, number, number, number]): void;
  add(overlay: AmapOverlay | AmapOverlay[]): void;
  remove(overlay: AmapOverlay | AmapOverlay[]): void;
  destroy(): void;
  on(event: string, handler: (event: unknown) => void): void;
  off(event: string, handler: (event: unknown) => void): void;
}

export interface AmapOverlay {
  setMap(map: AmapMap | null): void;
}

/** 半透明圆圈的填充色（W6-4）：与设计文档 §6.2 的"分享圈"图层一致。 */
export const SHARE_CIRCLE_STYLE = {
  fillColor: '#2f6f5e',
  fillOpacity: 0.18,
  strokeColor: '#2f6f5e',
  strokeWeight: 1,
  strokeOpacity: 0.6,
} as const;

declare global {
  interface Window {
    AMap?: AmapNamespace;
  }
}

/** 主应用给地图 15 秒（比展示站宽裕），失败一律抛给 UI。 */
const LOAD_TIMEOUT_MS = 15000;

/** 当前是否已加载完成（供 UI 同步判断，避免闪烁）。 */
export function isAmapReady(): boolean {
  return isLoaderReady();
}

/** 加载高德 JS API 并返回 `window.AMap`。
 *
 * @param jsKey   高德 JS API key（浏览器端公开，靠 referer 白名单保护）
 * @param securityCode  安全密钥（高德控制台的 securityJsCode）
 * @throws key 为空、脚本加载超时（15s）、`window.AMap` 未定义、脚本 onerror
 */
export function loadAmap(jsKey: string, securityCode = ''): Promise<AmapNamespace> {
  return loadAmapScript<AmapNamespace>({
    key: jsKey,
    securityJsCode: securityCode,
    timeoutMs: LOAD_TIMEOUT_MS,
  });
}

/** 测试专用：清掉共享层的去重缓存。 */
export function resetAmapLoaderForTest(): void {
  resetLoaderForTest();
}
