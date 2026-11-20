/** 照片卡片层（阶段 13 / W13-2：从 `showcase/src/ui/PhotoCards.tsx` **复制**）。
 *
 * 逻辑一行未改 —— 黄金角散布、避让光点、密度截断（`MAX_CARDS`）、成对分离、
 * 引线锚点这些都是 showcase 里按真机截图调过的参数，重写只会更差。
 * 只把它挪进 `theme-dark/` 成为独立副本（本仓库红线禁止 app import showcase）。
 * 卡片配色来自 `.theme-dark .card*`（见 `theme-dark/styles.css`）。
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
  /** 每帧调用：只写 style.transform / style.opacity，其余属性一概不动 */
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
}

const CARD_W = 132;
const CARD_H = 168;
/** 出画范围外就不渲染（屏幕外 260px） */
const VIEW_MARGIN = 260;
/**
 * 卡片安全区（相对视口的边距）。底部 92 让开 68px 时间轴 + 余量，
 * 否则卡片底边会被时间轴切掉（实测出现过 y=833、高 155、底边 988 > 900）。
 */
const SAFE_TOP = 56;
const SAFE_BOTTOM = 92;
const SAFE_LEFT = 40;
const SAFE_RIGHT = 40;
/** 卡片中心与光点的最小距离。卡片半对角线 = √(66²+84²) ≈ 107 < 159.6，任何角度都不会包住光点 */
const MIN_HEAD_DIST = CARD_H * 0.95;
/** 首次越过后至少停留这么久，否则 4x 速度下卡片只会闪一下 */
const LINGER_MS = 2600;
const REVEAL_MS = 320;
/** 停留结束后再淡出这么久，然后再交给「名次窗口」决定去留 */
const FADE_MS = 900;
/**
 * 同屏卡片密度上限。
 * 起步段西宁→塔尔寺→日月山→青海湖都在 120km 内，在 250km 视口里只占约 700px 宽，
 * 8 张 132×168 的卡片塞进去必然互压（实测两张「塔尔寺」叠在一起、文字互遮）。
 * 超过就只留离光点最近的几张——少而清楚，好过多而糊成一坨。
 */
const MAX_CARDS = 5;

/** 轻微过冲缓动：easeOutBack */
function easeOutBack(x: number, overshoot = 1.15): number {
  const c3 = overshoot + 1;
  const t = x - 1;
  return 1 + c3 * t * t * t + overshoot * t * t;
}

export const PhotoCards = forwardRef<PhotoCardsHandle, PhotoCardsProps>(function PhotoCards(
  { journey },
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
              reveal = elapsed / REVEAL_MS; // 浮出
            } else if (elapsed <= REVEAL_MS + LINGER_MS) {
              reveal = 1; // 停留
            } else {
              // 淡走：否则越过的卡片会一直满不透明挂在屏幕上，永不退场
              reveal = Math.max(0, 1 - (elapsed - REVEAL_MS - LINGER_MS) / FADE_MS);
            }
          } else {
            crossedAt.current[i] = 0;
          }

          const lingering =
            crossedAt.current[i] > 0 &&
            nowMs - crossedAt.current[i] < REVEAL_MS + LINGER_MS + FADE_MS;
          // 按名次取窗口，不用米数阈值：照片密度与总里程是解耦的，
          // 900m 在 2000km/42s 的线上只存在 19ms（4x 时更短），等于永远不成立。
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

          // 还没抵达的照片给一个随名次衰减的淡态，让"沿途一张张浮出来"提前可见。
          // 透明度必须够高：卡片底色是 rgba(8,14,24,.72)、渐变也是近黑色，
          // 叠在黑色底图上时 0.45 以下等于隐形（实测就是这样"看不见卡片"）。
          const dim = rankNear ? Math.max(0.36, 0.72 - rank * 0.12) : 0;
          const crossedOnce = crossedAt.current[i] > 0;
          // 已越过的卡片 reveal 在 3820ms 后恒为 0。若不兜底，暂停/播完时「正对着的那张」
          // 会永久 display:none（名次窗口对它失效，因为它走 reveal 分支而不是 dim 分支）。
          // 用 max 而不是直接赋值，避免暂停瞬间从 1 掉到 0.72 的跳变。
          const effectiveReveal = crossedOnce
            ? Math.max(reveal, paused && rankNear ? dim : 0)
            : dim;

          if (!visible || !onScreen || effectiveReveal <= 0.005) {
            if (el.style.display !== 'none') el.style.display = 'none';
            anchorData[base + 4] = 0;
            continue;
          }
          if (el.style.display !== 'block') el.style.display = 'block';

          // 黄金角散布
          const angle = i * 2.399963;
          const radius = 96 + (i % 3) * 26;
          let cx = ax + Math.cos(angle) * radius;
          let cy = ay + Math.sin(angle) * radius * 0.72;

          // ① 先避开光点：卡片压在播放头上会让光晕完全看不见（实测 80% 时刻就是如此）
          const awayX = cx - headX;
          const awayY = cy - headY;
          const awayDist = Math.hypot(awayX, awayY);
          if (awayDist < MIN_HEAD_DIST) {
            const pushAngle = awayDist < 1e-3 ? angle : Math.atan2(awayY, awayX);
            cx = headX + Math.cos(pushAngle) * MIN_HEAD_DIST;
            cy = headY + Math.sin(pushAngle) * MIN_HEAD_DIST;
          }

          // ② 再夹取进安全区（顺序不能反：夹取后再避让会又把卡片推出视口）。
          // 底部 92px 让开时间轴，否则卡片底边会被切掉。
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
        // 选择排序把最远的逐个换到末尾再截断；shown ≤ 10，O(n²) 可忽略，且零分配。
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

        // ── 第二遍：成对分离 ──
        // 夹取会把多张卡片推到同一条安全边线上，导致文字互压（实测同景点两张重叠 70%）。
        // 3 轮足够收敛；每轮结束后重新夹取，避免被推出安全区。
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
              // 沿重叠较小的轴推开（位移最短）
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
        // 第一遍为了让 anchorData 正确会先 display:block，密度截断之后被踢出的那几张
        // 会残留上一帧的位置，表现为「多出几张不动的卡片」。这里做一次对账
        // （total×shown ≤ 32×5 = 160 次比较，可忽略）。
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
      {journey.photos.map((photo, index) => (
        <div
          key={photo.id}
          className="card"
          ref={(node) => {
            nodes.current[index] = node;
          }}
        >
          <div
            className="card-media"
            style={{ backgroundImage: photo.thumb ? `url(${photo.thumb})` : photo.gradient }}
          />
          <div className="card-body">
            <div className="card-place">{photo.placeName ?? '未命名地点'}</div>
            <div className="card-date">
              {formatDateDot(photo.takenAt)} <span style={{ opacity: 0.5 }}>{formatTimeHM(photo.takenAt)}</span>
            </div>
          </div>
        </div>
      ))}
    </>
  );
});
