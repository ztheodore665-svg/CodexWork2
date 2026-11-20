/** 深色版应用主体（阶段 13 / W13-2：从 `showcase/src/App.tsx` 移植并改造）。
 *
 * ## 状态机
 *
 * ```
 * landing ──用演示轨迹──→ play ──导出视频──→ done
 *    └──从系统相册导入──→ setup ──开始旅程──→ play
 * ```
 *
 * 与 showcase 的差异（每一条都对应任务书里的一条硬要求）：
 *
 * 1. **零网络**：底图只用 `map/gridMap`（自绘格网）。本文件与 `theme-dark/**`
 *    里没有任何地图 SDK 的 import，也没有任何 `fetch` / XHR；
 * 2. **速度曲线走 core**：`samplePose(journey, qToDistance(q, journey, durations), pose)`，
 *    且 `clock.durationMs = totalDurationSeconds(durations) * 1000` ——
 *    与浅色版同一份实现（`core/speedCurve`），倍速按钮保留；
 * 3. **两条入口**：「用演示轨迹开始」（`data/demoJourneys.ts`）与
 *    「从系统相册导入」（`pickFromSystem` → `core/photoImport` 的 `toTrailPhotos` → `Journey`）；
 * 4. **导出视频**：`core/videoRecorder` 的 `recordCanvas`（复用可见画布，不新开离屏）。
 *
 * ## 主循环为什么这么写（照抄 showcase 的实测结论，别改）
 *
 * - `q` **不进 React state**：每帧变化的量只活在 `Clock` 里，只有 `playing`
 *   变化时才同步一次 React，否则 60fps × setState 会把主线程吃掉；
 * - 相机用 `Camera.follow` 阻尼跟随，倍速时按 `DEFAULT_TAU / max(1, speed)` 压滞后；
 * - 内部全程 WGS84，只在渲染前 `wgs84ToGcj02`：渲染器的 `prepareScene` 内部也是
 *   这么转的，两边必须一致，否则轨迹与卡片锚点会错开；
 * - 降级格网只在**揭幕之后**才画（`fallback && started`），否则揭幕前会闪一屏经纬网。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import {
  analyticProjection,
  buildJourney,
  Camera,
  Clock,
  createPose,
  DEFAULT_TAU,
  prepareScene,
  Renderer,
  samplePose,
  setCamera,
  type DrawState,
  type Journey,
  type Projection,
  type TransportMode,
} from '@travelmemory/trail';
import { mercX, mercY, wgs84ToGcj02 } from '@travelmemory/geo';
import { pickFromSystem, type SystemPhoto } from '@travelmemory/photo-source';

import { computeSegmentDurations, qToDistance, totalDurationSeconds } from '../core/speedCurve';
import { toTrailPhotos } from '../core/photoImport';
import { applyLocation } from '../core/validate';
import { canRecord, recordCanvas } from '../core/videoRecorder';
import { loadIdentity, type LocalIdentity } from '../core/identity';
import { buildJourneyBundle, downloadBlob } from '../core/journeyBuild';
import { demoPhotosForExport, journeyFileJson, signJourneyFile } from './exportJourney';
import { DEMO_TRIPS } from './data/demoJourneys';
import { createGridMap, type GridMapController } from './map/gridMap';
import { DonePanel } from './ui/DonePanel';
import { Hud, type HudHandle } from './ui/Hud';
import { Landing } from './ui/Landing';
import { PhotoCards, type PhotoCardsHandle } from './ui/PhotoCards';
import { SetupPanel } from './ui/SetupPanel';
import { Timeline, type TimelineHandle } from './ui/Timeline';
import './styles.css';

type Mode = 'landing' | 'setup' | 'play' | 'done';

/** 导出的旅程标题（本应用不采集标题）。 */
const IMPORTED_TITLE = '我的旅程';
/** 无卡片叠加层也需要一个空锚点数组（渲染器按 anchorCount 读）。 */
const EMPTY_ANCHORS = new Float32Array(0);

