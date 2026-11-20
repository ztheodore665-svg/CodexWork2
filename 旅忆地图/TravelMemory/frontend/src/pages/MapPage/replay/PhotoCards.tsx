/** 回放时沿途浮出的照片卡片（主应用版）。
 *
 * 从 `showcase/src/ui/PhotoCards.tsx` 移植，布局与避让算法一字未改 —— 它本来就没有
 * showcase 专属依赖，只 import 共享包（`@travelmemory/trail` / `geo`）。
 * 两份组件各自留在宿主里，因为 JSX + CSS 是应用专属语义，按 PROJECT.md 的判据不进 packages。
 *
 * ## 唯一新增的东西：`thumbs` 映射
 *
 * 主应用的 `Photo.thumb` 不来自后端（后端只存元数据，缩略图在 IndexedDB），
 * 所以卡片接受一张 `photoId → objectURL` 的表；查不到就退回 `photo.thumb`，
 * 再没有就用 `gradient` 占位。这样**不用改 Journey 对象、不用改 adapter、不用重建主循环**，
 * 缩略图异步到达时只触发一次 React 重渲染。
 *
 * ## 性能红线：update() 里绝不读布局
 *
 * 这个函数每帧被主循环调用，写完 32 个节点的 `display/transform/opacity` 之后，
 * 只要有一个 `getBoundingClientRect` / `offsetWidth` / `getComputedStyle`，
 * 浏览器就得同步重排一次 —— 展示站曾因此 p90 卡在 32.8ms（每 3 帧丢 1 帧）。
 * 所有几何量都从 `proj / scene` 参数纯算术算出来。契约测试见 `photoCards.test.ts`。
 */

import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import {
  ANCHOR_STRIDE,
  formatDateDot,
  formatTimeHM,
  type Journey,
  type Projection,
  type Scene,
} from '@travelmemory/trail';
import { clamp } from '@travelmemory/geo';

export interface PhotoCardsHandle {
  /** 每帧调用：只写 style.display / transform / opacity，其余属性一概不动 */
  update(
    scene: Scene,
    proj: Projection,
    progressS: number,
    paused: boolean,
    nowMs: number,
    headX: number,
    headY: number,
  ): void;
  /** 供渲染器画引线用的锚点（ax, ay, cx, cy, visible） */
  readonly anchors: Float32Array;
}

interface PhotoCardsProps {
  journey: Journey;
  /** photoId → 本机缩略图的 objectURL。缺省时全走 gradient 兜底。 */
  thumbs?: ReadonlyMap<string, string>;
}

const CARD_W = 132;
const CARD_H = 168;
/** 出画范围外就不渲染（屏幕外 260px） */
const VIEW_MARGIN = 260;
/** 卡片安全区：底部留出让开播放控制栏，避免卡片底边被切掉 */
const SAFE_TOP = 56;
const SAFE_BOTTOM = 92;
const SAFE_LEFT = 40;
const SAFE_RIGHT = 40;
/** 卡片中心与光点的最小距离，保证任何角度都不会把光晕盖住 */
const MIN_HEAD_DIST = CARD_H * 0.95;
/** 首次越过后至少停留这么久，否则 4x 速度下卡片只会闪一下 */
const LINGER_MS = 2600;
const REVEAL_MS = 320;
const FADE_MS = 900;
/** 同屏卡片密度上限：少而清楚，好过多而糊成一坨 */
const MAX_CARDS = 5;

/** 轻微过冲缓动：easeOutBack */
function easeOutBack(x: number, overshoot = 1.15): number {
  const c3 = overshoot + 1;
  const t = x - 1;
  return 1 + c3 * t * t * t + overshoot * t * t;
}

