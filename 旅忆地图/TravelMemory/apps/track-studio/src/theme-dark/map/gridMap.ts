/** 深色版的底图：**只有自绘降级格网**，一行网络代码都没有（阶段 13 / W13-2）。
 *
 * ## 从哪复制、删了什么
 *
 * 复制来源：`apps/track-studio/src/theme-light/map/baseMap.ts`（与
 * `showcase/src/map/baseMap.ts` 同源，563 行）里的 `createFallbackController`，
 * 加上它依赖的投影/格网绘制代码。**删掉**的部分：
 *
 *  1. 地图脚本的动态加载、8s 超时、2.5s 失败冷却、占位 Key 判定；
 *  2. 整个联网底图的 SDK 控制器（`lngLatToContainer` 标定、25Hz `setCenter`、
 *     `whenReady` 4s 上限、`setStatus` 交互开关）；
 *  3. 联网/降级两条分支的选择与 `coordinateSystem` 的二选一 —— 现在恒为 WGS84 直投。
 *
 * **保留**的部分（每一条都是上游踩过的坑）：
 *
 *  - `analyticProjection` 投影（与 `@travelmemory/trail` 的渲染器同一把尺子）；
 *  - 经纬网 + 中心十字 + 右下角 `GRID 0.05° Z11` 标注 —— 轨迹需要参照物，
 *    纯黑背景会让"光点在动"完全失去尺度感（W14-5 起改用省级轮廓，格网退为降级底图）；
 *  - 拖拽平移 / 滚轮缩放 / 点击取点（点击**返回 WGS84**：平面是 GCJ 时自动换算，
 *    平面是 WGS84 时原样返回 —— 见 `planeToWgs84`）；
 *  - 「重绘用 rAF 合并」：wheel 每秒能触发 30~60 次，逐次重绘会渲染风暴；
 *  - 重绘**无条件**发生，不挂在"有没有 onViewChange"上（否则删掉一个回调
 *    就静默砍掉滚轮重绘）。
 *
 * ## 与播放主循环的配合
 *
 * 播放页传 `paintGrid: false`：格网由 `@travelmemory/trail` 的渲染器统一画
 * （`DrawState.fallback = true`），底图这层再画一遍会出现双层网格摩尔纹。
 * 补点页传 `paintGrid: true`：那里没有渲染器，格网必须由本层自己画。
 */

import { gcj02ToWgs84, invMercX, invMercY, mercX, mercY } from '@travelmemory/geo';
import { analyticProjection, type Projection } from '@travelmemory/trail';

import {
  loadProvinces,
  outlineCacheKey,
  renderChinaOutline,
  type ProvinceCollection,
  type ProvinceFrame,
} from './chinaOutline';

/** 网格间距（度）：缩放越大格子越细。与渲染器的 `gridSpacingFor` 保持同一套档位。 */
function gridSpacingFor(zoom: number): number {
  if (zoom >= 11) return 0.05;
  if (zoom >= 9.5) return 0.1;
  if (zoom >= 8) return 0.25;
  return 0.5;
}

export type GridClickHandler = (lng: number, lat: number) => void;

/**
 * 屏幕点 → 平面坐标（**调用方声明的坐标系**，不做任何基准换算）。
 *
 * 抽成纯函数是为了能单测：`toLngLat` 需要一个真实 canvas 的 `getBoundingClientRect`，
 * 而这条换算本身与 DOM 无关。
 */
export function screenPointToPlane(
  projection: Projection,
  px: number,
  py: number,
): { lng: number; lat: number } {
  return {
    lng: invMercX(projection.cx + (px - projection.ox) / projection.scaleX),
    lat: invMercY(projection.cy + (py - projection.oy) / projection.scaleY),
  };
}

/**
 * 点击结果的**唯一转换边界**：把平面坐标换成 WGS84 再交给上层。
 *
 * 上层（`core/validate` 的 `applyLocation` → IndexedDB / EXIF 存储）一律按 **WGS84** 存，
 * 这是全项目的红线。所以：
 *  - 平面是 `'gcj02'`（播放页的中心来自 `wgs84ToGcj02`）→ 必须 `gcj02ToWgs84`；
 *  - 平面是 `'wgs84'`（补点页的中心来自照片 EXIF）→ 原样返回，**再转一次就会错 500m**。
 */
export function planeToWgs84(
  frame: ProvinceFrame,
  lng: number,
  lat: number,
): { lng: number; lat: number } {
  return frame === 'gcj02' ? gcj02ToWgs84(lng, lat) : { lng, lat };
}