/** 当前弧长落在第几天（`journey.days` 已按时间升序）。 */
function dayIndexAt(journey: Journey, s: number): number {
  const days = journey.days;
  if (days.length === 0) return 0;
  for (let index = 0; index < days.length; index += 1) {
    if (s <= days[index].endS + 0.001) return index;
  }
  return days.length - 1;
}

/**
 * 当前地名。`pose.t` 在播放时单调递增，用一个游标把每帧比较次数压到常数级：
 * 先让游标追上 `t`，再只在游标 ±3 张里找最近的一张（照抄 showcase）。
 */
function createPlaceLookup(journey: Journey): (t: number) => string {
  const photos = journey.photos;
  if (photos.length === 0) return () => '';
  let cursor = 0;
  return (t: number): string => {
    while (cursor + 1 < photos.length && photos[cursor + 1].takenAt <= t) cursor += 1;
    while (cursor > 0 && photos[cursor].takenAt > t) cursor -= 1;
    const from = cursor > 3 ? cursor - 3 : 0;
    const to = Math.min(photos.length - 1, cursor + 3);
    let best = cursor;
    let bestGap = Math.abs(photos[cursor].takenAt - t);
    for (let index = from; index <= to; index += 1) {
      const gap = Math.abs(photos[index].takenAt - t);
      if (gap < bestGap) {
        bestGap = gap;
        best = index;
      }
    }
    return photos[best].placeName ?? '';
  };
}

/** 渲染器的格网间距档位（与 `map/gridMap` 的 `gridSpacingFor` 同一套值）。 */
function gridSpacingFor(zoom: number): number {
  if (zoom >= 11) return 0.05;
  if (zoom >= 9.5) return 0.1;
  if (zoom >= 8) return 0.25;
  return 0.5;
}

/* ── 播放舞台 ───────────────────────────────────────────────── */

interface PlayStageProps {
  journey: Journey;
  onBack: () => void;
  /** 录制完成，把 Blob 交给上层进完成页。 */
  onExported: (blob: Blob) => void;
}

