/** 信物地图（W12-4）：有 Key 走高德，没 Key 走自绘示意格网。
 *
 * ## 两条路径，同一个交互
 *
 * ```
 * hasAmapKey() === false  →  一个请求都不发，SVG 画格网 + 圆点（点圆点同样 onSelect）
 * hasAmapKey() === true   →  动态 import 高德适配层 → Circle / Marker（点一下同样 onSelect）
 * ```
 *
 * "没 Key 也要能用"不是降级妥协，而是**默认路径**：这个应用的主场是"零后端、
 * 私密分享"，绝大多数部署不会配地图 Key。所以未配置时的文案必须诚实：
 * 「未配置地图服务，仅显示散布示意」—— 让人一眼知道这不是真实地理位置。
 *
 * ## 为什么动态 import
 *
 * `index.html`（standalone）的 CSP **一个地图域名都没有**。若这里静态 import
 * `../map/amap`，`webapi.amap.com` 会被打进所有入口共享的 chunk —— 虽然不会发请求，
 * 但"零网络"的证据链会被搅浑。动态 import 让高德适配层单独成 chunk，
 * 只有"embed + 有 Key + 真的要画地图"时才被拉取。
 *
 * ## 坐标系
 *
 * `UnpackedPlace.lat/lng` 是 **WGS84**（协议里存的值）。高德要 GCJ-02，所以
 * **给高德之前**转一次（`wgs84ToGcj02`）；降级示意不需要转（它连底图都没有，
 * 转了只会让"示意"和"真实位置"混为一谈）。
 *
 * 注意 Marker / Circle 的坐标都用**转换后**的 GCJ-02：在高德上画 WGS84 会整体偏
 * 300~700 米，肉眼看不出、但每个点都是错的。
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { wgs84ToGcj02 } from '@travelmemory/geo';

import { hasAmapKey, readAmapKey, readAmapSecurityCode } from '../../env';
import type { AmapMapInstance, AmapNamespace, AmapOverlayInstance } from '../../map/amap';
import { DEFAULT_PADDING, planPlaceOverlays, projectPlaces } from '../../map/planPlaces';
import type { UnpackedPlace } from '../../types/share';

export interface PlaceMapProps {
  places: readonly UnpackedPlace[];
  /** 点了某个地点（地图上的圈/点）。 */
  onSelect: (placeId: string) => void;
}

/** 降级示意的画布坐标系（SVG `viewBox`，实际尺寸由 CSS 等比缩放）。 */
const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 620;
/** 格网间距（viewBox 单位）。 */
const GRID_STEP = 50;
/** 圆点半径（viewBox 单位）。 */
const DOT_RADIUS = 12;

/** 地图渲染路径。`loading` 只出现在"有 Key、正在拉脚本"的短暂窗口。 */
type MapMode = 'loading' | 'amap' | 'fallback';

/** 承诺圈的样式（与主应用 `SHARE_CIRCLE_STYLE` 同一套语义色）。 */
const CIRCLE_STYLE = {
  fillColor: '#2f6f5e',
  fillOpacity: 0.22,
  strokeColor: '#2f6f5e',
  strokeWeight: 1,
  strokeOpacity: 0.7,
} as const;