export const PhotoCards = forwardRef<PhotoCardsHandle, PhotoCardsProps>(function PhotoCards(
  { journey, thumbs },
  ref,
) {
  const count = journey.photos.length;
  const nodes = useRef<(HTMLDivElement | null)[]>([]);
  const crossedAt = useRef<Float64Array>(new Float64Array(count));
  const anchors = useRef<Float32Array>(new Float32Array(Math.max(1, count) * ANCHOR_STRIDE));
  /** 本轮可见卡片的布局（stride 4：cx, cy, opacity, scale），预分配，主循环内零 GC */
  const layout = useRef<Float64Array>(new Float64Array(Math.max(1, count) * 4));
  /** layout 里每个槽位对应的照片下标 */
  const shownIdx = useRef<Int32Array>(new Int32Array(Math.max(1, count)));

  useEffect(() => {
    crossedAt.current = new Float64Array(count);
    anchors.current = new Float32Array(Math.max(1, count) * ANCHOR_STRIDE);
    layout.current = new Float64Array(Math.max(1, count) * 4);
    shownIdx.current = new Int32Array(Math.max(1, count));
    nodes.current = nodes.current.slice(0, count);
    for (let i = 0; i < count; i += 1) {
      const el = nodes.current[i];
      if (el) el.style.display = 'none';
    }
  }, [count, journey.id]);

  useImperativeHandle(
    ref,
    () => ({
      get anchors() {
        return anchors.current;
      },
      update(
        scene: Scene,
        proj: Projection,
        progressS: number,
        paused: boolean,
        nowMs: number,
        headX: number,
        headY: number,
      ) {
        const total = scene.photoCount;
        if (total === 0) return;

        // 当前最近的站点（暂停时的取景窗口用）
        let nearest = 0;
        let nearestGap = Number.POSITIVE_INFINITY;
        for (let i = 0; i < total; i += 1) {
          const gap = Math.abs(scene.photoS[i] - progressS);
          if (gap < nearestGap) {
            nearestGap = gap;
            nearest = i;
          }
        }

        const anchorData = anchors.current;
        const slots = layout.current;
        const slotIndex = shownIdx.current;

        const viewW = proj.ox * 2;
        const viewH = proj.oy * 2;
        const minCx = SAFE_LEFT + CARD_W / 2;
        const maxCx = viewW - SAFE_RIGHT - CARD_W / 2;
        const minCy = SAFE_TOP + CARD_H / 2;
        const maxCy = viewH - SAFE_BOTTOM - CARD_H / 2;
        let shown = 0;

        // ── 第一遍：可见性判定 + 初始位置（黄金角 → 避让光点 → 夹取） ──
        for (let i = 0; i < total; i += 1) {
          const el = nodes.current[i];
          const base = i * ANCHOR_STRIDE;
          const s = scene.photoS[i];
          const delta = progressS - s;

          let reveal = 0;
          if (delta >= 0) {
            if (crossedAt.current[i] === 0) crossedAt.current[i] = nowMs;
            const elapsed = Math.max(0, nowMs - crossedAt.current[i]);
            if (elapsed <= REVEAL_MS) {
              reveal = elapsed / REVEAL_MS;
            } else if (elapsed <= REVEAL_MS + LINGER_MS) {
              reveal = 1;
            } else {
              reveal = Math.max(0, 1 - (elapsed - REVEAL_MS - LINGER_MS) / FADE_MS);
            }
          } else {
            crossedAt.current[i] = 0;
          }

          const lingering =
            crossedAt.current[i] > 0 &&
            nowMs - crossedAt.current[i] < REVEAL_MS + LINGER_MS + FADE_MS;
          const rank = Math.abs(i - nearest);
          const rankNear = rank <= (paused ? 3 : 2);
          const visible = rankNear || lingering;

          if (!el) continue;

          const ax = proj.ox + (scene.photoMX[i] - proj.cx) * proj.scaleX;
          const ay = proj.oy + (scene.photoMY[i] - proj.cy) * proj.scaleY;

          const onScreen =
            ax > -VIEW_MARGIN &&
            ax < viewW + VIEW_MARGIN &&
            ay > -VIEW_MARGIN &&
            ay < viewH + VIEW_MARGIN;

          const dim = rankNear ? Math.max(0.36, 0.72 - rank * 0.12) : 0;
          const crossedOnce = crossedAt.current[i] > 0;
          const effectiveReveal = crossedOnce
            ? Math.max(reveal, paused && rankNear ? dim : 0)
            : dim;

          if (!visible || !onScreen || effectiveReveal <= 0.005) {
            if (el.style.display !== 'none') el.style.display = 'none';
            anchorData[base + 4] = 0;
            continue;
          }
          if (el.style.display !== 'block') el.style.display = 'block';

          const angle = i * 2.399963;
          const radius = 96 + (i % 3) * 26;
          let cx = ax + Math.cos(angle) * radius;
          let cy = ay + Math.sin(angle) * radius * 0.72;

          const awayX = cx - headX;
          const awayY = cy - headY;
          const awayDist = Math.hypot(awayX, awayY);
          if (awayDist < MIN_HEAD_DIST) {
            const pushAngle = awayDist < 1e-3 ? angle : Math.atan2(awayY, awayX);
            cx = headX + Math.cos(pushAngle) * MIN_HEAD_DIST;
            cy = headY + Math.sin(pushAngle) * MIN_HEAD_DIST;
          }

          cx = clamp(cx, minCx, maxCx);
          cy = clamp(cy, minCy, maxCy);

          const eased = easeOutBack(reveal);
          const scale = 0.88 + 0.12 * (reveal > 0 ? Math.max(0, eased) : effectiveReveal);

          const slot = shown * 4;
          slots[slot] = cx;
          slots[slot + 1] = cy;
          slots[slot + 2] = effectiveReveal;
          slots[slot + 3] = scale;
          slotIndex[shown] = i;
          shown += 1;
        }

        // ── 密度截断：留下离光点最近的 MAX_CARDS 张 ──
        while (shown > MAX_CARDS) {
          let far = 0;
          let farD = -1;
          for (let n = 0; n < shown; n += 1) {
            const s0 = n * 4;
            const ddx = slots[s0] - headX;
            const ddy = slots[s0 + 1] - headY;
            const d = ddx * ddx + ddy * ddy;
            if (d > farD) {
              farD = d;
              far = n;
            }
          }
          const fs = far * 4;
          const ls = (shown - 1) * 4;
          for (let k = 0; k < 4; k += 1) {
            const tmp = slots[fs + k];
            slots[fs + k] = slots[ls + k];
            slots[ls + k] = tmp;
          }
          const ti = slotIndex[far];
          slotIndex[far] = slotIndex[shown - 1];
          slotIndex[shown - 1] = ti;
          shown -= 1;
        }

        // ── 第二遍：成对分离（3 轮足够收敛） ──
        for (let round = 0; round < 3; round += 1) {
          for (let a = 0; a < shown; a += 1) {
            const sa = a * 4;
            for (let b = a + 1; b < shown; b += 1) {
              const sb = b * 4;
              const dx = slots[sb] - slots[sa];
              const dy = slots[sb + 1] - slots[sa + 1];
              const overlapX = CARD_W * 0.92 - Math.abs(dx);
              const overlapY = CARD_H * 0.92 - Math.abs(dy);
              if (overlapX <= 0 || overlapY <= 0) continue;
              if (overlapX < overlapY) {
                const push = (overlapX / 2 + 1) * (dx >= 0 ? 1 : -1);
                slots[sa] -= push;
                slots[sb] += push;
              } else {
                const push = (overlapY / 2 + 1) * (dy >= 0 ? 1 : -1);
                slots[sa + 1] -= push;
                slots[sb + 1] += push;
              }
            }
          }
          for (let n = 0; n < shown; n += 1) {
            const slot = n * 4;
            slots[slot] = clamp(slots[slot], minCx, maxCx);
            slots[slot + 1] = clamp(slots[slot + 1], minCy, maxCy);
          }
        }

        // ── 第三遍：最终避让 + 夹取，然后写 DOM 与引线锚点 ──
        for (let n = 0; n < shown; n += 1) {
          const slot = n * 4;
          const i = slotIndex[n];
          const el = nodes.current[i];
          if (!el) continue;

          let cx = slots[slot];
          let cy = slots[slot + 1];
          const awayX = cx - headX;
          const awayY = cy - headY;
          const awayDist = Math.hypot(awayX, awayY);
          if (awayDist < MIN_HEAD_DIST) {
            const pushAngle = awayDist < 1e-3 ? i * 2.399963 : Math.atan2(awayY, awayX);
            cx = headX + Math.cos(pushAngle) * MIN_HEAD_DIST;
            cy = headY + Math.sin(pushAngle) * MIN_HEAD_DIST;
          }
          cx = clamp(cx, minCx, maxCx);
          cy = clamp(cy, minCy, maxCy);

          el.style.transform =
            'translate3d(' +
            (cx - CARD_W / 2).toFixed(1) +
            'px,' +
            (cy - CARD_H / 2).toFixed(1) +
            'px,0) scale(' +
            (slots[slot + 3] as number).toFixed(3) +
            ')';
          el.style.opacity = (slots[slot + 2] as number).toFixed(3);

          const base = i * ANCHOR_STRIDE;
          anchorData[base] = proj.ox + (scene.photoMX[i] - proj.cx) * proj.scaleX;
          anchorData[base + 1] = proj.oy + (scene.photoMY[i] - proj.cy) * proj.scaleY;
          anchorData[base + 2] = cx;
          anchorData[base + 3] = cy;
          anchorData[base + 4] = 1;
        }

        // ── 收尾对账：本帧没进入 slots 的卡片必须隐藏 ──
        for (let i = 0; i < total; i += 1) {
          let kept = false;
          for (let n = 0; n < shown; n += 1) {
            if (slotIndex[n] === i) {
              kept = true;
              break;
            }
          }
          if (kept) continue;
          const el = nodes.current[i];
          if (el && el.style.display !== 'none') el.style.display = 'none';
          anchorData[i * ANCHOR_STRIDE + 4] = 0;
        }
      },
    }),
    [],
  );

  return (
    <>
      {journey.photos.map((photo, index) => {
        const thumb = thumbs?.get(photo.id) ?? photo.thumb;
        return (
          <div
            key={photo.id}
            className="card"
            ref={(node) => {
              nodes.current[index] = node;
            }}
          >
            <div
              className="card-media"
              style={{ backgroundImage: thumb ? `url(${thumb})` : photo.gradient }}
            />
            <div className="card-body">
              <div className="card-place">{photo.placeName ?? '未命名地点'}</div>
              <div className="card-date">
                {formatDateDot(photo.takenAt)} <span style={{ opacity: 0.5 }}>{formatTimeHM(photo.takenAt)}</span>
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
});