function PlayStage({ journey, onBack, onExported }: PlayStageProps): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hudRef = useRef<HudHandle>(null);
  const timelineRef = useRef<TimelineHandle>(null);
  const cardsRef = useRef<PhotoCardsHandle>(null);
  const resetRef = useRef<() => void>(() => undefined);
  /** 录制时"播到底"的回调（主循环里 resolve 它）。 */
  const exportResolveRef = useRef<(() => void) | null>(null);

  const clockRef = useRef<Clock | null>(null);
  if (clockRef.current === null) clockRef.current = new Clock();
  const clock = clockRef.current;

  /**
   * 省界轮廓是否就绪（W14-5）。
   *
   * 用 ref 而不是 state：主循环每帧都要读它，`state` 会带来闭包快照问题
   * （effect 的依赖里加上它会让整条播放链路重建）。而"要不要画格网"这件事
   * 不需要触发 React 重渲染 —— 改的是 canvas 的绘制内容。
   */
  const outlineReadyRef = useRef(false);

  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [mapReady, setMapReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  /**
   * 变速曲线（W12-1）：每段的播放时长。`journey` 在一次播放里不变，所以只算一次 ——
   * 放进主循环等于每秒多做 60 次 haversine。
   */
  const durations = useMemo(() => computeSegmentDurations(journey), [journey]);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    // 变速（W12-1）：把「按距离分档」的段时长折成总时长写进 Clock。
    // 只改时长，**不改 q 的语义**（进度条 / seek / 倍速全部仍基于 q）。
    clock.durationMs = Math.max(1000, totalDurationSeconds(durations) * 1000);

    let renderer: Renderer;
    try {
      renderer = new Renderer(canvas);
    } catch {
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

    let controller: GridMapController | null = null;
    let disposed = false;
    let raf = 0;

    const spacing = gridSpacingFor(journey.suggestedZoom);

    // 每次挂载用全新容器：避免重挂载时复用已被销毁的节点
    const inner = document.createElement('div');
    inner.style.cssText = 'position:absolute;inset:0;';
    host.appendChild(inner);

    const drawState: DrawState = {
      scene,
      proj,
      progressS: 0,
      nowMs: 0,
      fallback: true,
      gridSpacingDeg: spacing,
      headMX: 0,
      headMY: 0,
      anchors: EMPTY_ANCHORS,
      anchorCount: 0,
    };

    let last = performance.now();
    let lastPlaying = false;
    let frameCount = 0;
    let lastCenterAt = 0;
    let started = false;
    let revealTimer = 0;
    let failsafeTimer = 0;
    const mountedAt = performance.now();
    const lookupPlace = createPlaceLookup(journey);

    const prefersReducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /**
     * 开跑。只由 `whenReady()`（正常）或兜底定时器（异常）调用，内部幂等。
     * 「揭幕」与「开始播放」必须是同一时刻 —— 否则地图慢的时候会演成
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

    // 自绘底图没有加载过程：同步建好，异步语义只是为了与浅色版的主循环保持同一形状。
    //
    // W14-5：`outline` 默认开 —— 本地省界 JSON 到位后底图改画省界，不再画格网。
    // 播放页原本 `paintGrid: false`（格网交给内核渲染器），现在改为由**底图层**
    // 画省界，于是内核那边要跟着关（见下面 `drawState.fallback`）。
    controller = createGridMap(inner, {
      center: startGcj,
      zoom: journey.suggestedZoom,
      // 播放页的平面是 **GCJ-02**（中心来自 `wgs84ToGcj02`，轨迹也在 GCJ 平面），
      // 这里必须如实声明：省界因此不做 WGS 换算，与轨迹严丝合缝（W14-5 修正）。
      coordinateSystem: 'gcj02',
      interactive: false,
      paintGrid: false,
      outline: true,
      onOutlineChange: (ready) => {
        outlineReadyRef.current = ready;
      },
    });
    proj = controller.calibrate(renderer.width, renderer.height, journey.suggestedZoom);
    controller.setCenter(startGcj.lng, startGcj.lat);

    void controller.whenReady().then(() => {
      if (disposed) return;
      // 底图极快时也至少显示 400ms 遮罩，避免一次无意义的黑闪
      const wait = Math.max(0, 400 - (performance.now() - mountedAt));
      revealTimer = window.setTimeout(() => {
        if (disposed) return;
        startPlayback();
      }, wait);
    });

    // 兜底：任何异步路径出问题都不会把遮罩永远盖在屏幕上（与 whenReady 谁先到谁生效）
    failsafeTimer = window.setTimeout(() => {
      if (disposed) return;
      startPlayback();
    }, 6000);

    /** 画布尺寸自愈 + 视口变化处理（`resize()` 内部有短路，重复调用几乎零成本）。 */
    const syncSize = (): void => {
      renderer.resize();
      proj.ox = renderer.width / 2;
      proj.oy = renderer.height / 2;
    };

    const frame = (now: number): void => {
      if (disposed) return;
      raf = window.requestAnimationFrame(frame);

      // 每 32 帧校正一次画布尺寸：防止任何布局/DPR 变化把画布锁在小尺寸上
      frameCount += 1;
      if ((frameCount & 31) === 0) syncSize();

      const dt = now - last;
      last = now;
      const step = Math.min(Math.max(dt, 0), 50);

      clock.tick(step);
      // 只在播放状态「变化」的那一帧同步一次 React，不逐帧 setState
      if (clock.playing !== lastPlaying) {
        lastPlaying = clock.playing;
        setPlaying(clock.playing);
      }

      // W12-1：`q` 先经变速曲线换成弧长，再取样。q=0→0、q=1→totalMeters，单调不减。
      samplePose(journey, qToDistance(clock.q, journey, durations), pose);
      // 倍速补偿：4x 下屏幕速度翻 4 倍，阻尼滞后会到 ~208px（光点被甩出中心）。
      camera.follow(pose.lng, pose.lat, step, DEFAULT_TAU / Math.max(1, clock.speed));

      const camGcj = wgs84ToGcj02(camera.lng, camera.lat);
      const camMx = mercX(camGcj.lng);
      const camMy = mercY(camGcj.lat);

      if (controller) {
        // 自绘格网只按时间闸重画（40ms 一次），没必要每帧都重绘格线
        const due = now - lastCenterAt >= 40;
        if (due) {
          controller.setCenter(camGcj.lng, camGcj.lat);
          lastCenterAt = now;
        }
      }

      // 覆盖层锚定「相机当前值」，不是「地图最后一次被下发的中心」：
      // 地图 25Hz、画布 60fps，若锚定 applied 会让整条轨迹+光点以 25Hz 抖动。
      setCamera(proj, camMx, camMy, proj.ox, proj.oy);

      const headGcj = wgs84ToGcj02(pose.lng, pose.lat);
      const headMX = mercX(headGcj.lng);
      const headMY = mercY(headGcj.lat);

      const cards = cardsRef.current;
      if (cards) {
        const headScreenX = proj.ox + (headMX - proj.cx) * proj.scaleX;
        const headScreenY = proj.oy + (headMY - proj.cy) * proj.scaleY;
        cards.update(scene, proj, pose.s, !clock.playing, now, headScreenX, headScreenY);
      }

      drawState.proj = proj;
      drawState.progressS = pose.s;
      drawState.nowMs = now;
      // 格网只在揭幕之后才画：揭幕前用户看到的是「干净深色底」而不是满屏经纬线
      drawState.fallback = started && !outlineReadyRef.current;
      drawState.headMX = headMX;
      drawState.headMY = headMY;
      drawState.anchors = cards ? cards.anchors : EMPTY_ANCHORS;
      drawState.anchorCount = scene.photoCount;
      renderer.draw(drawState);

      const hud = hudRef.current;
      if (hud) {
        hud.update(
          pose.t,
          dayIndexAt(journey, pose.s),
          journey.days.length,
          pose.s,
          clock.q,
          lookupPlace(pose.t),
        );
      }
      timelineRef.current?.draw(clock.q);

      // 导出录制：播到底且已暂停 → resolve 宿主的 play()
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
        return;
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        clock.seek(clock.q + 0.01);
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        clock.seek(clock.q - 0.01);
        return;
      }
      if (event.key === 'Home') {
        event.preventDefault();
        clock.seek(0);
      }
    };
    window.addEventListener('keydown', onKeyDown);

    resetRef.current = (): void => {
      samplePose(journey, 0, pose);
      camera.reset(pose.lng, pose.lat);
      clock.replay();
    };

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

  const handleSpeed = useCallback(
    (next: number) => {
      clock.setSpeed(next);
      setSpeed(next);
    },
    [clock],
  );

  const handleReplay = useCallback(() => {
    resetRef.current();
  }, []);

  const handleExport = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!canRecord()) {
      setNotice(
        '当前环境不支持录制视频（移动端或浏览器不支持 MediaRecorder）。请用系统录屏功能录制。',
      );
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
    <div className="play">
      <div ref={hostRef} style={{ position: 'absolute', inset: 0 }} />
      {/* 盖住底图尚未铺满时的占位；就绪后淡出。纯色 + opacity 过渡＝合成器单层操作，
          不要加 backdrop-filter（全屏重采样会吃掉帧率）。 */}
      <div className="map-scrim" style={{ opacity: mapReady ? 0 : 1 }} />
      <canvas className="play-canvas" ref={canvasRef} />
      <PhotoCards ref={cardsRef} journey={journey} />
      <Hud ref={hudRef} title={journey.title} subtitle={journey.subtitle} />
      <button type="button" className="back-btn" onClick={onBack}>
        返回调整
      </button>

      <button type="button" className="export-btn" disabled={exporting} onClick={handleExport}>
        {exporting ? `录制中 ${Math.round(progress * 100)}%` : '导出视频'}
      </button>
      {notice ? <div className="play-note">{notice}</div> : null}
      {error ? <div className="play-note play-note-warn">{error}</div> : null}

      <Timeline
        ref={timelineRef}
        journey={journey}
        clock={clock}
        playing={playing}
        speed={speed}
        onSpeedChange={handleSpeed}
        onReplay={handleReplay}
      />
    </div>
  );
}