/**
 * 深色底上的省界配色（W14-5）。
 *
 * 三条口径：普通边界要"看得见但不抢轨迹"（42% 透明），省界（每个面的外环）
 * 提亮到 72% + 粗 0.5px 让相邻两省之间有一条明确的分界，填充只给 28%
 * 的深蓝，用来把陆地和背景区分开、同时不压掉轨迹的荧光。
 */
const OUTLINE_STYLE = {
  color: 'rgba(100,180,255,0.42)',
  majorColor: 'rgba(120,200,255,0.72)',
  lineWidth: 0.9,
  fillColor: 'rgba(20,40,80,0.28)',
} as const;

/** 建离屏画布：优先 `OffscreenCanvas`，不支持（或抛错）时退回 DOM canvas。 */
function createOutlineSurface(
  width: number,
  height: number,
  dpr: number,
): {
  canvas: HTMLCanvasElement | OffscreenCanvas;
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
} | null {
  const pixelWidth = Math.max(1, Math.round(width * dpr));
  const pixelHeight = Math.max(1, Math.round(height * dpr));

  if (typeof OffscreenCanvas !== 'undefined') {
    try {
      const offscreen = new OffscreenCanvas(pixelWidth, pixelHeight);
      const offCtx = offscreen.getContext('2d');
      if (offCtx) {
        offCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        offCtx.clearRect(0, 0, width, height);
        return { canvas: offscreen, ctx: offCtx };
      }
    } catch {
      /* 某些浏览器禁用了 OffscreenCanvas，退回 DOM canvas */
    }
  }

  const fallback = document.createElement('canvas');
  fallback.width = pixelWidth;
  fallback.height = pixelHeight;
  const ctx = fallback.getContext('2d');
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { canvas: fallback, ctx };
}

export interface GridMapController {
  /** 永远是 `'grid'`：本主题不加载任何底图 SDK。 */
  readonly kind: 'grid';
  /**
   * 传入的 `center`（以及内部平面）所用的坐标系。
   *
   * **注意**：它描述的是"平面"，不是"点击回调的返回值" —— 点击回调整理成 WGS84 后才交给上层
   * （见 `planeToWgs84`）。
   */
  readonly coordinateSystem: ProvinceFrame;
  /** 取出当前缩放对应的解析投影（与中心无关，只需调用一次）。 */
  calibrate(width: number, height: number, fallbackZoom: number): Projection;
  setCenter(lng: number, lat: number): void;
  setZoom(zoom: number): void;
  setInteractive(enabled: boolean): void;
  onClick(handler: GridClickHandler): void;
  /** 自绘格网没有瓦片要等，**立即** resolve（调用方据此揭幕）。 */
  whenReady(): Promise<void>;
  /** 省界轮廓是否已就绪（`false` = 仍在画格网）。播放页据此决定要不要让内核画格网。 */
  isOutlineReady(): boolean;
  destroy(): void;
}

export interface GridMapOptions {
  center: { lng: number; lat: number };
  zoom: number;
  /**
   * `center` 的坐标系，**必填**（这次就是把坑填在这里）。
   *
   * - 补点页：中心来自照片 EXIF → `'wgs84'`；
   * - 播放页：中心来自 `wgs84ToGcj02(相机)`，且轨迹也在 GCJ 平面 → `'gcj02'`。
   *
   * 它同时决定省界要不要换算（见 `ChinaOutlineOptions.frame`）与点击回调的换算方向。
   */
  coordinateSystem: ProvinceFrame;
  /** 播放态传 false：相机由主循环驱动，开交互会和阻尼抢控制权。 */
  interactive: boolean;
  /** 是否由本层自己画格网（播放页 false，格网交给渲染器）。 */
  paintGrid: boolean;
  /**
   * 是否尝试加载本地省界轮廓（W14-5，默认 `true`）。
   *
   * 成功：底图改画省界（**不再画格网**）；失败/为空：保持格网，行为与阶段 13 完全一致。
   * 关掉它（`false`）可以强制回到纯格网 —— QA 用它验证"加载失败退化"不必真改文件名。
   */
  outline?: boolean;
  /** 省界就绪状态变化。`true` 表示"从现在起不会再画格网"。 */
  onOutlineChange?: (ready: boolean) => void;
}

/**
 * 建一个只画格网的底图控制器。
 *
 * 不返回 Promise：没有"加载失败"这回事，同步建好即可 —— 这也是它比上层
 * 适配层简单的地方（上游的 `Promise<MapController>` 是为了容纳脚本加载）。
 */
