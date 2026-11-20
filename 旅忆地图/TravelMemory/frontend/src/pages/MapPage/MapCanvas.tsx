/** 地图组件：把 `RenderPlan` 画到高德地图上，并对外暴露**命令式句柄**。
 *
 * ## 这个文件刻意"薄"
 *
 * 所有决策（画什么、圈多大、视野怎么框）都在 `layers.ts` 里算完并经单测锁死，
 * 这里只做：加载高德、建地图、按计划加覆盖物、卸载时销毁。
 * 60fps 渲染（聚合、视口裁剪、canvas 图层）留给人类按真机数据调 ——
 * 但"模糊圈不画 Marker"这类**语义**已经在图层管理器里，不会因为渲染优化而丢。
 *
 * ## 为什么需要 `MapHandle`（useImperativeHandle）
 *
 * 产品化之后地图是全屏底层，**上层容器要能指挥地图**：
 *   - 导入照片成功后，视野要飞到刚聚类出的那片区域（任务三）；
 *   - 点 AI 回答里的引用 `[n]`，要飞到对应证据并高亮。
 * 这两件事都发生在别的组件的回调里，靠 props 反推（比如传一个 `focusToken`
 * 让 effect 重跑）能实现，但会把"什么时候该飞"这个决策泄露到父组件，
 * 而且 effect 依赖一多就容易出现"明明该飞却没飞"的竞态。
 * 显式句柄让"飞"成为一个**命令**：谁触发谁负责调用，语义一对一。
 *
 * ## 降级路径（PROJECT.md §二、[8]）
 *
 * key 没配 / 脚本加载失败 → 不白屏，显示占位卡片，页面其余部分照常工作。
 */

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import {
  SHARE_CIRCLE_STYLE,
  isAmapReady,
  loadAmap,
  type AmapMap,
  type AmapNamespace,
  type AmapOverlay,
} from './amap';
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../lib/mapView';
import { planMapRender, type MapPointInput, type RenderPlan } from './layers';

export interface MapCanvasProps {
  points: readonly MapPointInput[];
  /** 高德 JS API key（由页面从环境变量读入后传下，组件本身不读 env，便于测试）。 */
  jsKey: string;
  securityCode?: string;
  /** 要高亮的地图点 id（点 AI 证据时由上层传入）。空串 = 无高亮。 */
  highlightId?: string;
  /** 「把视野收到全部覆盖物」的请求计数器。
   *
   *  每次 +1 触发一次 fitBounds，0 = 从不主动请求。
   *  为什么用计数器而不是让父组件调 `ref.fitAll()`：父组件的
   *  `await 刷新行程()` 返回时 React 还没重渲染，此时调 `fitAll()`
   *  读到的是**上一批**覆盖物 —— 飞向旧位置，而且没有任何报错。
   *  计数器走的是同一次 commit：effect 3（重画覆盖物）先跑，
   *  本 effect 后跑，读到的必然是新覆盖物。 */
  fitSignal?: number;
}

/** 父组件可对地图下的命令。 */
export interface MapHandle {
  /** 把视野收到当前全部覆盖物上。
   *
   *  **没有覆盖物时什么都不做**：`setFitView([])` 在高德上的行为是
   *  fitBounds 到全世界，用户会突然看到自己家变成了地球仪。 */
  fitAll(): void;
  /** 飞到指定中心。 */
  flyTo(lng: number, lat: number, zoom?: number): void;
  /** 当前地图是否可用（key 配了且脚本加载成功）。 */
  isReady(): boolean;
}

/** 高亮样式：在分享圈/默认 Marker 之外单独一档。
 *  高亮的意义是"这一条是你刚点的"，所以要用**醒目**而不是"另一种好看"。 */
const HIGHLIGHT_CIRCLE_STYLE = {
  fillColor: '#d97706',
  fillOpacity: 0.28,
  strokeColor: '#b45309',
  strokeWeight: 3,
  strokeOpacity: 0.95,
} as const;

