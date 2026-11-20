/**
 * Canvas 2D 轨迹渲染器。
 *
 * 性能红线（每一条都对应一个真实掉帧原因）：
 *  - 发光用三遍描边，不用 shadowBlur（shadowBlur 每帧几十次高斯模糊，必掉帧）；
 *  - 渐变 / pattern 全部缓存，光晕用 translate 复用同一个渐变，不每帧创建；
 *  - 轨迹点存在 Float32Array 里，绘制时直接循环算屏幕坐标，不生成对象数组；
 *  - 屏幕外 200px 之外的点不画。
 */

import type { Journey, JourneySegment } from './types';
import { invMercX, invMercY, mercX, mercY, wgs84ToGcj02 } from '@travelmemory/geo';
import { projectX, projectY, type Projection } from './projection';
import { DEFAULT_TRANSPORT, TRANSPORT_STYLE } from './transport';

const TAU = Math.PI * 2;
const CULL_MARGIN = 200;

/** 照片卡片半宽 / 半高（与 CSS 里 .card 的尺寸一致） */
const CARD_HALF_W = 66;
const CARD_HALF_H = 84;

/** 卡片锚点数组的步长：ax, ay, cx, cy, visible */
export const ANCHOR_STRIDE = 5;

export interface SceneLabel {
  mx: number;
  my: number;
  text: string;
}

/** 渲染用的预计算数据（一次构建，永不重建） */
export interface Scene {
  /** GCJ-02 墨卡托坐标（与高德底图一致） */
  trackMX: Float32Array;
  trackMY: Float32Array;
  trackS: Float32Array;
  trackT: Float32Array;
  count: number;
  photoMX: Float32Array;
  photoMY: Float32Array;
  photoS: Float32Array;
  photoT: Float32Array;
  photoCount: number;
  totalMeters: number;
  /** 路段（交通工具），与 journey.segments 同一份引用，不复制 */
  segments: JourneySegment[];
  labels: SceneLabel[];
}

export interface DrawState {
  scene: Scene;
  proj: Projection;
  /** 已走过的里程（米） */
  progressS: number;
  nowMs: number;
  fallback: boolean;
  /** 降级底图的经纬网间距（度） */
  gridSpacingDeg: number;
  /** 光点屏幕锚点（GCJ 墨卡托） */
  headMX: number;
  headMY: number;
  /** 卡片锚点平面数组，见 ANCHOR_STRIDE */
  anchors: Float32Array;
  anchorCount: number;
}

/**
 * 把 Journey 转成渲染用的 GCJ 墨卡托数组。
 * 需求要求内部全程 WGS84、只在渲染前转 GCJ02——这里就是那道边界。
 */
export function prepareScene(journey: Journey): Scene {
  const n = journey.track.length;
  const trackMX = new Float32Array(n);
  const trackMY = new Float32Array(n);
  const trackS = new Float32Array(n);
  const trackT = new Float32Array(n);

  for (let i = 0; i < n; i += 1) {
    const sample = journey.track[i];
    const lng = invMercX(sample.mx);
    const lat = invMercY(sample.my);
    const gcj = wgs84ToGcj02(lng, lat);
    trackMX[i] = mercX(gcj.lng);
    trackMY[i] = mercY(gcj.lat);
    trackS[i] = sample.s;
    trackT[i] = sample.t;
  }

  const photoCount = journey.photos.length;
  const photoMX = new Float32Array(photoCount);
  const photoMY = new Float32Array(photoCount);
  const photoS = new Float32Array(photoCount);
  const photoT = new Float32Array(photoCount);

  const labelMap = new Map<string, SceneLabel>();

  for (let i = 0; i < photoCount; i += 1) {
    const photo = journey.photos[i];
    const lat = photo.lat ?? 0;
    const lng = photo.lng ?? 0;
    const gcj = wgs84ToGcj02(lng, lat);
    const mx = mercX(gcj.lng);
    const my = mercY(gcj.lat);
    photoMX[i] = mx;
    photoMY[i] = my;
    photoT[i] = photo.takenAt;
    photoS[i] = nearestS(journey, photo.lat ?? 0, photo.lng ?? 0);
    if (photo.placeName && !labelMap.has(photo.placeName) && labelMap.size < 14) {
      labelMap.set(photo.placeName, { mx, my, text: photo.placeName });
    }
  }

  return {
    trackMX,
    trackMY,
    trackS,
    trackT,
    count: n,
    photoMX,
    photoMY,
    photoS,
    photoT,
    photoCount,
    totalMeters: journey.totalMeters,
    segments: journey.segments,
    labels: Array.from(labelMap.values()),
  };
}

