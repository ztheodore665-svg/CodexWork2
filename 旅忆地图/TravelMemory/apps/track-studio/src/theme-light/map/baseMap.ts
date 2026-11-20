/**
 * 底图适配层：高德 JS API 2.0 的相机驱动、标定，以及无 Key / 加载失败时的降级格网底图。
 *
 * 设计要点：
 *  - 不引入任何地图 SDK 的 npm 包，只用 <script> 动态加载；
 *  - 8 秒超时 + onerror 双保险，失败就降级，降级后播放/轨迹/HUD/时间轴照常工作；
 *  - 标定交给高德自己的 lngLatToContainer，避免整体错位。
 *
 * **加载机制已经抽到 `@travelmemory/amap-loader`**（去重 / 超时 / script 复用 /
 * 安全密钥 / Node 守卫），主应用共用同一份。这里只留展示站的策略：静默降级、
 * 占位 Key 判定、失败冷却重试 —— 主应用那边恰恰相反，失败要抛给 UI。
 */

import { invMercX, invMercY, mercX, mercY } from '@travelmemory/geo';
import { analyticProjection, TILE_SIZE, type Projection } from '@travelmemory/trail';
import { loadAmapScript } from '@travelmemory/amap-loader';

export type MapKind = 'amap' | 'fallback';
export type CoordinateSystem = 'gcj02' | 'wgs84';

/* ── 最小化的高德类型声明（只声明用到的部分，不用 any） ────────── */

interface AMapLngLat {
  lng: number;
  lat: number;
}

interface AMapLngLatConstructor {
  new (lng: number, lat: number): AMapLngLat;
}

interface AMapPixel {
  x: number;
  y: number;
}

interface AMapClickEvent {
  lnglat: AMapLngLat;
}

interface AMapMapInstance {
  getCenter(): AMapLngLat;
  /** ⚠️ immediately 默认 false = 内部过渡动画；逐帧调用必须传 true */
  setCenter(center: AMapLngLat | [number, number], immediately?: boolean): void;
  getZoom(): number;
  setZoom(zoom: number, immediately?: boolean): void;
  lngLatToContainer(position: AMapLngLat | [number, number]): AMapPixel;
  containerToLngLat(pixel: AMapPixel): AMapLngLat;
  on(event: 'click', handler: (event: AMapClickEvent) => void): void;
  on(event: 'complete', handler: () => void): void;
  on(event: 'moveend', handler: () => void): void;
  on(event: 'zoomchange', handler: () => void): void;
  setStatus(status: Record<string, unknown>): void;
  destroy(): void;
}

interface AMapNamespace {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AMapMapInstance;
  LngLat: AMapLngLatConstructor;
}

declare global {
  interface Window {
    AMap?: AMapNamespace;
  }
}

/** 展示站只给 8 秒，超时就降级，别让「用演示轨迹开始」卡住。 */
const LOAD_TIMEOUT_MS = 8000;
/** 失败后的重试冷却，避免反复插脚本 / 反复等 8 秒 */
const RETRY_COOLDOWN_MS = 2500;
/**
 * 嵌入版（浅色）用高德官方浅色底图。
 *
 * 阶段 13 这里跟 showcase 一样写死 `amap://styles/dark`，于是"浅色嵌入版"的播放页
 * 一打开就是一块暗底，与主应用的浅色页面撕裂。阶段 14（W14-2）改为 light。
 * `amap://styles/light` 与 `dark` 同属官方内置样式清单，无需额外配置；
 * 若某天发现真机上不可用，退回 `amap://styles/normal`（默认底图，同样是浅色）。
 */
const LIGHT_STYLE = 'amap://styles/light';

/** 未配置 Key（占位串）——这种情况重试也没有意义 */
let noKey = false;
/** 上次失败的时刻，用于冷却 */
let failedAt = 0;

function readEnvKey(): string {
  const raw: unknown = import.meta.env.VITE_AMAP_JS_KEY;
  return typeof raw === 'string' ? raw.trim() : '';
}

function readEnvSecurityCode(): string {
  const raw: unknown = import.meta.env.VITE_AMAP_SECURITY_CODE;
  return typeof raw === 'string' ? raw.trim() : '';
}

