/** 全屏轨迹回放层。
 *
 * ## 为什么用 Dialog 而不是自己做一个全屏 div
 *
 * 回放是"模态"的：Esc 关闭、焦点陷阱、关闭后焦点回到触发行、body 滚动锁 —— 这四件事
 * 自己写必错一处。Radix 的 Dialog 已经全给了，我们只需要把 `DialogContent` 撑满全屏。
 *
 * ## 为什么主循环里一个 setState 都没有
 *
 * 60fps 下每帧 setState 会让 React 每 16ms 重渲染一次整棵树，回放立刻掉帧。
 * 所以进度百分比、进度条宽度、播放/暂停图标全部走 ref 直接改 DOM（4 帧节流一次），
 * 沿途浮出的照片卡片也是每帧由 `cards.update()` 直接写 DOM。
 * React 只负责"开/关"、"数据变了重建"和"本机缩略图取到了"这三件低频的事；
 * 最后一件刻意不进主循环的 effect 依赖，否则缩略图一到位播放就被打回 0%。
 *
 * ## 相机为什么锚在相机值而不是"最后下发的中心"
 *
 * 底图每 40ms 才 `setCenter` 一次（防瓦片被反复取消），而画布是 60fps。
 * 若用"最后下发的中心"当投影锚点，整条轨迹+光点会以 25Hz 跳动；
 * 锚在相机当前值则覆盖层绝对平滑，代价只是底图最多落后 40ms（≈9.5px）。
 * 亮物体的抖动比暗底图的滞后更刺眼，所以把误差放在底图这一侧。
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { Pause, Play, X } from 'lucide-react';
import {
  analyticProjection,
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
} from '@travelmemory/trail';
import { mercX, mercY, wgs84ToGcj02 } from '@travelmemory/geo';
import { Dialog, DialogContent, DialogTitle } from '../../../components/ui/dialog';
import type { PhotoListItem } from '../../../services/api/photos';
import type { TripView } from '../../../services/api/trips';
import { buildReplayJourney } from './adapter';
import { createReplayMap, type ReplayMapController } from './replayMap';
import { PhotoCards, type PhotoCardsHandle } from './PhotoCards';
import { loadReplayThumbs } from './thumbs';

const AMAP_JS_KEY = (import.meta.env.VITE_AMAP_JS_KEY as string | undefined) ?? '';
const AMAP_SECURITY_CODE = (import.meta.env.VITE_AMAP_SECURITY_CODE as string | undefined) ?? '';

/** 底图下发中心的最小间隔。与 showcase/src/App.tsx 保持一致，便于同机对照。
 *
 * 别调成 50 或 100：`lastCenterAt = now` 取的是 rAF 时间戳（16.667ms 的整数倍），
 * interval 会被量化成整数帧，40/50 都是「每 3 帧一次」；50 又正好压在 3×16.667 的
 * 临界点上，真假各半会在 3~4 帧之间抖。100（6 帧）实测同样没提升 —— 播放后段的
 * 掉帧来自镜头进入新区域时高德瓦片的动态加载，不是下发频率。
 * 完整调查见 docs/stages/stage9-trail-replay.md「帧率调查」。 */
const CENTER_INTERVAL_MS = 40;
/** 卡片句柄还没挂载到 ref 时的空锚点（渲染器会跳过引线）。
 *  React 的 ref 在首帧 rAF 之前就已赋值，所以这基本只是个类型安全的兜底。 */
const EMPTY_ANCHORS = new Float32Array(0);

