/** 播放页（Play，§7.1.1 / §7.1.2 的主循环）。
 *
 * ## 这个文件是整个应用最需要"照抄"的地方
 *
 * 主循环写法来自 `showcase/src/App.tsx` 的 `PlayStage`（阶段 9 实测结论）：
 *
 * - `q` **不进 React state**：每帧变化的量只活在 `Clock` 里，只有 `playing`
 *   变化时才同步一次 React，否则 60fps × setState 会把主线程吃掉；
 * - 相机用 `Camera.follow` 做阻尼跟随，倍速时按 `DEFAULT_TAU / max(1, speed)` 压滞后；
 * - `q` → 弧长 `s` **不是线性**的（W12-1）：`qToDistance` 按「两个景点之间有多远」
 *   给每一段分配 0.5/1/2 秒，于是短段不再一眨眼、长段不再压缩。进度条 / `seek` /
 *   倍速按钮仍然全部基于 `q`，只有「取样」这一步被改写（改成基于 `s` 手感会跳）；
 * - 内部全程 **WGS84**，只在渲染/下发前 `wgs84ToGcj02`，这是"轨迹与高德底图对齐"的唯一办法；
 * - `setCenter` 限流到 25Hz（`CENTER_INTERVAL_MS = 40`）：每帧下发会让高德不断
 *   取消在飞的瓦片请求（实测 6 秒 293 次 ERR_ABORTED），底图永远画不出来；
 * - 覆盖层锚定"相机当前值"而不是"地图最后一次被下发的中心"，否则整条轨迹会以 25Hz 抖动；
 * - 降级格网只在**底图揭幕之后**才画（`fallback && started`），否则揭幕前会闪一屏经纬网。
 *
 * 这些不是风格偏好，每一条都对应一个真实掉帧/错位 bug。改动前先读这句话。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Camera,
  Clock,
  DEFAULT_TAU,
  Renderer,
  TRANSPORT_STYLE,
  analyticProjection,
  createPose,
  prepareScene,
  samplePose,
  setCamera,
  type Journey,
  type Projection,
  type Scene,
} from '@travelmemory/trail';
import { mercX, mercY, wgs84ToGcj02 } from '@travelmemory/geo';

import { initMap, type MapController } from '../map/controller';
import { computeSegmentDurations, qToDistance, totalDurationSeconds } from '../../core/speedCurve';
import { canRecord, recordCanvas } from '../../core/videoRecorder';

/** 下发地图中心的最小间隔（毫秒）——见文件头第 4 条。 */
const CENTER_INTERVAL_MS = 40;

/* ── 浅色播放页的轨迹配色（阶段 14 / W14-2） ─────────────────────
 *
 * 为什么不用 `packages/trail` 的 `Renderer`：那份渲染器是**暗场**设计 ——
 * 轨迹是 `#7dd3fc` 青色 + 三遍描边发光 + 全屏暗角 + 扫描线（见内核
 * `renderer.ts` 的 `drawHead` / `ensureVignette` / `ensureScanPattern`），
 * 颜色写死在核心里，本阶段不允许改内核。于是浅色版在这里自绘：
 *
 * | 元素 | 颜色 | 说明 |
 * | --- | --- | --- |
 * | 未走轨迹 | `#0066cc` | 深青，白底上对比度 ≈ 5.6:1 |
 * | 已走轨迹 | `#0099ff` | 亮一档 + 线宽 +0.8px，"走过"这件事靠亮度和粗细区分 |
 * | 光点 | `#0066cc` | **实心、无发光无阴影**（发光在浅底上只会糊成一团） |
 * | 站点圆点 | 同色，未走 35% 透明度 | |
 *
 * 不画：暗角、扫描线、降级格网。前三样是暗场效果；格网交给底图层
 * （`initMap({ paintFallback: true })`），否则两层网格会摩尔纹。
 */
