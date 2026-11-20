/** 轨迹变速曲线（W12-1）：按「两个景点之间有多远」决定这一段播多久。
 *
 * ## 为什么需要它
 *
 * 播放器的时钟原本是**匀速**的：`Clock.q` 线性推进，弧长 `s = q × totalMeters`。
 * 于是「西湖 → 灵隐寺」4.5km 与「杭州 → 上海」170km 在屏幕上占一样的时长，
 * 长路段被压缩成一眨眼，短路段却慢得让人以为卡住了。
 *
 * 这里把**时间**按「段」分档：短段 0.5s、中段 1s、长段 2s（见 `DEFAULT_SPEED_CURVE`）。
 * 于是时间不再是弧长的线性函数，`q → s` 变成一条分段线性的单调曲线。
 *
 * ## 三个不变量（改这个文件前先读）
 *
 * 1. **`q` 仍然是唯一的进度量**。进度条、`clock.seek`、倍速按钮全部基于 `q`，
 *    只有「`q` → 弧长 `s`」这一步被变速曲线改写。反过来把 UI 改成基于 `s`，
 *    拖动进度条的手感会随距离分档跳变（手柄跟手，画面却一顿一顿）。
 * 2. **段边界必须用同一把尺**：累计 haversine 距离按 `journey.totalMeters` 归一化。
 *    `journey.totalMeters` 是**平滑样条**的长度（比折线略长），所以两套尺子必须
 *    对齐到「首 = 0、尾 = totalMeters」，否则 `q=1` 时镜头到不了终点。
 * 3. **严格单调不减**：`q` 增大时 `s` 不能回退，否则镜头会来回抽搐。
 *
 * ## 与包内其它模块的关系
 *
 * 只依赖 `@travelmemory/geo` 的 haversine 与 `@travelmemory/trail` 的类型，
 * **不碰** `packages/trail`（红线：本轮不改 packages）。
 */

import { haversine } from '@travelmemory/geo';
import type { Journey, Photo } from '@travelmemory/trail';

/** 分档参数。三个时长 + 两个距离阈值，阈值单位是公里。 */
export interface SpeedCurveConfig {
  /** 短段（< shortThresholdKm）播多久 */
  shortSegmentSeconds: number;
  /** 中段（shortThresholdKm ~ midThresholdKm）播多久 */
  midSegmentSeconds: number;
  /** 长段（>= midThresholdKm）播多久 */
  longSegmentSeconds: number;
  /** 短段与中段的分界（公里） */
  shortThresholdKm: number;
  /** 中段与长段的分界（公里） */
  midThresholdKm: number;
}

/**
 * 默认分档：近距离景区内 0.5s、市内/近郊 1s、城际 2s。
 *
 * 取值理由：5 张演示照片（西湖一带，段长 2.6~5.6km）总共 4s，
 * 一眼能看完又看得出"镜头在走"；若按 42s 匀速播，用户在中途就会去拖进度条。
 */
export const DEFAULT_SPEED_CURVE: SpeedCurveConfig = {
  shortSegmentSeconds: 0.5,
  midSegmentSeconds: 1,
  longSegmentSeconds: 2,
  shortThresholdKm: 2,
  midThresholdKm: 50,
};

const METERS_PER_KM = 1000;

/** 取坐标；任一不是有限数字就返回 null（`Photo` 的 lat/lng 用 null 表示未定位）。 */
function coordsOf(photo: Photo | undefined): { lat: number; lng: number } | null {
  if (!photo) return null;
  const { lat, lng } = photo;
  if (typeof lat !== 'number' || !Number.isFinite(lat)) return null;
  if (typeof lng !== 'number' || !Number.isFinite(lng)) return null;
  return { lat, lng };
}

/** 合法化秒数：非有限数 / 负数都折成兜底值（配置写错不该让播放直接坏掉）。 */
function sanitizeSeconds(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number.NaN;
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, parsed);
}