/** 取照片在轨迹上的弧长位置（按墨卡托最近点） */
function nearestS(journey: Journey, lat: number, lng: number): number {
  const track = journey.track;
  if (track.length === 0) return 0;
  const px = mercX(lng);
  const py = mercY(lat);
  let bestS = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let i = 0; i < track.length; i += 1) {
    const dx = track[i].mx - px;
    const dy = track[i].my - py;
    const distance = dx * dx + dy * dy;
    if (distance < bestDistance) {
      bestDistance = distance;
      bestS = track[i].s;
    }
  }
  return bestS;
}

export class Renderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  width = 0;
  height = 0;
  dpr = 1;

  private headGlow: CanvasGradient | null = null;
  private vignette: CanvasGradient | null = null;
  private gridBackground: CanvasGradient | null = null;
  private scanPattern: CanvasPattern | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) throw new Error('Canvas 2D context unavailable');
    this.ctx = ctx;
    this.resize();
  }

  /**
   * 按 CSS 尺寸 × dpr 重设画布；尺寸变化时重建与尺寸相关的缓存。
   *
   * ⚠️ 必须量**父容器**，不能量 canvas 自己：canvas 是替换元素，绝对定位 + width:auto 时
   * 按固有尺寸（默认 300×150）解析，而固有尺寸又被我们写回的 canvas.width 属性反向决定，
   * 量自己会自锁在 300×150 —— 曾经导致轨迹/光点/网格全挤在左上角一小块。
   */
  resize(): boolean {
    const host = this.canvas.parentElement ?? this.canvas;
    const rect = host.getBoundingClientRect();
    // dpr 上限 1.5：全屏画布在 dpr=2 时是 2880×1800，每帧三遍描边 + 网格 + 暗角代价太高，
    // 实测帧率在 36~54fps 之间波动。1.5 省 44% 像素，肉眼几乎无差。
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const width = Math.max(1, Math.round(rect.width || window.innerWidth));
    const height = Math.max(1, Math.round(rect.height || window.innerHeight));
    if (width === this.width && height === this.height && dpr === this.dpr) return false;

    this.width = width;
    this.height = height;
    this.dpr = dpr;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.vignette = null;
    this.gridBackground = null;

    // 开发期护栏：画布宽度应当等于视口宽度（差 >4px 说明布局又出问题了）
    if (import.meta.env.DEV && Math.abs(width - window.innerWidth) > 4) {
      console.warn('[renderer] canvas width mismatch', width, window.innerWidth);
    }
    return true;
  }

  private ensureHeadGlow(): CanvasGradient {
    if (!this.headGlow) {
      const g = this.ctx.createRadialGradient(0, 0, 0, 0, 0, 26);
      g.addColorStop(0, 'rgba(34,211,238,0.38)');
      g.addColorStop(0.5, 'rgba(34,211,238,0.14)');
      g.addColorStop(1, 'rgba(34,211,238,0)');
      this.headGlow = g;
    }
    return this.headGlow;
  }

  private ensureVignette(): CanvasGradient {
    if (!this.vignette) {
      const cx = this.width / 2;
      const cy = this.height / 2;
      const outer = Math.hypot(cx, cy);
      const g = this.ctx.createRadialGradient(cx, cy, outer * 0.55, cx, cy, outer * 1.02);
      g.addColorStop(0, 'rgba(3,6,12,0)');
      g.addColorStop(1, 'rgba(3,6,12,0.55)');
      this.vignette = g;
    }
    return this.vignette;
  }

  private ensureScanPattern(): CanvasPattern | null {
    if (this.scanPattern) return this.scanPattern;
    const tile = document.createElement('canvas');
    tile.width = 1;
    tile.height = 4;
    const tctx = tile.getContext('2d');
    if (!tctx) return null;
    tctx.fillStyle = 'rgba(255,255,255,0.5)';
    tctx.fillRect(0, 0, 1, 1);
    const pattern = this.ctx.createPattern(tile, 'repeat');
    this.scanPattern = pattern;
    return pattern;
  }

  draw(state: DrawState): void {
    const ctx = this.ctx;
    const w = this.width;
    const h = this.height;
    if (w <= 0 || h <= 0) return;

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const proj = state.proj;
    const scaleX = proj.scaleX;
    const scaleY = proj.scaleY;
    const ox = proj.ox;
    const oy = proj.oy;
    const pcx = proj.cx;
    const pcy = proj.cy;

    if (state.fallback) this.drawGrid(state);

    const scene = state.scene;
    const n = scene.count;
    const trackMX = scene.trackMX;
    const trackMY = scene.trackMY;
    const trackS = scene.trackS;

    const headX = ox + (state.headMX - pcx) * scaleX;
    const headY = oy + (state.headMY - pcy) * scaleY;

    // ── 已走过的索引上界（二分） ────────────────────────────────
    let lo = 0;
    let hi = n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (trackS[mid] <= state.progressS) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    const traveledEnd = lo; // [0, traveledEnd) 已走过

    // ── 1. 未走过的轨迹：按路段的交通工具分段描边 ───────────────
    //
    // 每段有自己的颜色与线型（自驾实线 / 火车长虚线 / 飞机点线 / 步行细点线）。
    // 段边界处要先把上一个点补进新路径，否则每跨一段就会出现一个采样间隔的缺口。
    const segments = scene.segments;
    const segAt = (s: number): number => {
      // 段是首尾相接且有序的，线性扫描足够（一般 10~30 段）
      let k = 0;
      while (k < segments.length - 1 && s >= segments[k].endS) k += 1;
      return k;
    };

    if (n > 1) {
      let segIdx = segAt(traveledEnd < n ? trackS[traveledEnd] : 0);
      let mode = segments.length > 0 ? segments[segIdx].mode : DEFAULT_TRANSPORT;

      let rest = new Path2D();
      rest.moveTo(headX, headY);
      let drawing = true;
      let prevX = headX;
      let prevY = headY;

      const flushRest = (): void => {
        if (!drawing && prevX === headX && prevY === headY) return;
        const style = TRANSPORT_STYLE[mode];
        ctx.setLineDash(style.dash);
        ctx.lineWidth = style.width;
        ctx.strokeStyle = style.colorDim;
        ctx.stroke(rest);
      };

      ctx.save();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      for (let i = traveledEnd; i < n; i += 1) {
        while (segIdx < segments.length - 1 && trackS[i] >= segments[segIdx].endS) {
          flushRest();
          segIdx += 1;
          mode = segments[segIdx].mode;
          rest = new Path2D();
          rest.moveTo(prevX, prevY); // 接上，避免段边界缺口
          drawing = true;
        }

        const x = ox + (trackMX[i] - pcx) * scaleX;
        const y = oy + (trackMY[i] - pcy) * scaleY;
        if (x < -CULL_MARGIN || x > w + CULL_MARGIN || y < -CULL_MARGIN || y > h + CULL_MARGIN) {
          drawing = false;
          continue;
        }
        if (!drawing) {
          rest.moveTo(x, y);
          drawing = true;
        } else {
          rest.lineTo(x, y);
        }
        prevX = x;
        prevY = y;
      }
      flushRest();
      ctx.restore();

      // ── 2. 已走过的轨迹：三遍描边做发光 ───────────────────────
      if (traveledEnd > 0) {
        const walked = new Path2D();
        walked.moveTo(
          ox + (trackMX[0] - pcx) * scaleX,
          oy + (trackMY[0] - pcy) * scaleY,
        );
        for (let i = 1; i < traveledEnd; i += 1) {
          walked.lineTo(ox + (trackMX[i] - pcx) * scaleX, oy + (trackMY[i] - pcy) * scaleY);
        }
        walked.lineTo(headX, headY);

        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.setLineDash([]);
        ctx.strokeStyle = 'rgba(34,211,238,0.10)';
        ctx.lineWidth = 10;
        ctx.stroke(walked);
        ctx.strokeStyle = 'rgba(34,211,238,0.35)';
        ctx.lineWidth = 4;
        ctx.stroke(walked);
        ctx.strokeStyle = '#7dd3fc';
        ctx.lineWidth = 1.6;
        ctx.stroke(walked);
        ctx.restore();

        // ── 2.5 走过部分的交通工具色芯线 ──────────────────────
        // 底色仍是统一青色发光（那是这个应用的识别色），在上面叠一层细的
        // 交通工具色，走过之后回看整条线，哪段坐的什么一目了然。
        if (segments.length > 1) {
          ctx.save();
          ctx.setLineDash([]);
          ctx.lineCap = 'round';
          ctx.lineJoin = 'round';
          ctx.globalAlpha = 0.9;
          const end = Math.min(traveledEnd, n - 1);
          let k = segAt(0);
          let startIdx = 0;
          while (startIdx <= end) {
            const segEnd = k < segments.length - 1 ? segments[k].endS : Number.POSITIVE_INFINITY;
            let stop = startIdx;
            while (stop <= end && trackS[stop] < segEnd) stop += 1;
            if (stop > startIdx + 1) {
              ctx.beginPath();
              ctx.moveTo(
                ox + (trackMX[startIdx] - pcx) * scaleX,
                oy + (trackMY[startIdx] - pcy) * scaleY,
              );
              for (let i = startIdx + 1; i <= stop && i <= end; i += 1) {
                ctx.lineTo(ox + (trackMX[i] - pcx) * scaleX, oy + (trackMY[i] - pcy) * scaleY);
              }
              ctx.strokeStyle = TRANSPORT_STYLE[segments[k].mode].color;
              ctx.lineWidth = 1.2;
              ctx.stroke();
            }
            startIdx = Math.max(stop, startIdx + 1);
            if (k < segments.length - 1) k += 1;
            else break;
          }
          ctx.restore();
          ctx.globalAlpha = 1;
        }
      }
    }

    // ── 3. 站点小圆点 ──────────────────────────────────────────
    const photoCount = scene.photoCount;
    if (photoCount > 0) {
      const photoMX = scene.photoMX;
      const photoMY = scene.photoMY;
      const photoS = scene.photoS;
      ctx.save();
      // 未经过的
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      for (let i = 0; i < photoCount; i += 1) {
        if (photoS[i] <= state.progressS) continue;
        const x = ox + (photoMX[i] - pcx) * scaleX;
        const y = oy + (photoMY[i] - pcy) * scaleY;
        if (x < -CULL_MARGIN || x > w + CULL_MARGIN || y < -CULL_MARGIN || y > h + CULL_MARGIN) continue;
        ctx.beginPath();
        ctx.arc(x, y, 2.2, 0, TAU);
        ctx.fill();
      }
      // 已经过的
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      for (let i = 0; i < photoCount; i += 1) {
        if (photoS[i] > state.progressS) continue;
        const x = ox + (photoMX[i] - pcx) * scaleX;
        const y = oy + (photoMY[i] - pcy) * scaleY;
        if (x < -CULL_MARGIN || x > w + CULL_MARGIN || y < -CULL_MARGIN || y > h + CULL_MARGIN) continue;
        ctx.beginPath();
        ctx.arc(x, y, 2.2, 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    }

    // ── 4. 照片卡片引线 ────────────────────────────────────────
    this.drawLeaders(state);

    // ── 5. 光点 ────────────────────────────────────────────────
    this.drawHead(state.nowMs, headX, headY);

    // ── 6. 暗角 + 扫描线 ───────────────────────────────────────
    ctx.save();
    ctx.fillStyle = this.ensureVignette();
    ctx.fillRect(0, 0, w, h);
    const pattern = this.ensureScanPattern();
    if (pattern) {
      ctx.globalAlpha = 0.03;
      ctx.fillStyle = pattern;
      ctx.fillRect(0, 0, w, h);
    }
    ctx.restore();
  }

  private drawHead(nowMs: number, x: number, y: number): void {
    const ctx = this.ctx;
    const glow = this.ensureHeadGlow();

    ctx.save();
    ctx.translate(x, y);

    // 外圈渐变光晕
    ctx.beginPath();
    ctx.arc(0, 0, 26, 0, TAU);
    ctx.fillStyle = glow;
    ctx.fill();

    // 两圈扩散涟漪
    for (let ring = 0; ring < 2; ring += 1) {
      const phase = ((nowMs * 0.001 + ring * 0.8) % 1.6) / 1.6;
      const radius = 6 + phase * 34;
      const alpha = (1 - phase) * 0.45;
      ctx.beginPath();
      ctx.arc(0, 0, radius, 0, TAU);
      ctx.strokeStyle = `rgba(34,211,238,${alpha.toFixed(3)})`;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }

    // 实心白核 + 青色描边
    ctx.beginPath();
    ctx.arc(0, 0, 3.5, 0, TAU);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(34,211,238,0.9)';
    ctx.stroke();

    ctx.restore();
  }

  private drawLeaders(state: DrawState): void {
    const anchors = state.anchors;
    const limit = Math.min(state.anchorCount, Math.floor(anchors.length / ANCHOR_STRIDE));
    if (limit <= 0) return;
    const ctx = this.ctx;

    ctx.save();
    ctx.strokeStyle = 'rgba(125,211,252,0.28)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < limit; i += 1) {
      const base = i * ANCHOR_STRIDE;
      if (anchors[base + 4] < 0.5) continue;
      const ax = anchors[base];
      const ay = anchors[base + 1];
      const cx = anchors[base + 2];
      const cy = anchors[base + 3];

      const dx = ax - cx;
      const dy = ay - cy;
      if (dx === 0 && dy === 0) continue;

      // 线段在卡片矩形边界处收住
      let t = 1;
      if (dx !== 0) t = Math.min(t, CARD_HALF_W / Math.abs(dx));
      if (dy !== 0) t = Math.min(t, CARD_HALF_H / Math.abs(dy));
      ctx.moveTo(ax, ay);
      ctx.lineTo(cx + dx * t, cy + dy * t);
    }
    ctx.stroke();

    ctx.fillStyle = 'rgba(125,211,252,0.5)';
    for (let i = 0; i < limit; i += 1) {
      const base = i * ANCHOR_STRIDE;
      if (anchors[base + 4] < 0.5) continue;
      ctx.beginPath();
      ctx.arc(anchors[base], anchors[base + 1], 2, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  /** 降级底图：深色底 + 墨卡托对齐经纬网 + 地名 */
  private drawGrid(state: DrawState): void {
    const ctx = this.ctx;
    const w = this.width;
    const h = this.height;

    if (!this.gridBackground) {
      const g = ctx.createRadialGradient(w * 0.5, h * 0.42, 0, w * 0.5, h * 0.42, Math.hypot(w, h) * 0.62);
      g.addColorStop(0, '#0a1523');
      g.addColorStop(0.55, '#070c16');
      g.addColorStop(1, '#04070d');
      this.gridBackground = g;
    }
    ctx.save();
    ctx.fillStyle = this.gridBackground;
    ctx.fillRect(0, 0, w, h);

    const proj = state.proj;
    const spacing = state.gridSpacingDeg > 0 ? state.gridSpacingDeg : 0.25;

    // 屏幕四角反算经纬度范围
    const minLng = invMercX(proj.cx + (0 - proj.ox) / proj.scaleX);
    const maxLng = invMercX(proj.cx + (w - proj.ox) / proj.scaleX);
    const maxLat = invMercY(proj.cy + (0 - proj.oy) / proj.scaleY);
    const minLat = invMercY(proj.cy + (h - proj.oy) / proj.scaleY);

    ctx.strokeStyle = 'rgba(125,211,252,0.075)';
    ctx.lineWidth = 1;
    ctx.beginPath();

    const startLng = Math.floor(minLng / spacing) * spacing;
    for (let lng = startLng; lng <= maxLng; lng += spacing) {
      const x = Math.round(proj.ox + (mercX(lng) - proj.cx) * proj.scaleX) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
    }

    const startLat = Math.floor(minLat / spacing) * spacing;
    for (let lat = startLat; lat <= maxLat; lat += spacing) {
      const y = Math.round(proj.oy + (mercY(lat) - proj.cy) * proj.scaleY) + 0.5;
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
    }
    ctx.stroke();

    // 地名标签
    const labels = state.scene.labels;
    if (labels.length > 0) {
      ctx.font =
        '11px -apple-system, "Segoe UI", "Noto Sans SC", system-ui, sans-serif';
      ctx.fillStyle = 'rgba(125,211,252,0.45)';
      ctx.textBaseline = 'middle';
      for (let i = 0; i < labels.length; i += 1) {
        const x = projectX(proj, labels[i].mx);
        const y = projectY(proj, labels[i].my);
        if (x < -100 || x > w + 100 || y < -60 || y > h + 60) continue;
        ctx.fillText(labels[i].text, x + 8, y - 8);
      }
    }
    ctx.restore();
  }
}
