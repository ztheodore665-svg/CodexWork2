/**
 * Journey 构建：Photo[] → 平滑轨迹 / 分天 / 统计。
 *
 * 关键点：里程必须用真实经纬度做 haversine 累加，不能用墨卡托平面距离，
 * 否则高纬地区的里程会明显偏大（墨卡托在纬度方向上是拉伸的）。
 */

import type { Journey, JourneyDay, JourneySegment, Photo, TrackSample } from './types';
import { clamp, haversine, invMercX, invMercY, mercX, mercY } from '@travelmemory/geo';
import { catmullRomCentripetal, resampleByArcLength, type Pt } from './spline';
import { formatDayMonth } from './format';
import { DEFAULT_TRANSPORT, normalizeTransport, type TransportMode } from './transport';

export interface BuildJourneyInput {
  id: string;
  title: string;
  subtitle: string;
  photos: Photo[];
}

/** 播放时的定位姿态（复用同一个对象，避免每帧分配） */
export interface Pose {
  s: number;
  t: number;
  lng: number;
  lat: number;
  mx: number;
  my: number;
  index: number;
}

export function createPose(): Pose {
  return { s: 0, t: 0, lng: 0, lat: 0, mx: 0, my: 0, index: 0 };
}

interface LocatedPhoto {
  photo: Photo;
  lat: number;
  lng: number;
  takenAt: number;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

/**
 * 建议缩放级别：让相邻站点的中位间距大约占屏幕宽度的 35%。
 *
 * 注意（相对需求的修正）：需求里的公式带了一个多余的 `/ 256`。
 * 156543.03392 已经是「zoom 0 时、单瓦片 256px 下赤道每像素米数」，
 * 再除 256 会让结果恒定被 clamp 到 7.5。这里用标准形式：
 *   mpp = 156543.03392 * cos(lat) / 2^zoom
 *   2^zoom = 156543.03392 * cos(lat) * 0.35 * screenW / dMedian
 */
/**
 * 同一地点判定阈值（米）。
 *
 * 这里是 P0 级别的关键常量：景点通常一拍就是好几张，抖动在 1km 量级。
 * 如果按 50m 去重，同一个景点的 2 张照片会被算成 2 个"站点"，
 * 站间距中位数被拉到 1km 左右，`suggestedZoom` 直接顶到 13（视野只有 22km），
 * 结果是：照片卡片全在屏幕外、镜头滞后被放大到 500px+、底图每帧平移 2600px 导致瓦片全被取消。
 */
const STATION_MERGE_METERS = 2000;

/**
 * 建议缩放级别：让相邻站点的中位间距大约占屏幕宽度的 35%。
 *
 * 注意（相对需求的修正）：需求里的公式带了一个多余的 `/ 256`。
 * 156543.03392 已经是「zoom 0 时、单瓦片 256px 下赤道每像素米数」，
 * 再除 256 会让结果恒定被 clamp 到 7.5。这里用标准形式：
 *   mpp = 156543.03392 * cos(lat) / 2^zoom
 *   2^zoom = 156543.03392 * cos(lat) * 0.35 * screenW / dMedian
 */
function computeSuggestedZoom(lngLat: { lng: number; lat: number }[], latitudes: number[]): number {
  const stations: { lng: number; lat: number }[] = [];
  for (const point of lngLat) {
    const last = stations[stations.length - 1];
    if (!last) {
      stations.push(point);
      continue;
    }
    if (haversine(last.lat, last.lng, point.lat, point.lng) > STATION_MERGE_METERS) {
      stations.push(point);
    }
  }

  const gaps: number[] = [];
  for (let i = 1; i < stations.length; i += 1) {
    gaps.push(haversine(stations[i - 1].lat, stations[i - 1].lng, stations[i].lat, stations[i].lng));
  }
  if (gaps.length === 0) return 11;

  const dMedian = median(gaps);
  const latRef = radians(median(latitudes));
  if (dMedian <= 0) return 11;

  const screenW = 1440;
  const zoom = Math.log2((0.35 * 156543.03392 * Math.cos(latRef) * screenW) / dMedian);
  const clamped = clamp(zoom, 7.5, 13);

  // 撞边界 = 建议视距不可信（数据太密或太疏）。这次的 P0 就是静默撞上界 13：
  // 同景点两张照片被当成两站，dMedian 从 110km 塌到 1km，画面全废而无人知晓。
  // 只 warn 不抛，生产包被 tree-shake。
  if (import.meta.env.DEV && clamped !== zoom) {
    console.warn('[journey] suggestedZoom 撞到 clamp 边界', {
      zoom,
      clamped,
      dMedianKm: Math.round(dMedian / 1000),
      stations: stations.length,
      photos: lngLat.length,
    });
  }

  return clamp(Math.round(clamped * 2) / 2, 7.5, 13);
}

function radians(deg: number): number {
  return (deg * Math.PI) / 180;
}

function roundZoom(value: number): number {
  return clamp(Math.round(value * 2) / 2, 7.5, 13);
}

export function buildJourney(input: BuildJourneyInput): Journey {
  const located: LocatedPhoto[] = [];
  for (const photo of input.photos) {
    if (photo.lat === null || photo.lng === null) continue;
    if (!Number.isFinite(photo.lat) || !Number.isFinite(photo.lng)) continue;
    located.push({ photo, lat: photo.lat, lng: photo.lng, takenAt: photo.takenAt });
  }
  located.sort((a, b) => a.takenAt - b.takenAt);

  const photos = located.map((item) => item.photo);

  let minLng = 0;
  let minLat = 0;
  let maxLng = 0;
  let maxLat = 0;
  if (located.length > 0) {
    minLng = located[0].lng;
    maxLng = located[0].lng;
    minLat = located[0].lat;
    maxLat = located[0].lat;
    for (const item of located) {
      if (item.lng < minLng) minLng = item.lng;
      if (item.lng > maxLng) maxLng = item.lng;
      if (item.lat < minLat) minLat = item.lat;
      if (item.lat > maxLat) maxLat = item.lat;
    }
  }

  // 只算一次，两条返回路径共用
  const zoom = located.length > 1 ? computeSuggestedZoom(located, located.map((p) => p.lat)) : 11;

  const base: Journey = {
    id: input.id,
    title: input.title,
    subtitle: input.subtitle,
    photos,
    track: [],
    totalMeters: 0,
    days: [],
    bbox: { minLng, minLat, maxLng, maxLat },
    suggestedZoom: zoom,
    // 退化路径（少于 2 张已定位照片）没有轨迹，自然也没有路段。
    // 渲染器对空数组有兜底：取不到路段时一律按默认交通工具（自驾）描边。
    segments: [],
  };

  // 少于 2 张已定位照片 → 没有轨迹可言，交给 UI 提示
  if (located.length < 2) {
    if (located.length === 1) {
      base.days = [
        {
          index: 0,
          label: formatDayMonth(located[0].takenAt),
          startS: 0,
          endS: 0,
        },
      ];
    }
    return base;
  }

  // ── 1. 控制点 → 向心样条 ─────────────────────────────────────
  const control: Pt[] = located.map((item) => ({ x: mercX(item.lng), y: mercY(item.lat) }));
  const smoothed = catmullRomCentripetal(control, 24);
  const sampleCount = Math.round(clamp(smoothed.length * 24, 400, 2400));
  const arc = resampleByArcLength(smoothed, sampleCount);

  // ── 2. 墨卡托 → 经纬度 → 真实米数 ───────────────────────────
  const track: TrackSample[] = new Array(arc.length);
  let previousLng = 0;
  let previousLat = 0;
  let accumulated = 0;
  for (let i = 0; i < arc.length; i += 1) {
    const lng = invMercX(arc[i].x);
    const lat = invMercY(arc[i].y);
    if (i > 0) {
      accumulated += haversine(previousLat, previousLng, lat, lng);
    }
    track[i] = { mx: arc[i].x, my: arc[i].y, s: accumulated, t: 0 };
    previousLng = lng;
    previousLat = lat;
  }
  const totalMeters = Math.max(accumulated, 0);

  // ── 3. 每张照片在轨迹上的弧长位置（取最近的采样点） ──────────
  const photoS: number[] = new Array(located.length);
  for (let k = 0; k < located.length; k += 1) {
    const px = mercX(located[k].lng);
    const py = mercY(located[k].lat);
    let bestIndex = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let i = 0; i < track.length; i += 1) {
      const dx = track[i].mx - px;
      const dy = track[i].my - py;
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = i;
      }
    }
    photoS[k] = track[bestIndex].s;
  }

  // ── 4. 采样点时间：按弧长比例在两站之间线性插值 ──────────────
  for (let i = 0; i < track.length; i += 1) {
    const s = track[i].s;
    let k = 0;
    while (k < located.length - 2 && photoS[k + 1] < s) k += 1;
    const s0 = photoS[k];
    const s1 = photoS[k + 1];
    const t0 = located[k].takenAt;
    const t1 = located[k + 1].takenAt;
    let ratio = s1 > s0 ? (s - s0) / (s1 - s0) : 0;
    ratio = ratio < 0 ? 0 : ratio > 1 ? 1 : ratio;
    track[i].t = t0 + (t1 - t0) * ratio;
  }

  // ── 5. 分天：按拍摄时刻的 UTC 日期分组 ──────────────────────
  const days: JourneyDay[] = [];
  let currentKey = '';
  for (let k = 0; k < located.length; k += 1) {
    const date = new Date(located[k].takenAt);
    const key = `${date.getUTCFullYear()}-${date.getUTCMonth() + 1}-${date.getUTCDate()}`;
    if (key !== currentKey) {
      currentKey = key;
      days.push({
        index: days.length,
        label: formatDayMonth(located[k].takenAt),
        startS: photoS[k],
        endS: photoS[k],
      });
    }
    const day = days[days.length - 1];
    day.endS = Math.max(day.endS, photoS[k]);
    day.startS = Math.min(day.startS, photoS[k]);
  }
  if (days.length > 0) {
    days[0].startS = 0;
    days[days.length - 1].endS = totalMeters;
  }

  // ── 5.5 路段交通工具 ────────────────────────────────────────
  //
  // 规则两条：
  //  ① 用户只在「景点之间」设置交通工具；景点内部那 1km 的抖动不单独成段，
  //     否则整条线会被切出很多一小截的异色。
  //  ② 没有显式设置的路段**继承上一段**——这样"从西宁一路自驾"只需要设一次，
  //     中间几张连拍不会突然冒出一段青色。
  const segments: JourneySegment[] = [];
  let carry: TransportMode = DEFAULT_TRANSPORT;
  for (let k = 0; k < located.length - 1; k += 1) {
    const explicit = located[k].photo.transportToNext;
    if (explicit !== undefined) carry = normalizeTransport(explicit);
    const startS = photoS[k];
    const endS = photoS[k + 1];
    if (endS - startS < 1) continue; // 同一地点的两张照片，不成段
    segments.push({ startS, endS, mode: carry });
  }
  // 收尾：最后一张照片到终点（若样条把终点拉出去了一点）
  if (segments.length > 0) {
    const last = segments[segments.length - 1];
    last.endS = Math.max(last.endS, totalMeters);
  } else if (totalMeters > 0) {
    segments.push({ startS: 0, endS: totalMeters, mode: carry });
  }

  return {
    ...base,
    track,
    totalMeters,
    days,
    segments,
    suggestedZoom: roundZoom(zoom),
  };
}