export function createGridMap(container: HTMLElement, options: GridMapOptions): GridMapController {
  let centerLng = options.center.lng;
  let centerLat = options.center.lat;
  let zoom = options.zoom;
  let interactive = options.interactive;
  let clickHandler: GridClickHandler | null = null;
  let width = 0;
  let height = 0;
  let dragging = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;
  let frame = 0;

  const canvas = document.createElement('canvas');
  let ctx: CanvasRenderingContext2D | null = null;

  /** 省界数据（异步就绪）；`null` = 还没到 / 加载失败。 */
  let provinces: ProvinceCollection | null = null;
  /** 省界是否已就绪 —— `paint` 用它决定画轮廓还是画格网。 */
  let outlineReady = false;
  /** 离屏位图缓存：同一个缓存键只重建一次。 */
  let outlineCache: {
    key: string;
    dpr: number;
    canvas: HTMLCanvasElement | OffscreenCanvas;
    ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
  } | null = null;

  // 播放页传 `paintGrid: false`（格网交给内核渲染器），但省界仍然要由本层画 ——
  // 所以"要不要这张画布"的判据是 `paintGrid || outline`，不是 `paintGrid`。
  const wantsCanvas = options.paintGrid || options.outline !== false;
  if (wantsCanvas) {
    canvas.style.cssText =
      'position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:auto;';
    container.appendChild(canvas);
    ctx = canvas.getContext('2d');
  }

  const projectionOf = (): Projection =>
    analyticProjection({ lng: centerLng, lat: centerLat }, zoom, width, height);

  /** 画一帧底图：底色调 + （省界轮廓 | 经纬格网）+ 中心十字 + 角标。 */
  const paint = (): void => {
    if (!ctx) return;
    if (width === 0 || height === 0) return;
    const proj = projectionOf();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 底色：与深色主题同一个色系（中心略亮，四周压暗），不像"空白"也不抢轨迹
    const bg = ctx.createRadialGradient(
      width * 0.5,
      height * 0.42,
      0,
      width * 0.5,
      height * 0.42,
      Math.hypot(width, height) * 0.62,
    );
    bg.addColorStop(0, '#0a1523');
    bg.addColorStop(0.55, '#070c16');
    bg.addColorStop(1, '#04070d');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    if (outlineReady && provinces) {
      paintOutline(proj, dpr);
    } else if (options.paintGrid) {
      paintGrid(proj);
    }

    // 中心十字：让"镜头在哪"一眼可见（两种底图都保留，它是取点时的参照物）
    ctx.strokeStyle = 'rgba(34,211,238,0.35)';
    ctx.beginPath();
    ctx.moveTo(width / 2 - 7, height / 2);
    ctx.lineTo(width / 2 + 7, height / 2);
    ctx.moveTo(width / 2, height / 2 - 7);
    ctx.lineTo(width / 2, height / 2 + 7);
    ctx.stroke();
  };

  /** 经纬格网（省界不可用时的底图，与阶段 13 逐字相同）。 */
  const paintGrid = (proj: Projection): void => {
    if (!ctx) return;
    const spacing = gridSpacingFor(zoom);
    const minLng = invMercX(proj.cx + (0 - proj.ox) / proj.scaleX);
    const maxLng = invMercX(proj.cx + (width - proj.ox) / proj.scaleX);
    const maxLat = invMercY(proj.cy + (0 - proj.oy) / proj.scaleY);
    const minLat = invMercY(proj.cy + (height - proj.oy) / proj.scaleY);

    ctx.strokeStyle = 'rgba(125,211,252,0.09)';
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

    ctx.font = '10px ui-monospace, "SF Mono", Menlo, monospace';
    ctx.fillStyle = 'rgba(124,142,166,0.7)';
    ctx.fillText(`GRID  ${spacing.toFixed(2)}°   Z${zoom.toFixed(1)}`, 16, height - 16);
  };

  /**
   * 省界（W14-5）：位图缓存 + `drawImage`。
   *
   * 为什么必须缓存：35 个省几万个点，一次重绘（描边 + 填充）在 1280×800 / dpr2 下
   * 是几十毫秒级 —— 每帧画会让播放掉到个位数帧率。落在离屏画布上之后，
   * **视图不变就只 drawImage（<0.2ms）**，只有缩放/平移才重建。
   */
  const paintOutline = (proj: Projection, dpr: number): void => {
    if (!ctx || !provinces) return;
    const key = outlineCacheKey({
      width,
      height,
      cx: proj.cx,
      cy: proj.cy,
      scaleX: proj.scaleX,
      scaleY: proj.scaleY,
    });

    if (!outlineCache || outlineCache.key !== key || outlineCache.dpr !== dpr) {
      const started = typeof performance !== 'undefined' ? performance.now() : 0;
      const target = createOutlineSurface(width, height, dpr);
      if (!target) return;
      // 平面口径交给轮廓渲染器：GCJ 平面（播放页）不换算，WGS 平面（补点页）才换算一次
      renderChinaOutline(target.ctx, proj, provinces, {
        ...OUTLINE_STYLE,
        frame: options.coordinateSystem,
      });
      outlineCache = { key, dpr, canvas: target.canvas, ctx: target.ctx };
      if (import.meta.env.DEV) {
        const cost = (typeof performance !== 'undefined' ? performance.now() : 0) - started;
        console.debug(`[outline] rebuild ${width}x${height}@${dpr} ${cost.toFixed(2)}ms ${key}`);
      }
    }

    // 缓存位图是 dpr 倍大的像素尺寸；目标 ctx 已被 dpr 变换，所以按 CSS 尺寸贴回去即 1:1
    ctx.drawImage(outlineCache.canvas, 0, 0, width, height);
  };

  const schedule = (): void => {
    if (frame !== 0) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      paint();
    });
  };

  /** 视图变更统一走这里：合并到下一帧重绘（`schedule` 内部已有帧合并）。 */
  const queueViewChange = (): void => schedule();

  const measure = (): void => {
    const rect = container.getBoundingClientRect();
    width = Math.max(1, Math.round(rect.width));
    height = Math.max(1, Math.round(rect.height));
    schedule();
  };

  /**
   * 屏幕点 → **WGS84**（上层入库的坐标口径）。
   *
   * 两步走：先把屏幕点反算成"本平面坐标"（`screenPointToPlane`，纯函数、可单测），
   * 再过 `planeToWgs84` 换基准。补点页的平面本来就是 WGS84，第二步是恒等变换 ——
   * 这里不能无条件 `gcj02ToWgs84`，否则补点页会整体偏 500m。
   */
  const toLngLat = (clientX: number, clientY: number): { lng: number; lat: number } => {
    const rect = canvas.getBoundingClientRect();
    const plane = screenPointToPlane(projectionOf(), clientX - rect.left, clientY - rect.top);
    return planeToWgs84(options.coordinateSystem, plane.lng, plane.lat);
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
    // 拖动过就不算点击：否则每次平移都会给照片写一个错误的坐标
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
    queueViewChange();
  };

  if (wantsCanvas) {
    // 尺寸必须先量：`paint` 在 width/height 为 0 时直接返回（播放页也会走到这里）
    measure();
    window.addEventListener('resize', measure);
  }

  if (options.paintGrid) {
    // 交互只挂在"需要用户操作底图"的页面（补点页）；播放页的相机由主循环驱动
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
  }

  let destroyed = false;

  /**
   * 省界加载（W14-5）。失败什么都不做 —— 只是不进入 `outlineReady`，
   * 于是 `paint` 继续走格网分支，与阶段 13 行为逐字一致（D6 的失败降级）。
   */
  if (options.outline !== false) {
    void loadProvinces().then((collection) => {
      if (destroyed) return;
      if (!collection) {
        options.onOutlineChange?.(false);
        return;
      }
      provinces = collection;
      outlineReady = true;
      // 数据到了先重画一帧（尺寸可能还没量到，measure 之后还会再画一次）
      options.onOutlineChange?.(true);
      schedule();
    });
  } else {
    // 明确关掉时也要给一次回调：调用方据此知道"永远画格网"
    options.onOutlineChange?.(false);
  }

  return {
    kind: 'grid',
    coordinateSystem: options.coordinateSystem,
    whenReady(): Promise<void> {
      return Promise.resolve();
    },
    isOutlineReady(): boolean {
      return outlineReady;
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
    onClick(handler: GridClickHandler): void {
      clickHandler = handler;
    },
    destroy(): void {
      destroyed = true;
      outlineCache = null;
      if (frame !== 0) window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
      if (wantsCanvas) {
        canvas.removeEventListener('pointerdown', onPointerDown);
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('pointerup', onPointerUp);
        canvas.removeEventListener('wheel', onWheel);
        if (canvas.parentElement === container) container.removeChild(canvas);
      }
    },
  };
}