/** 用占位串（未配置）时不浪费时间发起请求，直接降级 */
function looksUnset(value: string): boolean {
  if (value.length === 0) return true;
  return value.includes('请') || value.includes('your') || value.length < 8;
}

/**
 * 加载高德 JS API；成功返回命名空间，失败 / 超时返回 null（静默降级）。
 *
 * 共享层统一用 reject 表示失败，这里把它折成 null —— 这是展示站与主应用唯一的
 * 策略差异（那边要把错误抛给 UI）。失败**不永久缓存**：超时只代表「这一次 8 秒内
 * 没加载完」，不代表 SDK 不可用，所以只做 2.5 秒冷却便允许重试。
 * 复用还在路上的 script、超时那一刻脚本刚好到达则直接采纳，都由共享层负责。
 */
export function loadAmap(): Promise<AMapNamespace | null> {
  if (noKey) return Promise.resolve(null);
  if (failedAt > 0 && Date.now() - failedAt < RETRY_COOLDOWN_MS) return Promise.resolve(null);

  const key = readEnvKey();
  if (looksUnset(key)) {
    noKey = true;
    return Promise.resolve(null);
  }

  return loadAmapScript<AMapNamespace>({
    key,
    securityJsCode: readEnvSecurityCode(),
    timeoutMs: LOAD_TIMEOUT_MS,
  }).then(
    (namespace) => {
      failedAt = 0;
      return namespace;
    },
    () => {
      failedAt = Date.now();
      return null;
    },
  );
}

/* ── 控制器 ──────────────────────────────────────────────────── */

export type ClickHandler = (lng: number, lat: number) => void;

export interface MapController {
  readonly kind: MapKind;
  /** 高德返回 GCJ-02，降级底图直接用 WGS84 */
  readonly coordinateSystem: CoordinateSystem;
  /** 取出当前缩放对应的解析投影（与中心无关，只需调用一次） */
  calibrate(width: number, height: number, fallbackZoom: number): Projection;
  setCenter(lng: number, lat: number): void;
  setZoom(zoom: number): void;
  setInteractive(enabled: boolean): void;
  onClick(handler: ClickHandler): void;
  /**
   * 首帧底图就绪（`complete` 首次触发）时 resolve，**上限 4000ms 也 resolve**。
   * 永不 reject、永不卡死——调用方用它决定遮罩何时淡出，不能因为地图慢就不放行。
   */
  whenReady(): Promise<void>;
  destroy(): void;
}

export interface BaseMapOptions {
  center: { lng: number; lat: number };
  zoom: number;
  interactive: boolean;
  /** 降级时是否自己画格网（播放模式由渲染器统一画，这里传 false） */
  paintFallback: boolean;
  /**
   * 交互模式下用户自己拖动 / 缩放地图后回调。
   * 只在 interactive === true 时触发——播放模式由相机驱动，回调会和阻尼抢控制权。
   */
  onViewChange?: (view: MapView) => void;
}

export interface MapView {
  lng: number;
  lat: number;
  zoom: number;
  userInitiated: boolean;
}

/** `lngLatToContainer` 一次性校验时用的角度探针 */
const PROBE_DEG = 0.05;

