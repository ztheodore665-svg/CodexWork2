/** 深色版的省份轮廓底图（W14-5）。
 *
 * ## 为什么要有它
 *
 * 阶段 13 的深色版只有一张自绘经纬格网：能看出"在动"，但看不出"在哪"。
 * 播放青藏环线时，一条发光轨迹飘在一张没有形状的格网上 —— 用户认不出那是青海湖。
 * 省级轮廓是**零网络**前提下能拿到的最强地理参照：一份 458KB 的本地 JSON，
 * 装下 35 个省级行政区，够把"这条线在哪"说清楚。
 *
 * ## 坐标系：DataV 是 GCJ-02，而本管线的投影平面**也是** GCJ-02 —— 所以不转换
 *
 * 取证（`F:/temp/china.json`，DataV `areas_v3/bound/100000_full.json`）：
 * 北京市 `properties.center = [116.405285, 39.904989]`。
 * 对照已知值：GCJ-02 北京中心 ≈ `[116.4074, 39.9042]`（差 0.002°），
 * WGS84 ≈ `[116.3913, 39.9075]`（差 0.015°）—— **数据是 GCJ-02**。
 *
 * 关键的一步是搞清楚"画到哪个平面上"：
 *  - `packages/trail/src/renderer.ts:83-84`：轨迹点先 `wgs84ToGcj02(lng,lat)` 再 `mercX/mercY`
 *    → **轨迹是 GCJ 墨卡托平面**；
 *  - `theme-dark/App.tsx`：`startGcj = wgs84ToGcj02(...)`，`analyticProjection(startGcj, …)`
 *    → **投影中心也是 GCJ**；
 *  - `analyticProjection` 里的 `mercX/mercY` 只是公式，**不带基准** —— 喂 GCJ 就是 GCJ 平面。
 *
 * 所以省界**必须直接 `mercX/mercY`，不许转**：转成 WGS84 会让省界相对轨迹整体偏
 * 约 500m（Z13 下 ≈26px），这正是"看起来边界浮在轨迹旁边"的真因，不是"内核 GCJ 边界要改"。
 *
 * 唯一的例外是**补点页**：那里 `gridMap` 的中心来自照片 EXIF 的 **WGS84** 坐标，
 * 平面是 WGS84 —— 所以 `frame: 'wgs84'` 时仍然要 `gcj02ToWgs84` 一次（见 `ChinaOutlineOptions.frame`）。
 * 转换函数一律用 `@travelmemory/geo` 的实现，不自己写偏移算法。
 *
 * ## 性能：三段式（转换一次 → 墨卡托一次 → 离屏位图）
 *
 * 1. **平面换算只做一次**：35 个 feature 约 3.5 万个点，逐帧换算不可接受。
 *    结果放进 `WeakMap<ProvinceCollection, Map<frame, PreparedFeature[]>>`；
 * 2. **落到墨卡托也只做一次**，渲染期只剩 `ox + (mx - cx) * scaleX` 这种线性运算；
 * 3. **离屏位图**由 `map/gridMap.ts` 持有：视图（缩放/平移）不变时只 `drawImage`，
 *    变了才重建。缓存键见 `outlineCacheKey`。
 *
 * // TODO(歧义): 市级边界按缩放按需加载（阶段15+）
 */

import { gcj02ToWgs84, mercX, mercY } from '@travelmemory/geo';
import type { Projection } from '@travelmemory/trail';

/** 本地数据地址（同源，不走任何外网）。 */
export const PROVINCES_URL = '/geo/china-provinces.json';

/** 精简后保留的坐标小数位。4 位 ≈ 11m，省界足够。 */
export const COORD_PRECISION = 4;

export interface ProvinceProperties {
  /** 6 位行政区码；缺失时为 null（不编造）。 */
  adcode: number | null;
  name: string;
}

export type ProvincePosition = [number, number];

export interface ProvincePolygonGeometry {
  type: 'Polygon';
  coordinates: ProvincePosition[][];
}

export interface ProvinceMultiPolygonGeometry {
  type: 'MultiPolygon';
  coordinates: ProvincePosition[][][];
}

export type ProvinceGeometry = ProvincePolygonGeometry | ProvinceMultiPolygonGeometry;

export interface ProvinceFeature {
  type: 'Feature';
  properties: ProvinceProperties;
  geometry: ProvinceGeometry;
}

export interface ProvinceCollection {
  type: 'FeatureCollection';
  features: ProvinceFeature[];
}