/**
 * 每张照片在轨迹上的弧长位置（米）。时间轴刻度、卡片显隐都要用。
 * 与 buildJourney 内部用的是同一套「墨卡托最近点」判据。
 */
export function photoArcPositions(journey: Journey): Float32Array {
  const count = journey.photos.length;
  const out = new Float32Array(count);
  const track = journey.track;
  if (track.length === 0) return out;
  for (let k = 0; k < count; k += 1) {
    const photo = journey.photos[k];
    const lat = photo.lat ?? 0;
    const lng = photo.lng ?? 0;
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
    out[k] = bestS;
  }
  return out;
}

/**
 * 取弧长 s 处的姿态。写进外部传入的 out 对象——主循环每帧调用，不能产生垃圾。
 */
export function samplePose(journey: Journey, s: number, out: Pose): Pose {
  const track = journey.track;
  const n = track.length;
  if (n === 0) {
    out.s = 0;
    out.t = 0;
    out.lng = 0;
    out.lat = 0;
    out.mx = 0.5;
    out.my = 0.5;
    out.index = 0;
    return out;
  }

  const target = clamp(s, 0, track[n - 1].s);
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (track[mid].s <= target) {
      lo = mid;
    } else {
      hi = mid;
    }
  }

  const a = track[lo];
  const b = track[hi];
  const span = b.s - a.s;
  const ratio = span > 0 ? (target - a.s) / span : 0;

  out.mx = a.mx + (b.mx - a.mx) * ratio;
  out.my = a.my + (b.my - a.my) * ratio;
  out.s = target;
  out.t = a.t + (b.t - a.t) * ratio;
  out.lng = invMercX(out.mx);
  out.lat = invMercY(out.my);
  out.index = lo;
  return out;
}