export interface ReplayStageProps {
  trip: TripView | null;
  photos: readonly PhotoListItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** 降级底图（自绘经纬网）的间距，按缩放档位选 */
function gridSpacingFor(zoom: number): number {
  if (zoom >= 11) return 0.05;
  if (zoom >= 9.5) return 0.1;
  if (zoom >= 8) return 0.25;
  return 0.5;
}

/**
 * 进度条的键盘映射（ARIA Authoring Practices 的标准 slider 键位）。
 * 抽成纯函数是为了能单测 —— `role="slider"` 曾经只有 tabIndex 没有 onKeyDown，
 * 组件测试覆盖不到，正是那次 P2 无障碍缺陷溜过去的原因。
 *
 * @returns 目标进度 q；按键不归我们管时返回 null（调用方便不要 preventDefault）。
 *          不在这里夹 [0,1]：交给 `Clock.seek` 统一处理，避免两处 clamp 分叉。
 */
export function sliderStep(key: string, shiftKey: boolean, q: number): number | null {
  const fine = shiftKey ? 0.01 : 0.05;
  switch (key) {
    case 'ArrowRight':
    case 'ArrowUp':
      return q + fine;
    case 'ArrowLeft':
    case 'ArrowDown':
      return q - fine;
    case 'PageUp':
      return q + 0.1;
    case 'PageDown':
      return q - 0.1;
    case 'Home':
      return 0;
    case 'End':
      return 1;
    default:
      return null;
  }
}

/**
 * 按里程给播放时长：内核默认 42s 是给 2000km 量级的演示线调的，
 * 一条 30km 的城市行程用 42s 播完会慢到像卡住，反过来 3000km 又会一眨眼。
 * 以 ~48 m/ms（≈48km/s）为基准，夹在 18~60s。
 */
function durationFor(totalMeters: number): number {
  const raw = totalMeters / 48;
  return Math.min(60000, Math.max(18000, raw));
}

interface ReplayCanvasProps {
  journey: Journey;
  title: string;
  subtitle: string;
  onClose: () => void;
}

function ReplayCanvas({ journey, title, subtitle, onClose }: ReplayCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fillRef = useRef<HTMLDivElement>(null);
  const pctRef = useRef<HTMLSpanElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef(false);
  const resumeRef = useRef(false);
  const cardsRef = useRef<PhotoCardsHandle | null>(null);
  /** 本机 IndexedDB 里的真实缩略图。null = 还没取完 / 一张都没有。 */
  const [thumbs, setThumbs] = useState<ReadonlyMap<string, string> | null>(null);

  // 缩略图异步到达只触发**一次 React 重渲染**。主循环 useEffect 的依赖是
  // [journey, clock]，这里没有动 journey，所以播放不会被重置、rAF 链不会重建。
  useEffect(() => {
    let cancelled = false;
    void loadReplayThumbs(journey.photos.map((p) => p.id)).then((map) => {
      if (!cancelled && map.size > 0) setThumbs(map);
    });
    return () => {
      cancelled = true;
    };
  }, [journey]);

  const clockRef = useRef<Clock | null>(null);
  if (clockRef.current === null) clockRef.current = new Clock();
  const clock = clockRef.current;

  /** 读屏念的进度文本：`第 3 天 · 47%`。没有天数信息时只给百分比。 */
  const ariaValueText = (q: number): string => {
    const percent = Math.round(q * 100);
    const days = journey.days;
    if (days.length === 0) return `${percent}%`;
    const s = q * journey.totalMeters;
    // 初始化成最后一天：弧长落在所有 endS 之后（=100%）时不该退回第 1 天
    let index = days.length - 1;
    for (let i = 0; i < days.length; i += 1) {
      if (s <= days[i].endS + 0.001) {
        index = i;
        break;
      }
    }
    return `第 ${index + 1} 天 · ${percent}%`;
  };

  /**
   * 把进度写进 DOM（进度条宽度 / 百分比文字 / 两个 aria 属性）。
   * 主循环每 4 帧调一次，键盘操作后立刻调一次 —— 两条路径必须共用，
   * 否则键盘 seek 之后读屏的 aria-valuenow 会停在旧值上。
   */
  const writeProgress = (q: number): void => {
    const percent = Math.round(q * 100);
    const fill = fillRef.current;
    if (fill) fill.style.width = `${(q * 100).toFixed(2)}%`;
    const label = pctRef.current;
    if (label) label.textContent = `${percent}%`;
    const track = trackRef.current;
    if (track) {
      track.setAttribute('aria-valuenow', String(percent));
      track.setAttribute('aria-valuetext', ariaValueText(q));
    }
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    // 每次挂载都用**全新的容器节点**：StrictMode 会双挂载，高德在同一个 DOM 节点上
    // 销毁再初始化容易残留上一张地图的图层（表现为底图叠影）。
    const mapHost = document.createElement('div');
    mapHost.style.cssText = 'position:absolute;inset:0;';
    host.appendChild(mapHost);

    const scene = prepareScene(journey);
    const renderer = new Renderer(canvas);
    const camera = new Camera();
    const pose = createPose();

    const start = samplePose(journey, 0, pose);
    camera.reset(start.lng, start.lat);
    const startGcj = wgs84ToGcj02(start.lng, start.lat);

    clock.durationMs = durationFor(journey.totalMeters);
    clock.replay();

    let proj: Projection = analyticProjection(
      startGcj,
      journey.suggestedZoom,
      renderer.width,
      renderer.height,
    );
    let controller: ReplayMapController | null = null;
    let isFallback = true;
    let appliedMx = Number.NaN;
    let appliedMy = Number.NaN;
    let lastCenterAt = 0;
    let disposed = false;
    let raf = 0;
    let last = performance.now();
    let frameCount = 0;

    const spacing = gridSpacingFor(journey.suggestedZoom);

    void createReplayMap(mapHost, startGcj, journey.suggestedZoom, AMAP_JS_KEY, AMAP_SECURITY_CODE)
      .then((ctrl) => {
        if (disposed) {
          ctrl.destroy();
          return;
        }
        controller = ctrl;
        // 契约：`createReplayMap` 成功即代表拿到了**真地图**控制器 —— 降级路径一律 reject
        // （见 replayMap.ts 与 showcase/src/map/baseMap.ts 的区别），所以这里可以无条件置 false。
        // ⚠️ 若将来把 `createReplayMap` 改成"失败也返回一个降级控制器"，这里必须改为判 `ctrl.kind`。
        isFallback = false;
        controller.setCenter(startGcj.lng, startGcj.lat, true);
      })
      .catch(() => {
        // 没有 key / SDK 加载失败：保留 isFallback=true，渲染器自绘经纬格网，
        // 轨迹照常播放。绝不把异常抛给用户。
        isFallback = true;
      });

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

    const syncSize = (): void => {
      renderer.resize();
      proj.ox = renderer.width / 2;
      proj.oy = renderer.height / 2;
    };

    const frame = (now: number): void => {
      if (disposed) return;
      raf = window.requestAnimationFrame(frame);

      // 每 32 帧校正一次画布尺寸：canvas 是替换元素，任何布局/DPR 变化都可能让它锁死在小尺寸
      frameCount += 1;
      if ((frameCount & 31) === 0) syncSize();

      const dt = now - last;
      last = now;
      const step = Math.min(Math.max(dt, 0), 50);

      clock.tick(step);
      samplePose(journey, clock.q * scene.totalMeters, pose);
      // 倍速补偿：屏幕速度随 speed 线性增长，4x 时阻尼滞后会到 ~200px，光点被甩出中心
      camera.follow(pose.lng, pose.lat, step, DEFAULT_TAU / Math.max(1, clock.speed));

      const camGcj = wgs84ToGcj02(camera.lng, camera.lat);
      const camMx = mercX(camGcj.lng);
      const camMy = mercY(camGcj.lat);

      if (controller) {
        const dx = (camMx - appliedMx) * proj.scaleX;
        const dy = (camMy - appliedMy) * proj.scaleY;
        const moved = dx * dx + dy * dy > 4; // 位移 ≥ 2px 才值得下发
        const due = now - lastCenterAt >= CENTER_INTERVAL_MS;
        if (due && (moved || !Number.isFinite(appliedMx) || !Number.isFinite(appliedMy))) {
          controller.setCenter(camGcj.lng, camGcj.lat, true);
          appliedMx = camMx;
          appliedMy = camMy;
          lastCenterAt = now;
        }
      }

      setCamera(proj, camMx, camMy, proj.ox, proj.oy);

      const headGcj = wgs84ToGcj02(pose.lng, pose.lat);
      const headMX = mercX(headGcj.lng);
      const headMY = mercY(headGcj.lat);
      const headScreenX = proj.ox + (headMX - proj.cx) * proj.scaleX;
      const headScreenY = proj.oy + (headMY - proj.cy) * proj.scaleY;

      // 照片卡片：每帧只**写** DOM（display / transform / opacity），不读任何几何。
      // 顺序红线：本帧唯一的布局读 syncSize() 在帧首；从这里到帧尾全是写，
      // 绝不能插入 getBoundingClientRect / offsetWidth / getComputedStyle ——
      // 展示站那个 p90 32.8ms 的强制同步重排就是这么来的（stage9 §6.4 第五步）。
      const cards = cardsRef.current;
      if (cards) {
        cards.update(scene, proj, pose.s, !clock.playing, now, headScreenX, headScreenY);
        drawState.anchors = cards.anchors;
        drawState.anchorCount = scene.photoCount;
      } else {
        drawState.anchors = EMPTY_ANCHORS;
        drawState.anchorCount = 0;
      }

      drawState.proj = proj;
      drawState.progressS = pose.s;
      drawState.nowMs = now;
      drawState.fallback = isFallback;
      drawState.headMX = headMX;
      drawState.headMY = headMY;
      renderer.draw(drawState);

      // 进度 UI：4 帧节流，直接写 DOM，不经过 React
      if ((frameCount & 3) === 0) {
        writeProgress(clock.q);
        const toggle = toggleRef.current;
        if (toggle) {
          const flag = clock.playing ? 'true' : 'false';
          if (toggle.dataset.playing !== flag) {
            toggle.dataset.playing = flag;
            // aria-label 也得跟着变，否则读屏永远念"播放或暂停"
            toggle.setAttribute('aria-label', clock.playing ? '暂停' : '播放');
          }
        }
      }
    };

    raf = window.requestAnimationFrame(frame);
    window.addEventListener('resize', syncSize);

    return () => {
      disposed = true;
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', syncSize);
      controller?.destroy();
      controller = null;
      if (mapHost.parentElement) mapHost.parentElement.removeChild(mapHost);
      clock.pause();
    };
  }, [journey, clock]);

