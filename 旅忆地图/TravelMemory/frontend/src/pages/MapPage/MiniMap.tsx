/** 补标用的**内嵌小地图**：点一下就拿到经纬度。
 *
 * ## 为什么不复用 `MapCanvas`
 *
 * `MapCanvas` 是全屏地图壳的渲染器：它吃一份 `RenderPlan`、按图层开关决定
 * 画圆还是画 Marker、还要管 fitBounds。补标对话框要的是另一件事 ——
 * 一张干净的地图，用户点哪就报哪个坐标，多一个像素的装饰都是干扰。
 * 硬把 `MapCanvas` 塞进对话框，得给它造一份假 plan、再想办法把点击事件
 * 从覆盖物里捞出来，而它内部根本没有"点击空白处"这个概念。
 *
 * ## 为什么只用 JS API key，不用高德的地点搜索
 *
 * 地点搜索（输入提示）走的是高德 **Web 服务 API**，要 `AMAP_API_KEY` ——
 * 那把 key 是给服务端用的，打进浏览器产物等于送人。而 JS API key
 * 的设计就是在浏览器里公开使用（靠 referer 白名单保护）。
 * 所以这里做"点地图选点"而不是"搜索地名"：能力对等，密钥不越权。
 *
 * ## 降级路径（PROJECT.md §二、[8]）
 *
 * key 没配 / 脚本加载失败 → 显示占位卡，对话框其余部分（坐标输入框）照常工作。
 */

import { useEffect, useRef, useState } from 'react';
import { MapPin } from 'lucide-react';

import {
  isAmapReady,
  loadAmap,
  type AmapMap,
  type AmapNamespace,
  type AmapOverlay,
} from './amap';
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../lib/mapView';

export interface MiniMapProps {
  /** 高德 JS API key。 */
  jsKey: string;
  securityCode?: string;
  /** 初始坐标（已定位的照片传它当前的 WGS84 坐标）；null = 用默认视野。 */
  initial?: { lat: number; lng: number } | null;
  /** 用户点地图时回调（WGS84）。 */
  onPick: (lat: number, lng: number) => void;
}

/** 容器高度。对话框里 260px 够看清街道名，又不至于把表单挤下去。 */
const MAP_HEIGHT = 260;

export function MiniMap({ jsKey, securityCode = '', initial = null, onPick }: MiniMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<AmapMap | null>(null);
  const markerRef = useRef<AmapOverlay | null>(null);
  const [amap, setAmap] = useState<AmapNamespace | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  // 建地图 + 绑点击（只建一次）
  useEffect(() => {
    if (!amap || !containerRef.current || mapRef.current) return;
    const center: [number, number] = initial
      ? [initial.lng, initial.lat]
      : [DEFAULT_MAP_CENTER[0], DEFAULT_MAP_CENTER[1]];

    mapRef.current = new amap.Map(containerRef.current, {
      viewMode: '2D',
      zoom: initial ? 12 : DEFAULT_MAP_ZOOM + 2,
      center,
    });
    const map = mapRef.current;

    // 点击空白处取坐标。绑在 map 上而不是 Marker 上：Marker 只能拖，
    // "点哪算哪"才是补标的自然交互。
    const handler = (event: { lnglat?: { lng: number; lat: number } }): void => {
      const lnglat = event.lnglat;
      if (!lnglat) return;
      onPick(lnglat.lat, lnglat.lng);
    };
    map.on('click', handler as unknown as () => void);

    return () => {
      map.off('click', handler as unknown as () => void);
      markerRef.current?.setMap(null);
      markerRef.current = null;
      map.destroy();
      mapRef.current = null;
    };
    // onPick 不进依赖：它每次 render 都是新函数，进去会让地图反复重建。
    // 用 ref 保持最新引用，见下面那个 effect。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [amap, initial]);

  // 跟随选中的坐标放 Marker
  const pickedRef = useRef(onPick);
  pickedRef.current = onPick;
  useEffect(() => {
    const map = mapRef.current;
    if (!amap || !map) return;

    const place = (lat: number, lng: number): void => {
      markerRef.current?.setMap(null);
      markerRef.current = new amap.Marker({
        position: [lng, lat],
        // 可拖拽：手滑点偏半条街时，拖动比重新点一次更快
        draggable: true,
      });
      markerRef.current.setMap(map);
      map.setCenter([lng, lat]);
    };

    if (initial) place(initial.lat, initial.lng);
    // 点击地图时由上面的 handler 调 onPick → 上层 setState → initial 变化 → 这里重新放
  }, [amap, initial]);

  if (error) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-muted/40 text-center"
        style={{ height: MAP_HEIGHT }}
        role="status"
      >
        <MapPin className="h-5 w-5 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">地图不可用，请直接用下方输入框填坐标</p>
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-lg border">
      <div ref={containerRef} data-testid="mini-map" style={{ height: MAP_HEIGHT }} />
      <p className="absolute bottom-1.5 left-1/2 -translate-x-1/2 rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground shadow-sm backdrop-blur">
        点地图选点，Marker 可拖动微调
      </p>
    </div>
  );
}
