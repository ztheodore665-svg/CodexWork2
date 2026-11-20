/** 轨迹回放内核。
 *
 * 这里只有"算"和"画"，**没有任何 React**——宿主（展示站 / 主应用）自己决定
 * 用什么组件包它。这样两套视觉完全不同的界面能共用同一份动画逻辑。
 *
 * 依赖方向单向：`宿主 → @travelmemory/trail → @travelmemory/geo`。
 */

// ── 数据模型 ──
export type { Journey, JourneyDay, JourneySegment, Photo, TrackSample, Waypoint } from './types';

// ── 交通工具 ──
export {
  DEFAULT_TRANSPORT,
  isTransportMode,
  normalizeTransport,
  TRANSPORT_MODES,
  TRANSPORT_STYLE,
  type TransportMode,
  type TransportStyle,
} from './transport';

// ── 行程构建与取样 ──
export {
  buildJourney,
  createPose,
  photoArcPositions,
  samplePose,
  type BuildJourneyInput,
  type Pose,
} from './journey';
export { catmullRomCentripetal, resampleByArcLength, type ArcPoint, type Pt } from './spline';

// ── 时间与格式化 ──
export { Clock } from './clock';
export {
  formatDateDot,
  formatDayMonth,
  formatDuration,
  formatKm,
  formatKmNumber,
  formatPercent,
  formatTimeHM,
  formatWeekday,
  groupThousands,
} from './format';

// ── 相机与投影 ──
export { Camera, DEFAULT_TAU } from './camera';
export {
  analyticProjection,
  projectX,
  projectY,
  setCamera,
  TILE_SIZE,
  type Projection,
} from './projection';

// ── 渲染器 ──
export {
  ANCHOR_STRIDE,
  prepareScene,
  Renderer,
  type DrawState,
  type Scene,
  type SceneLabel,
} from './renderer';