/** 合法化阈值（公里）：同上。 */
function sanitizeThreshold(value: unknown, fallback: number): number {
  return sanitizeSeconds(value, fallback);
}

/** 三点小数足够（0.001s 的误差在 60fps 下不可见），又让浮点相加结果稳定可断言。 */
function roundSeconds(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function roundMeters(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** 把外部传入的配置补齐成完整配置（缺字段 / 非法值一律回落到默认值）。 */
function normalizeConfig(config: SpeedCurveConfig | undefined): SpeedCurveConfig {
  const source = config ?? DEFAULT_SPEED_CURVE;
  return {
    shortSegmentSeconds: sanitizeSeconds(
      source.shortSegmentSeconds,
      DEFAULT_SPEED_CURVE.shortSegmentSeconds,
    ),
    midSegmentSeconds: sanitizeSeconds(source.midSegmentSeconds, DEFAULT_SPEED_CURVE.midSegmentSeconds),
    longSegmentSeconds: sanitizeSeconds(
      source.longSegmentSeconds,
      DEFAULT_SPEED_CURVE.longSegmentSeconds,
    ),
    shortThresholdKm: sanitizeThreshold(source.shortThresholdKm, DEFAULT_SPEED_CURVE.shortThresholdKm),
    midThresholdKm: sanitizeThreshold(source.midThresholdKm, DEFAULT_SPEED_CURVE.midThresholdKm),
  };
}

/** 单段时长：按 haversine 公里数分档。缺坐标的段按"短段"处理（不猜距离）。 */
function durationForSegment(
  from: { lat: number; lng: number } | null,
  to: { lat: number; lng: number } | null,
  config: SpeedCurveConfig,
): number {
  if (!from || !to) return config.shortSegmentSeconds;
  const km = haversine(from.lat, from.lng, to.lat, to.lng) / METERS_PER_KM;
  if (!Number.isFinite(km)) return config.shortSegmentSeconds;
  if (km < config.shortThresholdKm) return config.shortSegmentSeconds;
  if (km < config.midThresholdKm) return config.midSegmentSeconds;
  return config.longSegmentSeconds;
}

/**
 * 计算每一段的播放时长（秒），段数 = 照片数 − 1。
 *
 * `journey.photos` 已被 `buildJourney` 按 `takenAt` 升序且过滤掉未定位的照片，
 * 所以这里可以直接按相邻两张算距离。
 *
 * @param journey 已构建的旅程
 * @param config  分档参数（缺省用 `DEFAULT_SPEED_CURVE`）
 * @returns 段时长数组；**少于 2 张照片返回 `[]`**（没有段可言）
 */
export function computeSegmentDurations(
  journey: Journey,
  config: SpeedCurveConfig = DEFAULT_SPEED_CURVE,
): number[] {
  const photos = journey?.photos ?? [];
  if (photos.length < 2) return [];

  const resolved = normalizeConfig(config);
  const durations: number[] = [];
  for (let index = 1; index < photos.length; index += 1) {
    const seconds = durationForSegment(coordsOf(photos[index - 1]), coordsOf(photos[index]), resolved);
    durations.push(roundSeconds(seconds));
  }
  return durations;
}

/**
 * 总时长（秒）。非法项按 0 计 —— 宁可某段不占时间，也不要让 `NaN` 污染 `q`。
 *
 * @param durations `computeSegmentDurations` 的结果
 */
export function totalDurationSeconds(durations: number[]): number {
  if (!Array.isArray(durations) || durations.length === 0) return 0;
  let sum = 0;
  for (const item of durations) {
    if (typeof item !== 'number' || !Number.isFinite(item) || item <= 0) continue;
    sum += item;
  }
  return roundSeconds(sum);
}

/** `journey.totalMeters` 的防御性读取：非有限数 / 负数一律当 0。 */
function totalMetersOf(journey: Journey): number {
  const raw = journey?.totalMeters;
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return 0;
  return Math.max(0, raw);
}

/**
 * 各段端点（照片站点）在「弧长轴」上的位置，长度 = 段数 + 1。
 *
 * 做法：累计 haversine 距离 → 按 `journey.totalMeters` 归一化。
 * 平滑样条比折线略长，两者不可能逐点相等，所以只保证**首尾对齐**：
 * `boundaries[0] = 0`、`boundaries[last] = journey.totalMeters`，中间按比例摊开。
 *
 * 退化情况（所有照片同一位置、累计距离为 0）按段数均分 —— 保证端点与单调性，
 * 也不至于让分母为 0 算出 NaN。
 */
function segmentBoundaries(journey: Journey): number[] {
  const photos = journey?.photos ?? [];
  const totalMeters = totalMetersOf(journey);
  const boundaries: number[] = [0];
  const legCount = photos.length - 1;
  if (legCount < 1) return boundaries;

  const cumulative: number[] = [];
  let accumulated = 0;
  for (let index = 1; index < photos.length; index += 1) {
    const from = coordsOf(photos[index - 1]);
    const to = coordsOf(photos[index]);
    if (from && to) {
      const leg = haversine(from.lat, from.lng, to.lat, to.lng);
      if (Number.isFinite(leg) && leg > 0) accumulated += leg;
    }
    cumulative.push(accumulated);
  }

  if (accumulated <= 0) {
    // TODO(歧义): 所有照片在同一位置时"一段时间走了 0 米"没有物理意义。
    // 当前取最小实现：按段数均分总弧长，保证 q=1 能到终点。
    for (let index = 0; index < legCount; index += 1) {
      boundaries.push(roundMeters((totalMeters * (index + 1)) / legCount));
    }
    return boundaries;
  }

  for (const value of cumulative) {
    boundaries.push(roundMeters((totalMeters * value) / accumulated));
  }
  // 浮点相加可能让最后一项差一点点；直接钉死，保证 q=1 → totalMeters
  boundaries[boundaries.length - 1] = roundMeters(totalMeters);
  return boundaries;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

/**
 * 进度 `q` → 弧长 `s`（米）。**这是变速曲线的唯一出口**。
 *
 * 语义：
 *  - `q` 先乘以总时长得到「已播秒数」，落在第 k 段时按该段时长比例插值出弧长；
 *  - `q = 0 → 0`、`q = 1 → journey.totalMeters`；
 *  - 严格单调不减；越界 `q` 夹到 `[0, 1]`；
 *  - `durations` 为空（照片不足 2 张 / 调用方没算）时退回线性 `q × totalMeters`。
 *
 * @param q         归一化进度（可以越界，内部夹住）
 * @param journey   已构建的旅程（提供照片与总弧长）
 * @param durations `computeSegmentDurations(journey)` 的结果
 */
export function qToDistance(q: number, journey: Journey, durations: number[]): number {
  const totalMeters = totalMetersOf(journey);
  const progress = clamp01(q);
  const list = Array.isArray(durations) ? durations : [];
  if (list.length === 0) return progress * totalMeters;

  const totalSeconds = totalDurationSeconds(list);
  if (!(totalSeconds > 0)) return progress * totalMeters;

  const boundaries = segmentBoundaries(journey);
  // 形状不匹配（调用方把另一条旅程的 durations 传进来了）：退回线性，绝不越界读
  if (boundaries.length !== list.length + 1) return progress * totalMeters;

  const playedSeconds = progress * totalSeconds;
  let elapsed = 0;
  for (let index = 0; index < list.length; index += 1) {
    const raw = list[index];
    const duration = typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? raw : 0;
    if (playedSeconds <= elapsed + duration) {
      // 零时长段（配置把某档设成 0）在这里只能取 0：取 1 会把 `q=0` 直接推到
      // 该段末尾，破坏「`q=0 → 0`」这条不变量（QA 为此专门补了一条用例）。
      const local = duration > 0 ? clamp01((playedSeconds - elapsed) / duration) : 0;
      return boundaries[index] + (boundaries[index + 1] - boundaries[index]) * local;
    }
    elapsed += duration;
  }
  return totalMeters;
}