  const seekTo = (clientX: number): void => {
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const ratio = rect.width > 0 ? (clientX - rect.left) / rect.width : 0;
    clock.seek(ratio);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>): void => {
    dragRef.current = true;
    resumeRef.current = clock.playing;
    clock.pause();
    seekTo(event.clientX);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!dragRef.current) return;
    seekTo(event.clientX);
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!dragRef.current) return;
    dragRef.current = false;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      /* 指针可能已释放 */
    }
    if (resumeRef.current) clock.play();
  };

  /**
   * WCAG 2.1.1：`role="slider"` 必须能用键盘操作。
   * 键位按 ARIA Authoring Practices 的标准 slider 来：
   * 方向键 ±5%（Shift ±1% 精调）、PageUp/PageDown ±10%、Home/End 到两端。
   */
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    const next = sliderStep(event.key, event.shiftKey, clock.q);
    if (next === null) return;
    // 必须拦：方向键/PageUp/Home 默认会滚动容器
    event.preventDefault();
    clock.seek(next);
    writeProgress(clock.q);
  };

  return (
    <div className="replay-root">
      <div className="replay-map" ref={hostRef} />
      <canvas className="replay-canvas" ref={canvasRef} />
      <PhotoCards ref={cardsRef} journey={journey} thumbs={thumbs ?? undefined} />

      <div className="replay-top">
        <div className="replay-heading">
          <span className="replay-eyebrow">轨迹回放</span>
          <h2 className="replay-title">{title}</h2>
          {subtitle ? <span className="replay-sub">{subtitle}</span> : null}
        </div>
        <button type="button" className="replay-close" aria-label="关闭回放" onClick={onClose}>
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="replay-bottom">
        <button
          type="button"
          ref={toggleRef}
          className="replay-toggle"
          data-playing="true"
          aria-label="暂停"
          onClick={() => clock.toggle()}
        >
          <Play className="replay-icon-play h-4 w-4" />
          <Pause className="replay-icon-pause h-4 w-4" />
        </button>

        <div
          className="replay-track"
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label="播放进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={0}
          aria-valuetext={ariaValueText(0)}
          onKeyDown={handleKeyDown}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onLostPointerCapture={handlePointerUp}
        >
          <div className="replay-track-base" />
          <div className="replay-track-fill" ref={fillRef} />
        </div>

        <span className="replay-percent" ref={pctRef}>
          0%
        </span>
      </div>
    </div>
  );
}