export const MapCanvas = forwardRef<MapHandle, MapCanvasProps>(function MapCanvas(
  { points, jsKey, securityCode = '', highlightId = '', fitSignal = 0 },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<AmapMap | null>(null);
  const overlaysRef = useRef<AmapOverlay[]>([]);
  const [amap, setAmap] = useState<AmapNamespace | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 渲染计划：纯函数 + useMemo，points 不变就不重算。
  // 注意这里**不带 visibility 参数** —— 图层开关在 MapPage 就已经体现在
  // points 里了（被关掉的点根本不传进来），所以这里算的是"最终要画什么"。
  const plan: RenderPlan = useMemo(() => planMapRender(points), [points]);

  // 1) 加载高德（key 缺失 / 网络失败都走 error 分支，不白屏）
  useEffect(() => {
    let cancelled = false;
    if (isAmapReady()) {
      setAmap(window.AMap as AmapNamespace);
      return;
    }
    loadAmap(jsKey, securityCode).then(
      (ns) => {
        if (!cancelled) setAmap(ns);
      },
      (exc: Error) => {
        if (!cancelled) setError(exc.message);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [jsKey, securityCode]);

  // 2) 建地图（只建一次；销毁时 remove 全部覆盖物再 destroy）
  useEffect(() => {
    if (!amap || !containerRef.current || mapRef.current) return;
    mapRef.current = new amap.Map(containerRef.current, {
      viewMode: '2D',
      zoom: DEFAULT_MAP_ZOOM,
      // 中国地理中心附近：一进页面就能看到中国全貌及周边，
      // 而不是旧默认值那种"先看到大西北荒原"的观感。
      center: [DEFAULT_MAP_CENTER[0], DEFAULT_MAP_CENTER[1]],
    });
    const map = mapRef.current;
    return () => {
      for (const overlay of overlaysRef.current) overlay.setMap(null);
      overlaysRef.current = [];
      map.destroy();
      mapRef.current = null;
    };
  }, [amap]);

  // 3) 计划变化 → 差量更新覆盖物（先全清再重画：照片量级 < 千，重建比 diff 便宜且不会漏）
  useEffect(() => {
    const map = mapRef.current;
    if (!amap || !map) return;
    for (const overlay of overlaysRef.current) overlay.setMap(null);
    overlaysRef.current = [];

    const created: AmapOverlay[] = [];
    for (const item of plan.items) {
      const highlighted = item.id === highlightId;
      if (item.kind === 'circle') {
        // W6-4 红线：radius > 0 → 半透明圆，**不画 Marker**
        created.push(
          new amap.Circle({
            center: [item.center.lng, item.center.lat],
            radius: item.radius,
            ...(highlighted ? HIGHLIGHT_CIRCLE_STYLE : SHARE_CIRCLE_STYLE),
            extData: { id: item.id, name: item.name },
          }),
        );
      } else {
        // 高亮的 Marker 显示名字；朋友的分享**常显**标签 —— 它和本机照片长得一样，
        // 不标出来就会被当成"我去过这里"（W15-5 明确要防的误认）。
        const isFriend = item.source === 'friend';
        const labelContent = highlighted
          ? (item.name ?? '已选中')
          : isFriend
            ? '朋友的分享'
            : '';
        created.push(
          new amap.Marker({
            position: [item.position.lng, item.position.lat],
            extData: { id: item.id, name: item.name },
            label: labelContent ? { content: labelContent, direction: 'top' } : undefined,
          }),
        );
      }
    }
    for (const overlay of created) overlay.setMap(map);
    overlaysRef.current = created;

    // 视野：空计划不动地图（否则会 fitBounds 到全世界）
    if (plan.bounds) {
      map.setFitView(created, false);
    }
  }, [amap, plan, highlightId]);

  // 4) 外部请求 fitBounds（导入成功后由父组件 +1 触发）。
  //    声明顺序保证它在 effect 3 之后跑，读到的 overlay 列表是新的。
  useEffect(() => {
    if (fitSignal === 0) return;
    const map = mapRef.current;
    if (!map || overlaysRef.current.length === 0) return;
    map.setFitView(overlaysRef.current, false);
  }, [fitSignal, plan]);

  // 5) 对外命令
  useImperativeHandle(
    ref,
    (): MapHandle => ({
      fitAll() {
        const map = mapRef.current;
        if (!map || overlaysRef.current.length === 0) return;
        map.setFitView(overlaysRef.current, false);
      },
      flyTo(lng: number, lat: number, zoom?: number) {
        const map = mapRef.current;
        if (!map) return;
        map.setZoomAndCenter(zoom ?? DEFAULT_MAP_ZOOM + 4, [lng, lat]);
      },
      isReady() {
        return mapRef.current !== null;
      },
    }),
    [],
  );

  if (error) {
    return (
      <div className="map-fallback" role="status">
        <strong>地图不可用</strong>
        <p>{error}</p>
        <p className="hint">这不影响信物的加解密与验签 —— 那些都在本地完成。</p>
      </div>
    );
  }

  return (
    <div className="map-canvas-wrap">
      <div ref={containerRef} className="map-canvas" data-testid="map-canvas" />
      {plan.hidden > 0 && <div className="map-hidden-hint">已隐藏 {plan.hidden} 个点（图层开关）</div>}
      {plan.items.length === 0 && !error && (
        <div className="map-empty-hint">暂无可渲染的地点。导入照片或一个信物，就能看到它们落在地图上。</div>
      )}
    </div>
  );
});