/**
 * 目标投影平面的坐标系。
 *
 * - `'gcj02'`（默认）：平面就是 GCJ 墨卡托 —— 播放页的轨迹与相机中心都是 GCJ，
 *   **不做任何换算**（换算反而会错 500m）；
 * - `'wgs84'`：平面是 WGS84 墨卡托 —— 补点页的相机中心来自照片 EXIF（WGS84），
 *   此时把 DataV 的 GCJ 坐标 `gcj02ToWgs84` 一次再投影。
 */
export type ProvinceFrame = 'gcj02' | 'wgs84';

/** 渲染参数。颜色由调用方给（`map/gridMap.ts` 用深色主题的四个值）。 */
export interface ChinaOutlineOptions {
  /** 普通边界颜色。 */
  color: string;
  /** 普通边界线宽（CSS px）。 */
  lineWidth: number;
  /** 省界（外环）颜色；缺省时退回 `color`。 */
  majorColor?: string;
  /** 填充色；不传就只描边。 */
  fillColor?: string;
  /** 目标平面坐标系，默认 `'gcj02'`（见 `ProvinceFrame` 与文件头）。 */
  frame?: ProvinceFrame;
}

/** 视图快照：决定离屏位图能否复用。 */
export interface OutlineView {
  width: number;
  height: number;
  cx: number;
  cy: number;
  scaleX: number;
  scaleY: number;
}

/** 屏幕外这个距离以内的省界仍然画，避免视口边缘"线突然断"。 */
const CULL_MARGIN = 96;

const round4 = (value: number): number => Math.round(value * 10 ** COORD_PRECISION) / 10 ** COORD_PRECISION;

/** 一串坐标 → 4 位小数 + 丢掉连续重复点（4 位小数会制造大量重复）。 */
function cleanRing(input: unknown): ProvincePosition[] {
  if (!Array.isArray(input)) return [];
  const out: ProvincePosition[] = [];
  let prevLng = Number.NaN;
  let prevLat = Number.NaN;
  for (const point of input) {
    if (!Array.isArray(point) || point.length < 2) continue;
    const lng = Number(point[0]);
    const lat = Number(point[1]);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
    const rl = round4(lng);
    const rt = round4(lat);
    if (rl === prevLng && rt === prevLat) continue;
    out.push([rl, rt]);
    prevLng = rl;
    prevLat = rt;
  }
  return out;
}

/** 一个多边形（可能带洞）→ 只保留 ≥4 点的环。 */
function cleanPolygon(input: unknown): ProvincePosition[][] {
  if (!Array.isArray(input)) return [];
  const rings: ProvincePosition[][] = [];
  for (const ring of input) {
    const cleaned = cleanRing(ring);
    // 少于 4 个点连不成一个面（GeoJSON 要求首尾闭合，至少 4 个）
    if (cleaned.length >= 4) rings.push(cleaned);
  }
  return rings;
}

/**
 * 把任意 JSON 裁成"只含 adcode / name + 4 位小数坐标"的集合。
 *
 * 三条不变量（单测钉住）：
 *  1. 属性只留 `adcode` / `name`；
 *  2. 坐标最多 4 位小数，且没有连续重复点；
 *  3. 任何非法输入（不是对象 / 没有 features / 几何坏了）→ **空集合**，不抛。
 *
 * 为什么加载后还要再裁一遍：磁盘上那份已经是裁好的，但**这份函数是唯一的入口**，
 * 所以别人手改、或换回 DataV 原始文件时，行为都一致（幂等）。
 */
export function stripProvinces(raw: unknown): ProvinceCollection {
  const empty: ProvinceCollection = { type: 'FeatureCollection', features: [] };
  if (!raw || typeof raw !== 'object') return empty;
  const features = (raw as { features?: unknown }).features;
  if (!Array.isArray(features)) return empty;

  const out: ProvinceFeature[] = [];
  for (const item of features) {
    if (!item || typeof item !== 'object') continue;
    const feature = item as {
      properties?: unknown;
      geometry?: { type?: unknown; coordinates?: unknown } | null;
    };
    const geometry = feature.geometry;
    if (!geometry || typeof geometry !== 'object') continue;

    const props = (feature.properties ?? {}) as Record<string, unknown>;
    const adcodeRaw = props.adcode;
    const adcode =
      typeof adcodeRaw === 'number' && Number.isFinite(adcodeRaw)
        ? adcodeRaw
        : typeof adcodeRaw === 'string' && adcodeRaw.trim() !== '' && Number.isFinite(Number(adcodeRaw))
          ? Number(adcodeRaw)
          : null;
    const name = typeof props.name === 'string' ? props.name : '';

    if (geometry.type === 'Polygon') {
      const rings = cleanPolygon(geometry.coordinates);
      if (rings.length === 0) continue;
      out.push({
        type: 'Feature',
        properties: { adcode, name },
        geometry: { type: 'Polygon', coordinates: rings },
      });
      continue;
    }
    if (geometry.type === 'MultiPolygon') {
      const polygons: ProvincePosition[][][] = [];
      if (Array.isArray(geometry.coordinates)) {
        for (const polygon of geometry.coordinates) {
          const rings = cleanPolygon(polygon);
          if (rings.length > 0) polygons.push(rings);
        }
      }
      if (polygons.length === 0) continue;
      out.push({
        type: 'Feature',
        properties: { adcode, name },
        geometry: { type: 'MultiPolygon', coordinates: polygons },
      });
    }
    // 其它几何类型（LineString / Point / GeometryCollection）一律丢掉：省界只有面
  }

  return { type: 'FeatureCollection', features: out };
}