function createAmapController(
  amap: AMapNamespace,
  container: HTMLElement,
  options: BaseMapOptions,
): MapController {
  const map = new amap.Map(container, {
    zoom: options.zoom,
    center: [options.center.lng, options.center.lat],
    viewMode: '2D',
    mapStyle: LIGHT_STYLE,
    features: ['bg', 'road', 'building', 'point'],
    showLabel: true,
    // 关掉地图内部缓动：逐帧 setCenter 时必需，否则每次调用都起一段 tween，帧与帧互相打架
    animateEnable: false,
    jogEnable: false,
  });

  // 自己维护相机状态。getCenter() 是「实时中心点」，动画未结束时读到的是中间值，不能用来积分。
  let centerLng = options.center.lng;
  let centerLat = options.center.lat;
  let zoomLevel = options.zoom;
  let width = 0;
  let height = 0;
  /** 实测 / 解析 的比例修正（只在容器像素 ≠ CSS 像素时才会偏离 1） */
  let scaleFactor = 1;
  let verified = false;

  /**
   * 唯一一次把解析公式与地图真实投影对一遍：确认高 DPI 下容器像素是否等于 CSS 像素。
   * 这不依赖 getCenter()——我们拿的是自己下发的中心点。
   */
  const verifyScale = (): void => {
    if (verified || width <= 0) return;
    verified = true;
    try {
      const p0 = map.lngLatToContainer(new amap.LngLat(centerLng, centerLat));
      const p1 = map.lngLatToContainer(new amap.LngLat(centerLng + PROBE_DEG, centerLat));
      const dMx = mercX(centerLng + PROBE_DEG) - mercX(centerLng);
      if (!(dMx > 0)) return;
      const measured = (p1.x - p0.x) / dMx;
      const analytic = TILE_SIZE * Math.pow(2, zoomLevel);
      if (!Number.isFinite(measured) || measured <= 0) return;
      const ratio = measured / analytic;
      // 三档处理：
      //   0.98 ~ 1.02 → 正常的高 DPI / 舍入误差，采纳实测比例；
      //   0.90 ~ 0.98 或 1.02 ~ 1.10 → 可疑区间，不采纳（保持 1）；
      //   <0.90 或 >1.10 → 离谱，判定为「地图还没就绪」的测量失败，保持 1。
      // 原则：宁可回到已被证明与 EPSG:3857 逐位吻合的解析公式，
      // 也绝不把一个测量误差永久烘焙进投影（那会让整条轨迹一直偏）。
      scaleFactor = ratio > 0.98 && ratio < 1.02 ? ratio : 1;
    } catch {
      /* 忽略：解析公式已经是可靠默认值 */
    }
  };

  map.on('complete', verifyScale);
  const verifyTimer = window.setTimeout(verifyScale, 1500);

  // ── 首帧就绪（供上层淡出遮罩） ────────────────────────────────
  // complete 可能多次触发，用 settled 幂等；4s 上限保证地图慢时也不会把遮罩永远留在屏幕上。
  let readySettled = false;
  let resolveReady: (() => void) | null = null;
  const readyPromise = new Promise<void>((resolve) => {
    resolveReady = resolve;
  });
  const settleReady = (): void => {
    if (readySettled) return;
    readySettled = true;
    window.clearTimeout(readyTimer);
    resolveReady?.();
  };
  map.on('complete', settleReady);
  const readyTimer = window.setTimeout(settleReady, 4000);

  let interactive = options.interactive;
  const applyInteractive = (): void => {
    map.setStatus({
      dragEnable: interactive,
      zoomEnable: interactive,
      scrollWheel: interactive,
      doubleClickZoom: interactive,
      rotateEnable: interactive,
      pitchEnable: interactive,
      keyboardEnable: interactive,
    });
  };
  applyInteractive();

  // 交互模式下用户会自己拖动 / 缩放，控制器里的相机状态必须跟着走，
  // 否则 calibrate() 还按旧中心投影，任何叠加层都会整体错位。
  // moveend 在我们每帧 setCenter 时也会触发，所以必须先判 interactive。
  map.on('moveend', () => {
    if (!interactive) return;
    const current = map.getCenter();
    centerLng = current.lng;
    centerLat = current.lat;
    zoomLevel = map.getZoom();
    options.onViewChange?.({ lng: centerLng, lat: centerLat, zoom: zoomLevel, userInitiated: true });
  });
  map.on('zoomchange', () => {
    if (!interactive) return;
    zoomLevel = map.getZoom();
  });

  return {
    kind: 'amap',
    coordinateSystem: 'gcj02',
    calibrate(w: number, h: number, fallbackZoom: number): Projection {
      width = Math.max(1, Math.round(w));
      height = Math.max(1, Math.round(h));
      const zoom = Number.isFinite(zoomLevel) ? zoomLevel : fallbackZoom;
      return analyticProjection({ lng: centerLng, lat: centerLat }, zoom, width, height, scaleFactor);
    },
    setCenter(lng: number, lat: number): void {
      centerLng = lng;
      centerLat = lat;
      // immediately = true：逐帧调用时不要内部的过渡动画
      map.setCenter([lng, lat], true);
    },
    setZoom(zoom: number): void {
      zoomLevel = zoom;
      map.setZoom(zoom, true);
    },
    setInteractive(enabled: boolean): void {
      interactive = enabled;
      applyInteractive();
    },
    onClick(handler: ClickHandler): void {
      map.on('click', (event) => {
        handler(event.lnglat.lng, event.lnglat.lat);
      });
    },
    whenReady(): Promise<void> {
      return readyPromise;
    },
    destroy(): void {
      window.clearTimeout(verifyTimer);
      window.clearTimeout(readyTimer);
      settleReady();
      try {
        map.destroy();
      } catch {
        /* 某些版本重复 destroy 会抛错，忽略 */
      }
    },
  };
}