const LIGHT_TRACK_PENDING = '#0066cc';
const LIGHT_TRACK_WALKED = '#0099ff';
/** 已走段的线宽增量（px）：`TRANSPORT_STYLE[mode].width + 这个值`。 */
const LIGHT_TRACK_WALKED_EXTRA_WIDTH = 0.8;
/** 屏幕外这个距离以内的点仍然画，避免视口边缘出现"线突然断"（同内核 `CULL_MARGIN`）。 */
const CULL_MARGIN = 200;
/** 光点半径（px）。内核是 3.5 + 26px 光晕，浅色版只留实心核。 */
const HEAD_RADIUS = 4.5;
/** 站点圆点半径（px）。 */
const STOP_RADIUS = 2.4;

/** 一帧要画的全部输入。字段刻意与内核 `DrawState` 同名，便于对照阅读。 */
export interface LightFrame {
  scene: Scene;
  proj: Projection;
  /** 已走过的里程（米） */
  progressS: number;
  /** 光点屏幕锚点（GCJ 墨卡托） */
  headMX: number;
  headMY: number;
  /** CSS 像素宽高（不是画布像素） */
  width: number;
  height: number;
  /** devicePixelRatio（已按内核口径封顶 1.5） */
  dpr: number;
}

/** 按路段的交通工具取虚线与线宽（只借线型，颜色一律用浅色口径）。 */
function dashOf(mode: keyof typeof TRANSPORT_STYLE): number[] {
  return TRANSPORT_STYLE[mode].dash;
}