/**
 * 取本地省界数据。**永不 reject**：网络/解析/结构任何问题都返回 `null`，
 * 调用方据此退回格网（`gridMap` 的既有行为）。
 */
export async function loadProvinces(
  url: string = PROVINCES_URL,
  fetchImpl: typeof fetch | undefined = typeof fetch === 'function' ? fetch : undefined,
): Promise<ProvinceCollection | null> {
  if (typeof fetchImpl !== 'function') return null;
  try {
    const response = await fetchImpl(url, { credentials: 'same-origin' });
    if (!response.ok) return null;
    const raw: unknown = await response.json();
    const collection = stripProvinces(raw);
    return collection.features.length > 0 ? collection : null;
  } catch {
    return null;
  }
}

/** 一个环在墨卡托空间里的预处理结果（含 bbox，用于视口裁剪）。 */
interface PreparedRing {
  /** 交错存放 `[mx, my, mx, my, …]`。 */
  xy: Float64Array;
  minMx: number;
  maxMx: number;
  minMy: number;
  maxMy: number;
}

interface PreparedFeature {
  name: string;
  /** `[多边形][环]`。多边形 0 号环是外轮廓，其余是洞。 */
  polygons: PreparedRing[][];
}

/**
 * DataV 坐标 → 墨卡托的预处理缓存（按「集合 × 平面」两级）。
 *
 * 用 `WeakMap` 而不是 Map：集合对象一旦被调用方丢掉（比如重挂载换了新对象），
 * 缓存跟着回收，不会把几十万个 double 永久留在内存里。
 * 内层按 `frame` 分桶：同一个集合可能同时被"GCJ 平面"（播放页）与"WGS 平面"（补点页）用。
 */
const preparedCache = new WeakMap<ProvinceCollection, Map<ProvinceFrame, PreparedFeature[]>>();

function prepare(collection: ProvinceCollection, frame: ProvinceFrame): PreparedFeature[] {
  let byFrame = preparedCache.get(collection);
  if (!byFrame) {
    byFrame = new Map<ProvinceFrame, PreparedFeature[]>();
    preparedCache.set(collection, byFrame);
  }
  const cached = byFrame.get(frame);
  if (cached) return cached;

  // 平面换算：GCJ 平面直接用原坐标（DataV 就是 GCJ，转了才错）；
  // WGS 平面（补点页）才需要一次 gcj02ToWgs84。
  const toPlane =
    frame === 'gcj02'
      ? (lng: number, lat: number): { lng: number; lat: number } => ({ lng, lat })
      : (lng: number, lat: number): { lng: number; lat: number } => gcj02ToWgs84(lng, lat);

  const prepared: PreparedFeature[] = [];
  for (const feature of collection.features) {
    const rawPolygons: ProvincePosition[][][] =
      feature.geometry.type === 'Polygon'
        ? [feature.geometry.coordinates]
        : feature.geometry.coordinates;

    const polygons: PreparedRing[][] = [];
    for (const polygon of rawPolygons) {
      const rings: PreparedRing[] = [];
      for (const ring of polygon) {
        if (ring.length < 4) continue;
        const xy = new Float64Array(ring.length * 2);
        let minMx = Number.POSITIVE_INFINITY;
        let maxMx = Number.NEGATIVE_INFINITY;
        let minMy = Number.POSITIVE_INFINITY;
        let maxMy = Number.NEGATIVE_INFINITY;
        for (let index = 0; index < ring.length; index += 1) {
          const plane = toPlane(ring[index][0], ring[index][1]);
          const mx = mercX(plane.lng);
          const my = mercY(plane.lat);
          xy[index * 2] = mx;
          xy[index * 2 + 1] = my;
          if (mx < minMx) minMx = mx;
          if (mx > maxMx) maxMx = mx;
          if (my < minMy) minMy = my;
          if (my > maxMy) maxMy = my;
        }
        rings.push({ xy, minMx, maxMx, minMy, maxMy });
      }
      if (rings.length > 0) polygons.push(rings);
    }
    if (polygons.length > 0) prepared.push({ name: feature.properties.name, polygons });
  }

  byFrame.set(frame, prepared);
  return prepared;
}