/**
 * 降级底图：自绘格网画布，支持拖拽平移 / 滚轮缩放 / 点击取点。
 * 目的只有一个——没有可用的地图 Key 时，「手工补地点」这条路依然走得通。
 */
function createFallbackController(container: HTMLElement, options: BaseMapOptions): MapController {
  let centerLng = options.center.lng;
  let centerLat = options.center.lat;
  let zoom = options.zoom;
  let interactive = options.interactive;
  let clickHandler: ClickHandler | null = null;
  let width = 0;
  let height = 0;
  let dragging = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;
  let frame = 0;

  const canvas = document.createElement('canvas');
  let ctx: CanvasRenderingContext2D | null = null;
  if (options.paintFallback) {
    canvas.style.cssText =
      'position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:auto;';
    container.appendChild(canvas);
    ctx = canvas.getContext('2d');
  }

  const projectionOf = (): Projection =>
    analyticProjection({ lng: centerLng, lat: centerLat }, zoom, width, height);

  const paint = (): void => {
    if (!ctx) return;
    const proj = projectionOf();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (width === 0 || height === 0) return;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const bg = ctx.createRadialGradient(
      width * 0.5,
      height * 0.42,
      0,
      width * 0.5,
      height * 0.42,
      Math.hypot(width, height) * 0.62,
    );
    bg.addColorStop(0, '#f7f9fc');
    bg.addColorStop(0.55, '#eef3f9');
    bg.addColorStop(1, '#e4ecf4');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    const spacing = zoom >= 11 ? 0.05 : zoom >= 9.5 ? 0.1 : zoom >= 8 ? 0.25 : 0.5;
    const minLng = invMercX(proj.cx + (0 - proj.ox) / proj.scaleX);
    const maxLng = invMercX(proj.cx + (width - proj.ox) / proj.scaleX);
    const maxLat = invMercY(proj.cy + (0 - proj.oy) / proj.scaleY);
    const minLat = invMercY(proj.cy + (height - proj.oy) / proj.scaleY);

    ctx.strokeStyle = 'rgba(14,123,181,0.10)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let lng = Math.floor(minLng / spacing) * spacing; lng <= maxLng; lng += spacing) {
      const x = Math.round(proj.ox + (mercX(lng) - proj.cx) * proj.scaleX) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
    }
    for (let lat = Math.floor(minLat / spacing) * spacing; lat <= maxLat; lat += spacing) {
      const y = Math.round(proj.oy + (mercY(lat) - proj.cy) * proj.scaleY) + 0.5;
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
    }
    ctx.stroke();

    ctx.strokeStyle = 'rgba(14,123,181,0.35)';
    ctx.beginPath();
    ctx.moveTo(width / 2 - 7, height / 2);
    ctx.lineTo(width / 2 + 7, height / 2);
    ctx.moveTo(width / 2, height / 2 - 7);
    ctx.lineTo(width / 2, height / 2 + 7);
    ctx.stroke();

    ctx.font = '10px ui-monospace, "SF Mono", Menlo, monospace';
    ctx.fillStyle = 'rgba(91,107,127,0.75)';
    ctx.fillText(`GRID  ${spacing.toFixed(2)}°   Z${zoom.toFixed(1)}`, 16, height - 16);
  };

  // 视图变更通知用 rAF 合并：wheel 每秒能触发 30~60 次，
  // 逐个回调会让消费方（如果做 setState）渲染风暴。
  // 注意：重绘必须无条件发生，不能挂在「有没有 onViewChange」上——
  // 否则将来有人删掉调用点的 schedule() 就会静默砍掉降级底图的滚轮重绘。
  let viewChangePending = false;
  const queueViewChange = (): void => {
    viewChangePending = Boolean(options.onViewChange);
    schedule();
  };

  const schedule = (): void => {
    if (frame !== 0) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      paint();
      if (viewChangePending) {
        viewChangePending = false;
        options.onViewChange?.({ lng: centerLng, lat: centerLat, zoom, userInitiated: true });
      }
    });
  };

  const measure = (): void => {
    const rect = container.getBoundingClientRect();
    width = Math.max(1, Math.round(rect.width));
    height = Math.max(1, Math.round(rect.height));
    schedule();
  };

  const toLngLat = (clientX: number, clientY: number): { lng: number; lat: number } => {
    const rect = canvas.getBoundingClientRect();
    const proj = projectionOf();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    return {
      lng: invMercX(proj.cx + (px - proj.ox) / proj.scaleX),
      lat: invMercY(proj.cy + (py - proj.oy) / proj.scaleY),
    };
  };

  const onPointerDown = (event: PointerEvent): void => {
    if (!interactive) return;
    dragging = true;
    moved = false;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: PointerEvent): void => {
    if (!dragging || !interactive) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
    lastX = event.clientX;
    lastY = event.clientY;
    const proj = projectionOf();
    centerLng = invMercX(mercX(centerLng) - dx / proj.scaleX);
    centerLat = invMercY(mercY(centerLat) - dy / proj.scaleY);
    schedule();
  };

  const onPointerUp = (event: PointerEvent): void => {
    if (!dragging) return;
    dragging = false;
    try {
      canvas.releasePointerCapture(event.pointerId);
    } catch {
      /* 指针可能已经释放 */
    }
    if (moved) {
      queueViewChange();
      return;
    }
    if (!clickHandler) return;
    const point = toLngLat(event.clientX, event.clientY);
    clickHandler(point.lng, point.lat);
  };

  const onWheel = (event: WheelEvent): void => {
    if (!interactive) return;
    event.preventDefault();
    zoom = Math.min(18, Math.max(3, zoom - event.deltaY * 0.0018));
    queueViewChange(); // 内部会无条件 schedule() 重绘
  };

  if (options.paintFallback) {
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    measure();
    window.addEventListener('resize', measure);
  }

  return {
    kind: 'fallback',
    coordinateSystem: 'wgs84',
    /** 降级底图是自绘的，没有瓦片加载过程，立即算就绪 */
    whenReady(): Promise<void> {
      return Promise.resolve();
    },
    calibrate(w: number, h: number, fallbackZoom: number): Projection {
      width = Math.max(1, Math.round(w));
      height = Math.max(1, Math.round(h));
      const center = { lng: centerLng, lat: centerLat };
      return analyticProjection(center, Number.isFinite(zoom) ? zoom : fallbackZoom, width, height);
    },
    setCenter(lng: number, lat: number): void {
      centerLng = lng;
      centerLat = lat;
      schedule();
    },
    setZoom(next: number): void {
      zoom = next;
      schedule();
    },
    setInteractive(enabled: boolean): void {
      interactive = enabled;
    },
    onClick(handler: ClickHandler): void {
      clickHandler = handler;
    },
    destroy(): void {
      if (frame !== 0) window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
      if (options.paintFallback) {
        canvas.removeEventListener('pointerdown', onPointerDown);
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('pointerup', onPointerUp);
        canvas.removeEventListener('wheel', onWheel);
        if (canvas.parentElement === container) container.removeChild(canvas);
      }
    },
  };
}

/**
 * 创建底图控制器。加载失败不会 reject —— 永远返回一个可用的控制器（可能是降级版）。
 */
export async function createBaseMap(
  container: HTMLElement,
  options: BaseMapOptions,
): Promise<MapController> {
  const amap = await loadAmap();
  if (!amap) return createFallbackController(container, options);
  try {
    const controller = createAmapController(amap, container, options);
    if (!options.interactive) controller.setInteractive(false);
    return controller;
  } catch {
    return createFallbackController(container, options);
  }
}
