/**
 * 轨迹平滑：向心 Catmull-Rom 样条 + 等弧长重采样。
 *
 * 用向心参数化（α = 0.5）而不是均匀参数化：均匀参数在急转弯处会过冲、自交，
 * 向心参数能保证不打结。
 */

export interface Pt {
  x: number;
  y: number;
}

export interface ArcPoint {
  x: number;
  y: number;
  s: number;
}

function dist2d(a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function lerpPt(a: Pt, b: Pt, ta: number, tb: number, t: number): Pt {
  const span = tb - ta;
  if (span <= 0) return { x: a.x, y: a.y };
  const u = (t - ta) / span;
  return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u };
}

/**
 * 向心 Catmull-Rom。首尾各复制一次端点作为虚拟控制点。
 * @param points 控制点
 * @param samplesPerSegment 每段采样数
 */
export function catmullRomCentripetal(points: Pt[], samplesPerSegment = 24): Pt[] {
  if (points.length === 0) return [];
  if (points.length === 1) return [{ x: points[0].x, y: points[0].y }];

  const alpha = 0.5;
  const padded: Pt[] = [points[0], ...points, points[points.length - 1]];
  const out: Pt[] = [];
  const steps = Math.max(1, samplesPerSegment);

  for (let i = 1; i < padded.length - 2; i += 1) {
    const p0 = padded[i - 1];
    const p1 = padded[i];
    const p2 = padded[i + 1];
    const p3 = padded[i + 2];

    const t0 = 0;
    const t1 = t0 + Math.pow(dist2d(p0, p1), alpha);
    const t2 = t1 + Math.pow(dist2d(p1, p2), alpha);
    const t3 = t2 + Math.pow(dist2d(p2, p3), alpha);

    // 退化（重合点）时 t 区间长度为 0，直接落一个点，避免除零
    if (t2 - t1 <= 1e-12) {
      out.push({ x: p1.x, y: p1.y });
      continue;
    }

    for (let j = 0; j < steps; j += 1) {
      const t = t1 + (t2 - t1) * (j / steps);
      const a1 = lerpPt(p0, p1, t0, t1, t);
      const a2 = lerpPt(p1, p2, t1, t2, t);
      const a3 = lerpPt(p2, p3, t2, t3, t);
      const b1 = lerpPt(a1, a2, t0, t2, t);
      const b2 = lerpPt(a2, a3, t1, t3, t);
      out.push(lerpPt(b1, b2, t1, t2, t));
    }
  }

  const last = points[points.length - 1];
  out.push({ x: last.x, y: last.y });
  return out;
}

/**
 * 等弧长重采样。输出的 s 是「输入坐标单位」下的累计长度，
 * 调用方拿到后通常会用真实经纬度距离覆盖它。
 */
export function resampleByArcLength(pts: Pt[], count: number): ArcPoint[] {
  const n = pts.length;
  if (n === 0) return [];
  const target = Math.max(2, Math.floor(count));
  if (n === 1) {
    return [{ x: pts[0].x, y: pts[0].y, s: 0 }];
  }

  const cumulative = new Float64Array(n);
  cumulative[0] = 0;
  for (let i = 1; i < n; i += 1) {
    cumulative[i] = cumulative[i - 1] + dist2d(pts[i - 1], pts[i]);
  }
  const total = cumulative[n - 1];
  if (total <= 0) {
    const out: ArcPoint[] = [];
    for (let i = 0; i < target; i += 1) {
      out.push({ x: pts[0].x, y: pts[0].y, s: 0 });
    }
    return out;
  }

  const out: ArcPoint[] = [];
  let cursor = 0;
  for (let k = 0; k < target; k += 1) {
    const wanted = (total * k) / (target - 1);
    while (cursor < n - 2 && cumulative[cursor + 1] < wanted) {
      cursor += 1;
    }
    const segLen = cumulative[cursor + 1] - cumulative[cursor];
    const ratio = segLen <= 0 ? 0 : (wanted - cumulative[cursor]) / segLen;
    const a = pts[cursor];
    const b = pts[cursor + 1];
    out.push({
      x: a.x + (b.x - a.x) * ratio,
      y: a.y + (b.y - a.y) * ratio,
      s: wanted,
    });
  }
  return out;
}