/** 数据不足时的提示。不做假数据，也不画半条轨迹。 */
function ReplayUnavailable({ onClose }: { onClose: () => void }) {
  return (
    <div className="replay-root replay-empty">
      <p>这条行程里可定位的照片不足 2 张，画不出轨迹。</p>
      <p className="replay-empty-hint">在行程里补上地点后再回来看看。</p>
      <button type="button" className="replay-close replay-empty-close" onClick={onClose}>
        关闭
      </button>
    </div>
  );
}

export function ReplayStage({ trip, photos, open, onOpenChange }: ReplayStageProps) {
  const journey = useMemo(
    () => (trip ? buildReplayJourney(trip, photos) : null),
    [trip, photos],
  );

  if (!trip) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // `block` 覆盖 DialogContent 默认的 `grid`：默认 grid 只有一行 auto，
        // 子元素 `height:100%` 会解析成 auto（而 .replay-root 的子节点全是绝对定位 → 高度塌成 0）。
        // `[&>button]:hidden` 收掉组件自带的 X —— 关闭按钮由顶栏提供，避免两个重叠。
        // `aria-describedby={undefined}` 显式声明"没有描述"，否则 Radix 会在 dev 控制台告警。
        aria-describedby={undefined}
        className="left-0 top-0 block h-[100dvh] w-screen max-w-none translate-x-0 translate-y-0 rounded-none border-0 bg-[#05080f] p-0 text-[#e6f1ff] [&>button]:hidden"
      >
        <DialogTitle className="sr-only">{trip.title} 回放</DialogTitle>
        {journey ? (
          <ReplayCanvas
            journey={journey}
            title={trip.title}
            subtitle={journey.subtitle}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <ReplayUnavailable onClose={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  );
}