export function PlaceMap({ places, onSelect }: PlaceMapProps): JSX.Element {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [mode, setMode] = useState<MapMode>(() => (hasAmapKey() ? 'loading' : 'fallback'));
  const [errorNote, setErrorNote] = useState<string | null>(null);

  // 高德的 click 回调在挂载时注册一次，读不到最新 props，用 ref 透传。
  const selectRef = useRef(onSelect);
  useEffect(() => {
    selectRef.current = onSelect;
  }, [onSelect]);

  const overlays = useMemo(() => planPlaceOverlays(places), [places]);
  const projected = useMemo(
    () => projectPlaces(places, VIEW_WIDTH, VIEW_HEIGHT, DEFAULT_PADDING),
    [places],
  );
  /** 降级示意里也要显示名字（SVG `<title>`），所以给一份 id → name 的表。 */
  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const place of places) map.set(place.placeId, place.name);
    return map;
  }, [places]);

  useEffect(() => {
    // 没有 Key：**立刻**走示意，连 import 都不执行（一个网络请求都不会有）
    if (!hasAmapKey()) {
      setMode('fallback');
      setErrorNote(null);
      return;
    }

    const host = hostRef.current;
    if (!host) return;

    let cancelled = false;
    let map: AmapMapInstance | null = null;

    void (async () => {
      try {
        const { loadAmap } = await import('../../map/amap');
        const amap: AmapNamespace = await loadAmap(readAmapKey(), readAmapSecurityCode());
        if (cancelled) return;

        // WGS84 → GCJ-02（高德口径）。所有点先转好，中心与覆盖物用同一批值。
        const gcj = overlays.map((item) => wgs84ToGcj02(item.lng, item.lat));
        const center: [number, number] =
          gcj.length > 0
            ? [
                gcj.reduce((sum, point) => sum + point.lng, 0) / gcj.length,
                gcj.reduce((sum, point) => sum + point.lat, 0) / gcj.length,
              ]
            : [116.397428, 39.90923]; // 一个点都没有时的兜底视野（北京），与主应用默认不同是刻意的

        map = new amap.Map(host, {
          viewMode: '2D',
          zoom: 9,
          center,
          mapStyle: 'amap://styles/dark',
        });

        overlays.forEach((item, index) => {
          const point = gcj[index];
          if (!point || !map) return;
          const overlay: AmapOverlayInstance =
            item.kind === 'circle'
              ? new amap.Circle({
                  center: [point.lng, point.lat],
                  radius: item.radius,
                  ...CIRCLE_STYLE,
                })
              : new amap.Marker({
                  position: [point.lng, point.lat],
                  title: nameById.get(item.placeId) ?? '',
                });
          overlay.on('click', () => selectRef.current(item.placeId));
          map.add(overlay);
        });

        setMode('amap');
        setErrorNote(null);
      } catch (error) {
        if (cancelled) return;
        // 地图挂了不影响信物本身：地点列表与验签报告照常展示
        setErrorNote(error instanceof Error ? error.message : String(error));
        setMode('fallback');
      }
    })();

    return () => {
      cancelled = true;
      try {
        map?.destroy();
      } catch {
        /* 某些版本重复 destroy 会抛错，忽略 */
      }
      map = null;
    };
  }, [overlays, nameById]);

  return (
    <div className="place-map">
      {/* 高德会把 DOM 塞进这个容器；降级时它是个空盒子，由下面的 SVG 盖住 */}
      <div className="place-map-host" ref={hostRef} data-testid="place-map-host" />

      {mode !== 'amap' ? (
        <svg
          className="place-map-fallback"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="地点散布示意"
        >
          <rect x={0} y={0} width={VIEW_WIDTH} height={VIEW_HEIGHT} className="place-map-bg" />
          {Array.from({ length: Math.floor(VIEW_WIDTH / GRID_STEP) + 1 }, (_unused, index) => (
            <line
              key={`v${index}`}
              x1={index * GRID_STEP}
              y1={0}
              x2={index * GRID_STEP}
              y2={VIEW_HEIGHT}
              className="place-map-grid"
            />
          ))}
          {Array.from({ length: Math.floor(VIEW_HEIGHT / GRID_STEP) + 1 }, (_unused, index) => (
            <line
              key={`h${index}`}
              x1={0}
              y1={index * GRID_STEP}
              x2={VIEW_WIDTH}
              y2={index * GRID_STEP}
              className="place-map-grid"
            />
          ))}
          {projected.map((point) => (
            <circle
              key={point.placeId}
              cx={point.x}
              cy={point.y}
              r={DOT_RADIUS}
              className="place-map-dot"
              role="button"
              tabIndex={0}
              aria-label={nameById.get(point.placeId) ?? point.placeId}
              onClick={() => selectRef.current(point.placeId)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  selectRef.current(point.placeId);
                }
              }}
            >
              <title>{nameById.get(point.placeId) ?? point.placeId}</title>
            </circle>
          ))}
        </svg>
      ) : null}

      {mode === 'loading' ? <p className="place-map-note">正在加载地图…</p> : null}
      {mode === 'fallback' ? (
        <p className="place-map-note">
          未配置地图服务，仅显示散布示意
          {overlays.length === 0 ? ' · 这个信物里的地点没有坐标' : ''}
          {errorNote ? `（${errorNote}）` : ''}
        </p>
      ) : null}
    </div>
  );
}