/**
 * 离屏位图的缓存键：同一把尺子 + 同一个中心 → 同一张位图。
 *
 * 为什么要**量化**：`cx/cy` 是浮点数，一次 `0.1 + 0.2` 级别的误差就会让键变，
 * 于是"视图没变也重建"，缓存等于没有。这里把中心量化到 **1 屏幕像素**、
 * 把缩放量化到 1/1000 —— 亚像素的抖动不该触发一次几十毫秒的重建。
 */
export function outlineCacheKey(view: OutlineView): string {
  const w = Math.max(1, Math.round(view.width));
  const h = Math.max(1, Math.round(view.height));
  const cxPx = Math.round(view.cx * view.scaleX);
  const cyPx = Math.round(view.cy * view.scaleY);
  const sx = Math.round(view.scaleX * 1000);
  const sy = Math.round(view.scaleY * 1000);
  return `${w}x${h}|${cxPx},${cyPx}|${sx},${sy}`;
}

/**
 * 把省界画到（已按 dpr 变换过的）2D 上下文上。**纯绘制**：不清屏、不设 transform、
 * 不改全局状态（除了颜色/线宽/path，且都在 save/restore 内）。
 *
 * @param ctx 目标上下文（通常是离屏画布）
 * @param projection 与轨迹同一把尺子（`analyticProjection` 的产物）
 */
export function renderChinaOutline(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  projection: Projection,
  collection: ProvinceCollection,
  options: ChinaOutlineOptions,
): void {
  const prepared = prepare(collection, options.frame ?? 'gcj02');
  if (prepared.length === 0) return;

  const width = projection.ox * 2;
  const height = projection.oy * 2;
  const scaleX = projection.scaleX;
  const scaleY = projection.scaleY;
  const cx = projection.cx;
  const cy = projection.cy;
  const ox = projection.ox;
  const oy = projection.oy;

  const majorColor = options.majorColor ?? options.color;

  ctx.save();
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';

  for (const feature of prepared) {
    for (const rings of feature.polygons) {
      // 先用外环 bbox 做一次粗裁剪：视口外的省整个跳过（缩放后只有几个省在屏幕上）
      const outer = rings[0];
      const x0 = ox + (outer.minMx - cx) * scaleX;
      const x1 = ox + (outer.maxMx - cx) * scaleX;
      const y0 = oy + (outer.minMy - cy) * scaleY;
      const y1 = oy + (outer.maxMy - cy) * scaleY;
      if (
        x1 < -CULL_MARGIN ||
        x0 > width + CULL_MARGIN ||
        y1 < -CULL_MARGIN ||
        y0 > height + CULL_MARGIN
      ) {
        continue;
      }

      // 填充：外环 + 洞一次成环，用 evenodd 让洞真的是洞
      if (options.fillColor) {
        ctx.beginPath();
        for (const ring of rings) {
          const xy = ring.xy;
          const total = xy.length / 2;
          if (total < 4) continue;
          ctx.moveTo(ox + (xy[0] - cx) * scaleX, oy + (xy[1] - cy) * scaleY);
          for (let index = 1; index < total; index += 1) {
            ctx.lineTo(ox + (xy[index * 2] - cx) * scaleX, oy + (xy[index * 2 + 1] - cy) * scaleY);
          }
          ctx.closePath();
        }
        ctx.fillStyle = options.fillColor;
        ctx.fill('evenodd');
      }

      // 描边：0 号环是省界（更亮更粗），其余是洞/飞地（普通口径）
      for (let ringIndex = 0; ringIndex < rings.length; ringIndex += 1) {
        const ring = rings[ringIndex];
        const xy = ring.xy;
        const total = xy.length / 2;
        if (total < 4) continue;
        ctx.beginPath();
        ctx.moveTo(ox + (xy[0] - cx) * scaleX, oy + (xy[1] - cy) * scaleY);
        for (let index = 1; index < total; index += 1) {
          ctx.lineTo(ox + (xy[index * 2] - cx) * scaleX, oy + (xy[index * 2 + 1] - cy) * scaleY);
        }
        ctx.closePath();
        const major = ringIndex === 0;
        ctx.strokeStyle = major ? majorColor : options.color;
        ctx.lineWidth = major ? options.lineWidth + 0.5 : options.lineWidth;
        ctx.stroke();
      }
    }
  }

  ctx.restore();
}