/** 二分：返回 `[0, traveledEnd)` 区间内的轨迹下标都已走过。 */
export function traveledEndIndex(trackS: Float32Array, count: number, progressS: number): number {
  let lo = 0;
  let hi = count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (trackS[mid] <= progressS) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * 把一帧画到 2D 上下文上（纯函数：只读 `frame`，只写 `ctx`）。
 *
 * 导出它是为了能在单测里用假 ctx 断言"走过/未走用不同颜色"，
 * 不必起 jsdom canvas。
 */
export function drawLightFrame(ctx: CanvasRenderingContext2D, frame: LightFrame): void {
  const { scene, proj, width, height, dpr } = frame;
  if (width <= 0 || height <= 0) return;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const scaleX = proj.scaleX;
  const scaleY = proj.scaleY;
  const ox = proj.ox;
  const oy = proj.oy;
  const pcx = proj.cx;
  const pcy = proj.cy;
  const n = scene.count;

  const strokePolyline = (points: number[], dash: number[], lineWidth: number, color: string): void => {
    if (points.length < 4) return;
    ctx.beginPath();
    ctx.moveTo(points[0], points[1]);
    for (let i = 2; i < points.length; i += 2) ctx.lineTo(points[i], points[i + 1]);
    ctx.setLineDash(dash);
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = color;
    ctx.stroke();
  };

  const segments = scene.segments;
  const segAt = (s: number): number => {
    let k = 0;
    while (k < segments.length - 1 && s >= segments[k].endS) k += 1;
    return k;
  };

  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  if (n > 1) {
    const trackMX = scene.trackMX;
    const trackMY = scene.trackMY;
    const trackS = scene.trackS;
    const headX = ox + (frame.headMX - pcx) * scaleX;
    const headY = oy + (frame.headMY - pcy) * scaleY;
    const sx = (i: number): number => ox + (trackMX[i] - pcx) * scaleX;
    const sy = (i: number): number => oy + (trackMY[i] - pcy) * scaleY;

    const traveledEnd = traveledEndIndex(trackS, n, frame.progressS);

    // ── 1. 未走段：按路段的交通工具切换线型，颜色统一深青 ─────────
    let segIdx = segAt(traveledEnd < n ? trackS[traveledEnd] : 0);
    let runMode = segments.length > 0 ? segments[segIdx].mode : 'drive';
    let run: number[] = [headX, headY];
    let prevX = headX;
    let prevY = headY;

    for (let i = traveledEnd; i < n; i += 1) {
      // 跨段：先把当前折线落笔，再用"上一个点"接上新段，否则段边界会缺一格
      while (segIdx < segments.length - 1 && trackS[i] >= segments[segIdx].endS) {
        segIdx += 1;
        const nextMode = segments[segIdx].mode;
        if (nextMode !== runMode) {
          strokePolyline(run, dashOf(runMode), TRANSPORT_STYLE[runMode].width, LIGHT_TRACK_PENDING);
          run = [prevX, prevY];
          runMode = nextMode;
        }
      }

      const x = sx(i);
      const y = sy(i);
      if (
        x < -CULL_MARGIN ||
        x > width + CULL_MARGIN ||
        y < -CULL_MARGIN ||
        y > height + CULL_MARGIN
      ) {
        // 画布外：不加入折线，但记住坐标供段边界接续
        prevX = x;
        prevY = y;
        continue;
      }
      run.push(x, y);
      prevX = x;
      prevY = y;
    }
    strokePolyline(run, dashOf(runMode), TRANSPORT_STYLE[runMode].width, LIGHT_TRACK_PENDING);

    // ── 2. 已走段：更亮的青 + 更粗，一笔画完（不分段，走过了就不再区线型）──
    if (traveledEnd > 0) {
      const walked: number[] = [sx(0), sy(0)];
      for (let i = 1; i < traveledEnd; i += 1) walked.push(sx(i), sy(i));
      walked.push(headX, headY);

      // 线宽取首段（`drive`）的宽度：已走段是一条连续的"墨迹"，
      // 沿线宽跳变会让它看起来像抖动。
      const baseWidth = segments.length > 0 ? TRANSPORT_STYLE[segments[0].mode].width : 1.6;
      strokePolyline(walked, [], baseWidth + LIGHT_TRACK_WALKED_EXTRA_WIDTH, LIGHT_TRACK_WALKED);
    }
  }

  // ── 3. 站点圆点 ────────────────────────────────────────────────
  const photoCount = scene.photoCount;
  for (let i = 0; i < photoCount; i += 1) {
    const x = ox + (scene.photoMX[i] - pcx) * scaleX;
    const y = oy + (scene.photoMY[i] - pcy) * scaleY;
    if (x < -CULL_MARGIN || x > width + CULL_MARGIN || y < -CULL_MARGIN || y > height + CULL_MARGIN) {
      continue;
    }
    const passed = scene.photoS[i] <= frame.progressS;
    ctx.beginPath();
    ctx.arc(x, y, STOP_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = passed ? LIGHT_TRACK_PENDING : 'rgba(0, 102, 204, 0.35)';
    ctx.fill();
  }

  // ── 4. 光点：实心深青 + 一圈白描边做分离（不是发光，是描边）─────
  const headX = ox + (frame.headMX - pcx) * scaleX;
  const headY = oy + (frame.headMY - pcy) * scaleY;
  ctx.beginPath();
  ctx.arc(headX, headY, HEAD_RADIUS, 0, Math.PI * 2);
  ctx.fillStyle = LIGHT_TRACK_PENDING;
  ctx.fill();
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
  ctx.stroke();

  ctx.restore();
}

/**
 * 降级格网的经纬间距（度）：缩放越大格子越细。
 *
 * 阶段 14 / W14-2 起浅色播放页的格网由底图层（`map/baseMap.ts` 的降级控制器）自己画，
 * 档位与这里一致 —— 所以本函数**不再被播放循环使用**，只导出给单测锁住档位，
 * 防止将来改了一处忘了另一处（两层网格摩尔纹就是这么来的）。
 */
export function gridSpacingFor(zoom: number): number {
  if (zoom >= 11) return 0.05;
  if (zoom >= 9.5) return 0.1;
  if (zoom >= 8) return 0.25;
  return 0.5;
}

export interface PlayStageProps {
  journey: Journey;
  onBack: () => void;
  onExported: (blob: Blob) => void;
}

export function PlayStage({ journey, onBack, onExported }: PlayStageProps): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rangeRef = useRef<HTMLInputElement>(null);
  const resumeRef = useRef(false);
  const exportResolveRef = useRef<(() => void) | null>(null);

  const clockRef = useRef<Clock | null>(null);
  if (clockRef.current === null) clockRef.current = new Clock();
  const clock = clockRef.current;

  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [fallback, setFallback] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  /**
   * 变速曲线（W12-1）：每段的播放时长。`journey` 变化时才重算。
   *
   * 为什么放在 effect 外面：`journey` 在一次播放里不变，算一次就够；
   * 放在主循环里每帧算一遍等于每秒多做 60 次 haversine + 排序。
   */
  const durations = useMemo(() => computeSegmentDurations(journey), [journey]);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    // 变速（W12-1）：把「按距离分档」的段时长折成总时长，写进 Clock 的可变字段。
    //
    // 这一行的效果有两层：
    //  1. 默认总时长从 42s（`Clock` 的初值）降到按数据算出来的真实值 ——
    //     5 张演示照片是 4s，长路段不再被压成一眨眼；
    //  2. 「导出视频」的录制时长是 `clock.durationMs / speed`（见 handleExport），
    //     所以视频时长自动跟着变，不用改录制那条链路。
    //
    // 注意：只改时长，**不改 q 的语义**。进度条、`clock.seek`、倍速按钮全部仍然基于 q，
    // 若改成基于弧长 s，拖进度条的手感会随距离分档跳变。
    clock.durationMs = Math.max(1000, totalDurationSeconds(durations) * 1000);

    let renderer: Renderer;
    try {
      renderer = new Renderer(canvas);
    } catch {
      setError('当前浏览器不支持 Canvas 2D，无法播放轨迹。');
      return;
    }
    // 内核的 `Renderer` 只留作「DPR + 尺寸」管理器（`resize()` 会把 width/height/dpr
    // 按父容器量好并写回 canvas.width/height）。**不再调 `renderer.draw()`** ——
    // 那份绘制是暗场发光配色，浅色版改由本文件的 `drawLightFrame` 自绘。
    const ctx2d = canvas.getContext('2d');
    if (!ctx2d) {
      setError('当前浏览器不支持 Canvas 2D，无法播放轨迹。');
      return;
    }

    const scene = prepareScene(journey);
    const camera = new Camera();
    const pose = createPose();

    const start = samplePose(journey, 0, pose);
    camera.reset(start.lng, start.lat);
    const startGcj = wgs84ToGcj02(start.lng, start.lat);

    let proj: Projection = analyticProjection(
      startGcj,
      journey.suggestedZoom,
      renderer.width,
      renderer.height,
    );

    let controller: MapController | null = null;
    let isFallback = true;
    let appliedMx = Number.NaN;
    let appliedMy = Number.NaN;
    let disposed = false;
    let raf = 0;
    let frameCount = 0;
    let lastCenterAt = 0;
    let started = false;
    let revealTimer = 0;
    let failsafeTimer = 0;

    const mountedAt = performance.now();
    const prefersReducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // 每次挂载用全新容器：地图 SDK 会复用旧节点，重挂载时容易拿到已销毁的实例。
    const inner = document.createElement('div');
    inner.style.cssText = 'position:absolute;inset:0;';
    host.appendChild(inner);

    // 每帧只改这些字段，不重建对象（60fps 下每帧 new 一个对象会喂 GC）
    const frameData: LightFrame = {
      scene,
      proj,
      progressS: 0,
      headMX: 0,
      headMY: 0,
      width: renderer.width,
      height: renderer.height,
      dpr: renderer.dpr,
    };

    /**
     * 开跑。只由 `whenReady()`（正常）或兜底定时器（异常）调用，内部幂等。
     * "揭幕"与"开始播放"必须是同一时刻，否则地图慢的时候会演成
     * "揭开帘子时旅程已经播了半段"。
     */
    const startPlayback = (): void => {
      if (started || disposed) return;
      started = true;
      window.clearTimeout(revealTimer);
      window.clearTimeout(failsafeTimer);
      setMapReady(true);
      if (!prefersReducedMotion) clock.play();
    };

    void initMap(inner, {
      center: startGcj,
      zoom: journey.suggestedZoom,
      interactive: false,
      // 浅色版不再借内核渲染器画格网（见本文件顶部配色说明），降级时必须由底图层自己画
      paintFallback: true,
    }).then((ctrl) => {
      if (disposed) {
        ctrl.destroy();
        return;
      }
      controller = ctrl;
      isFallback = ctrl.kind === 'fallback';
      setFallback(isFallback);
      proj = ctrl.calibrate(renderer.width, renderer.height, journey.suggestedZoom);
      appliedMx = Number.NaN;
      appliedMy = Number.NaN;
      ctrl.setCenter(startGcj.lng, startGcj.lat);

      void ctrl.whenReady().then(() => {
        if (disposed) return;
        // 底图极快时也至少显示 400ms 遮罩，避免一次无意义的黑闪
        const wait = Math.max(0, 400 - (performance.now() - mountedAt));
        revealTimer = window.setTimeout(() => {
          if (disposed) return;
          startPlayback();
        }, wait);
      });
    });

    // 兜底：`whenReady()` 要等 initMap resolve 之后才存在，而高德加载最长 8s。
    // 没有这条，弱网下遮罩可能盖到 8~12s。与 whenReady 是"谁先到谁生效"。
    failsafeTimer = window.setTimeout(() => {
      if (disposed) return;
      startPlayback();
    }, 6000);

    const syncSize = (): void => {
      renderer.resize();
      proj.ox = renderer.width / 2;
      proj.oy = renderer.height / 2;
    };

    let last = performance.now();
    let lastPlaying = false;

    const frame = (now: number): void => {
      if (disposed) return;
      raf = window.requestAnimationFrame(frame);

      frameCount += 1;
      if ((frameCount & 31) === 0) syncSize();

      const dt = now - last;
      last = now;
      const step = Math.min(Math.max(dt, 0), 50);

      clock.tick(step);
      if (clock.playing !== lastPlaying) {
        lastPlaying = clock.playing;
        setPlaying(clock.playing);
      }

      // W12-1：`q` 先经变速曲线换成弧长，再取样。q=0→0、q=1→totalMeters，单调不减。
      samplePose(journey, qToDistance(clock.q, journey, durations), pose);
      camera.follow(pose.lng, pose.lat, step, DEFAULT_TAU / Math.max(1, clock.speed));

      const camGcj = wgs84ToGcj02(camera.lng, camera.lat);
      const camMx = mercX(camGcj.lng);
      const camMy = mercY(camGcj.lat);

      if (controller) {
        const dx = (camMx - appliedMx) * proj.scaleX;
        const dy = (camMy - appliedMy) * proj.scaleY;
        const moved = dx * dx + dy * dy > 4;
        const due = now - lastCenterAt >= CENTER_INTERVAL_MS;
        if (due && (moved || !Number.isFinite(appliedMx) || !Number.isFinite(appliedMy))) {
          controller.setCenter(camGcj.lng, camGcj.lat);
          appliedMx = camMx;
          appliedMy = camMy;
          lastCenterAt = now;
        }
      }

      setCamera(proj, camMx, camMy, proj.ox, proj.oy);

      const headGcj = wgs84ToGcj02(pose.lng, pose.lat);
      frameData.proj = proj;
      frameData.progressS = pose.s;
      frameData.headMX = mercX(headGcj.lng);
      frameData.headMY = mercY(headGcj.lat);
      frameData.width = renderer.width;
      frameData.height = renderer.height;
      frameData.dpr = renderer.dpr;
      drawLightFrame(ctx2d, frameData);

      // 进度条直接写 DOM：60fps 的 setState 会把主线程吃掉
      const range = rangeRef.current;
      if (range) range.value = String(Math.round(clock.q * 1000));

      // 导出录制：播放到底后 resolve 宿主的 play()
      const resolve = exportResolveRef.current;
      if (resolve && clock.q >= 1 && !clock.playing) {
        exportResolveRef.current = null;
        resolve();
      }
    };

    raf = window.requestAnimationFrame(frame);
    window.addEventListener('resize', syncSize);

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.code === 'Space') {
        event.preventDefault();
        clock.toggle();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        clock.seek(clock.q + 0.01);
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        clock.seek(clock.q - 0.01);
      } else if (event.key === 'Home') {
        event.preventDefault();
        clock.seek(0);
      }
    };
    window.addEventListener('keydown', onKeyDown);

    return () => {
      disposed = true;
      window.clearTimeout(revealTimer);
      window.clearTimeout(failsafeTimer);
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', syncSize);
      window.removeEventListener('keydown', onKeyDown);
      exportResolveRef.current = null;
      controller?.destroy();
      controller = null;
      if (inner.parentElement) inner.parentElement.removeChild(inner);
    };
  }, [journey, clock, durations]);

  const handleToggle = useCallback(() => {
    clock.toggle();
    setPlaying(clock.playing);
  }, [clock]);

  const handleSeekStart = useCallback(() => {
    resumeRef.current = clock.playing;
    clock.pause();
    setPlaying(false);
  }, [clock]);

  const handleSeekEnd = useCallback(() => {
    if (resumeRef.current) {
      clock.play();
      setPlaying(true);
    }
  }, [clock]);

  const handleSpeed = useCallback(
    (next: number) => {
      clock.setSpeed(next);
      setSpeed(next);
    },
    [clock],
  );

  const handleExport = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!canRecord()) {
      setNotice('当前环境不支持录制视频（移动端或浏览器不支持 MediaRecorder）。请用系统录屏功能录制。');
      return;
    }
    setNotice(null);
    setExporting(true);
    setProgress(0);
    const durationMs = Math.max(1000, clock.durationMs / Math.max(1, clock.speed));
    try {
      const blob = await recordCanvas(canvas, durationMs, {
        onProgress: (value) => setProgress(value),
        play: () =>
          new Promise<void>((resolve) => {
            exportResolveRef.current = resolve;
            clock.seek(0);
            clock.play();
            setPlaying(true);
          }),
        isCancelled: () => false,
      });
      if (blob) onExported(blob);
      else setNotice('录制没有产出数据。请用系统录屏功能录制，或换一个浏览器再试。');
    } finally {
      setExporting(false);
      exportResolveRef.current = null;
    }
  }, [clock, onExported]);

  return (
    <div className="stage">
      <div className="stage-map" ref={hostRef} />
      {/* 遮罩：盖住底图尚未铺满的空白。纯色 + opacity 过渡，不用 backdrop-filter（全屏重采样吃帧）。 */}
      <div className="stage-scrim" style={{ opacity: mapReady ? 0 : 1 }} />
      <canvas className="stage-canvas" ref={canvasRef} />

      <div className="stage-top">
        <div className="stage-title">
          <strong>{journey.title}</strong>
          <span>{journey.subtitle}</span>
        </div>
        <button type="button" className="btn btn-tiny" onClick={onBack}>
          返回调整
        </button>
      </div>

      {fallback ? (
        <div className="stage-note">底图未加载，已切换示意格网（轨迹与播放不受影响）</div>
      ) : null}
      {error ? <div className="stage-note stage-note-error">{error}</div> : null}
      {notice ? <div className="stage-note">{notice}</div> : null}

      <div className="stage-controls">
        <button type="button" className="btn btn-primary" onClick={handleToggle}>
          {playing ? '暂停' : '播放'}
        </button>

        <input
          ref={rangeRef}
          className="seek"
          type="range"
          min={0}
          max={1000}
          step={1}
          defaultValue={0}
          aria-label="播放进度"
          onPointerDown={handleSeekStart}
          onPointerUp={handleSeekEnd}
          onKeyDown={handleSeekStart}
          onKeyUp={handleSeekEnd}
          onChange={(event) => clock.seek(Number(event.target.value) / 1000)}
        />

        <div className="speeds" role="group" aria-label="播放倍速">
          {[1, 2, 4].map((item) => (
            <button
              key={item}
              type="button"
              className={item === speed ? 'btn btn-tiny is-active' : 'btn btn-tiny'}
              onClick={() => handleSpeed(item)}
            >
              {item}x
            </button>
          ))}
        </div>

        <button type="button" className="btn btn-primary" disabled={exporting} onClick={handleExport}>
          {exporting ? `录制中 ${Math.round(progress * 100)}%` : '导出视频'}
        </button>
      </div>
    </div>
  );
}