/* ── App ─────────────────────────────────────────────────────── */

/**
 * 深色版根组件。
 *
 * 外层 `<div className="theme-dark">` 是**样式作用域锚点**：`theme-dark/styles.css`
 * 里的每条规则都以 `.theme-dark` 开头，因此绝不会影响浅色（嵌入）主题。
 */
export default function App(): JSX.Element {
  return (
    <div className="theme-dark">
      <DarkApp />
    </div>
  );
}

function DarkApp(): JSX.Element {
  const [mode, setMode] = useState<Mode>('landing');
  /** 演示线（`Photo[]` 自带坐标，不需要补点）。 */
  const [demoTrip, setDemoTrip] = useState<(typeof DEMO_TRIPS)[number] | null>(null);
  /** 从系统相册导入的照片（内存态，关页即丢）。 */
  const [photos, setPhotos] = useState<SystemPhoto[]>([]);
  /**
   * 每段交通工具（W14-3a）：`{ 出发照片 id: 模式 }`。
   *
   * 键与 `Photo.transportToNext` 的索引键一致（都是"出发那张照片的 id"），
   * 所以下面可以直接 `new Map(Object.entries(transports))` 交给 `toTrailPhotos`，
   * 不需要再翻译一层 —— 浅色版 `App.tsx:135-143` 就是同一套写法。
   */
  const [transports, setTransports] = useState<Record<string, TransportMode>>({});
  /** 本机身份（签名 `journey.json` 用）。加载失败/未就绪时为 null。 */
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [videoBlob, setVideoBlob] = useState<Blob | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const demos = useMemo(
    () =>
      DEMO_TRIPS.map((trip) => ({
        id: trip.id,
        title: trip.title,
        subtitle: trip.subtitle,
        photoCount: trip.photos.length,
      })),
    [],
  );

  /* ── 视频 Blob → objectURL（卸载/更换时 revoke，避免泄漏） ───── */
  useEffect(() => {
    if (!videoBlob) {
      setVideoUrl(null);
      return;
    }
    const url = URL.createObjectURL(videoBlob);
    setVideoUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [videoBlob]);

  /* ── 身份（W14-3b：签名 journey.json 用） ──────────────────── */
  useEffect(() => {
    let alive = true;
    void loadIdentity().then((value) => {
      if (alive) setIdentity(value);
    });
    return () => {
      alive = false;
    };
  }, []);

  /* ── 交通工具表 → Map（`toTrailPhotos` 的入参形态） ─────────── */
  const transportMap = useMemo(
    () => new Map<string, TransportMode>(Object.entries(transports)),
    [transports],
  );

  /* ── 构建 Journey（两条来源：演示数据 / 相册导入） ─────────── */
  const journey = useMemo<Journey | null>(() => {
    if (mode !== 'play' && mode !== 'done') return null;
    if (demoTrip) {
      return buildJourney({
        id: demoTrip.id,
        title: demoTrip.title,
        subtitle: demoTrip.subtitle,
        photos: demoTrip.photos,
      });
    }
    if (photos.length === 0) return null;
    // W14-3a：把用户选的每段交通工具带进 `Photo.transportToNext`，
    // 内核 `Renderer` 会按 `TRANSPORT_STYLE` 画出不同线型/颜色。
    const trail = toTrailPhotos(photos, { transports: transportMap });
    const located = trail.filter((photo) => photo.lat !== null && photo.lng !== null).length;
    if (located < 2) return null;
    return buildJourney({
      id: 'track-studio-dark',
      title: IMPORTED_TITLE,
      subtitle: `${trail.length} 张照片 · ${located} 个定位点`,
      photos: trail,
    });
  }, [mode, demoTrip, photos, transportMap]);

  /* ── 导入（系统相册 / 拖拽）───────────────────────────────── */
  /**
   * 一条导入链路吃两种入口：按钮（打开系统文件选择器）与拖拽（已经有 `File[]`）。
   *
   * 拖拽时把 `waitForSelection` 换成一个"直接交回这些文件"的函数 ——
   * `pickFromSystem` 的其余步骤（读字节 → 读 EXIF → 压缩略图）完全复用，
   * 不会出现"拖进来的照片没有 EXIF 时间"这种两套实现才有的偏差。
   */
  const importFiles = useCallback(async (files?: File[]): Promise<void> => {
    setError(null);
    setBusy(true);
    setImporting({ done: 0, total: files ? files.length : 0 });
    try {
      const picked = await pickFromSystem(
        {},
        files ? { waitForSelection: async () => files } : {},
      );
      if (picked.length === 0) return;
      setDemoTrip(null);
      setPhotos((prev) => [...prev, ...picked]);
      setMode('setup');
    } catch {
      setError('读取照片失败。请重试，或换一个浏览器。');
    } finally {
      setBusy(false);
      setImporting(null);
    }
  }, []);

  const handlePickFiles = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileInput = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files ? Array.from(event.target.files) : [];
      // 清空 value：否则同一个文件第二次选不会触发 change
      event.target.value = '';
      if (files.length === 0) return;
      void importFiles(files);
    },
    [importFiles],
  );

  const handleDropFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      void importFiles(files);
    },
    [importFiles],
  );

  /* ── 页面流转 ─────────────────────────────────────────────── */
  const handleSelectDemo = useCallback((id: string) => {
    const trip = DEMO_TRIPS.find((item) => item.id === id);
    if (!trip) return;
    // 演示数据的每张照片都自带坐标，补点页对它没有意义，直接进播放
    setDemoTrip(trip);
    setPhotos([]);
    // 演示线自带坐标、不吃 `transports`（段是按演示照片顺序画的），
    // 但清空一下更安全：上一轮的键会当成"某张照片的模式"留在内存里。
    setTransports({});
    setError(null);
    setMode('play');
  }, []);

  const handleApplyLocation = useCallback((photoId: string, lat: number, lng: number) => {
    setPhotos((prev) => applyLocation(prev, photoId, lat, lng));
  }, []);

  /** W14-3a：写回一段的交通工具。 */
  const handleTransport = useCallback((photoId: string, next: TransportMode) => {
    setTransports((prev) => ({ ...prev, [photoId]: next }));
  }, []);

  const handleStart = useCallback(() => {
    setMode('play');
  }, []);

  const handleBackFromSetup = useCallback(() => {
    setMode('landing');
  }, []);

  const handleBackFromPlay = useCallback(() => {
    // 演示线没有补点页，退回首页；相册导入的退回补点页
    setMode(demoTrip ? 'landing' : 'setup');
  }, [demoTrip]);

  const handleExported = useCallback((blob: Blob) => {
    setVideoBlob(blob);
    setMode('done');
  }, []);

  const handleDownloadVideo = useCallback(() => {
    if (!videoBlob) return;
    const url = URL.createObjectURL(videoBlob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'track-studio.webm';
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // 立刻 revoke 会让部分浏览器来不及开始下载，延后一拍
    window.setTimeout(() => {
      try {
        URL.revokeObjectURL(url);
      } catch {
        /* 已经 revoke 过 */
      }
    }, 1000);
  }, [videoBlob]);

  const handleReplay = useCallback(() => {
    setMode('play');
  }, []);

  /**
   * W14-3b：签好名的 `journey.json` 文本（+ 签名公钥）。
   *
   * 为什么在 `useMemo` 里就把名签好，而不是点了按钮再签：
   *  - 完成页要用 `journeyJson === null` 决定按钮置灰，这就必须提前知道
   *    "有没有可导出的东西"；
   *  - 签名是纯计算（ed25519 对几 KB 的 JSON ≈ 亚毫秒），提前算不会卡 UI；
   *  - 反过来，如果放在点击回调里算，`onExportJson` 就成了可能抛错的异步动作，
   *    完成页还得为它维护错误态 —— 收益为零。
   *
   * `null` 的三种情况：身份还没加载好、没有任何照片、导入的照片不足 2 张。
   */
  const signedJourney = useMemo<{ json: string; signerPubkey: string } | null>(() => {
    if (!identity) return null;

    // 演示线走适配器（演示照片是内核 `Photo[]`，不是 `SystemPhoto`）；
    // 导入线直接用内存里的照片。两条来源最终都折成 `buildJourneyBundle` 的入参类型。
    const exportPhotos =
      photos.length > 0 ? photos : demoTrip ? demoPhotosForExport(demoTrip.photos) : [];
    if (exportPhotos.length === 0) return null;

    const located = exportPhotos.filter(
      (photo) =>
        typeof photo.lat === 'number' &&
        Number.isFinite(photo.lat) &&
        typeof photo.lng === 'number' &&
        Number.isFinite(photo.lng),
    ).length;
    if (located < 2) return null;

    try {
      const bundle = buildJourneyBundle({
        title: demoTrip ? demoTrip.title : IMPORTED_TITLE,
        photos: exportPhotos,
      });
      const file = signJourneyFile(bundle, identity.seedHex);
      return { json: journeyFileJson(file), signerPubkey: file.signerPubkey };
    } catch {
      // 签名失败（种子损坏等）不该把完成页打成白屏：按钮置灰即可
      return null;
    }
  }, [identity, photos, demoTrip]);

  const handleExportJson = useCallback(() => {
    if (!signedJourney) return;
    // 只落盘、不上传（B16）：一个 JSON 文件，用户可以自己拿去验签或导入主应用
    downloadBlob(
      new Blob([signedJourney.json], { type: 'application/json' }),
      'journey.json',
    );
  }, [signedJourney]);

  const handleClose = useCallback(() => {
    setDemoTrip(null);
    setPhotos([]);
    setTransports({});
    setVideoBlob(null);
    setError(null);
    setMode('landing');
  }, []);

  /* ── 渲染 ─────────────────────────────────────────────────── */

  if (mode === 'setup') {
    return (
      <>
        <SetupPanel
          photos={photos}
          transports={transports}
          busy={busy}
          importing={importing}
          onApplyLocation={handleApplyLocation}
          onTransport={handleTransport}
          onAddPhotos={handlePickFiles}
          onStart={handleStart}
          onBack={handleBackFromSetup}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={handleFileInput}
          style={{ display: 'none' }}
        />
      </>
    );
  }

  if (mode === 'play' && journey && journey.track.length > 0) {
    return <PlayStage journey={journey} onBack={handleBackFromPlay} onExported={handleExported} />;
  }

  if (mode === 'done') {
    return (
      <DonePanel
        videoUrl={videoUrl}
        videoSizeMb={videoBlob ? videoBlob.size / (1024 * 1024) : null}
        journeyJson={signedJourney ? signedJourney.json : null}
        signerPubkey={signedJourney ? signedJourney.signerPubkey : null}
        onDownload={handleDownloadVideo}
        onExportJson={handleExportJson}
        onReplay={handleReplay}
        onClose={handleClose}
      />
    );
  }

  if (mode === 'play') {
    // 走到这里只可能是"带位置的照片不足 2 张"，`buildJourney` 没有轨迹
    return (
      <div className="modal-mask">
        <div className="modal">
          <div className="modal-title">还差一点</div>
          <div className="modal-text">
            这条轨迹至少需要 2 张已定位的照片。回到上一页，选中照片后在地图上点一下补个地点。
          </div>
          <div className="modal-actions">
            <button
              type="button"
              className="btn"
              onClick={() => setMode(demoTrip ? 'landing' : 'setup')}
            >
              返回
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <Landing
        trips={demos}
        busy={busy}
        error={error}
        onSelectDemo={handleSelectDemo}
        onPickFiles={handlePickFiles}
        onDropFiles={handleDropFiles}
      />
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={handleFileInput}
        style={{ display: 'none' }}
      />
    </>
  );
}
