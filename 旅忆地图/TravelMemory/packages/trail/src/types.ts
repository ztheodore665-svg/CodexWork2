/** 全站共享的数据模型。字段含义见 README / 需求文档。 */

import type { TransportMode } from './transport';

export interface Photo {
  id: string;
  /** 缩略图 dataURL；演示数据为空，用 gradient 顶上 */
  thumb?: string;
  /** 程序化渐变（演示数据用），形如 'linear-gradient(135deg,#0f2027,#203a43)' */
  gradient?: string;
  /** WGS84。null = 尚未定位 */
  lat: number | null;
  lng: number | null;
  /**
   * 拍摄时刻，毫秒。统一用"把 EXIF 本地墙钟时间塞进 Date.UTC"的表示，
   * 显示时一律用 getUTC* 取值，保证跨时区不漂移。
   */
  takenAt: number;
  placeName?: string;
  /** 有 GPS 为 'exif'，手工补录为 'manual' */
  locationSource: 'exif' | 'manual' | 'none';
  /**
   * 从**这一张**到下一张的路上，用什么交通工具。
   *
   * 缺省 = 继承上一段（见 `journey.ts` 的继承规则）。设置面板只让用户改
   * 「两个景点之间」的那一段，景点内部那 1km 的抖动不单独成段。
   */
  transportToNext?: TransportMode;
}

export interface Waypoint {
  lng: number;
  lat: number;
}

export interface TrackSample {
  /** 墨卡托坐标（0..1），屏幕变换用 */
  mx: number;
  my: number;
  /** 累计里程（米） */
  s: number;
  /** 该点的插值时间戳 */
  t: number;
}

export interface JourneyDay {
  index: number;
  label: string;
  startS: number;
  endS: number;
}

/** 一个路段：从弧长 startS 走到 endS，用的交通工具是 mode。 */
export interface JourneySegment {
  startS: number;
  endS: number;
  mode: TransportMode;
}

export interface Journey {
  id: string;
  title: string;
  subtitle: string;
  /** 已按 takenAt 升序，且只含已定位照片 */
  photos: Photo[];
  /** 平滑后按弧长重采样 */
  track: TrackSample[];
  totalMeters: number;
  days: JourneyDay[];
  /** 按顺序首尾相接、覆盖整条轨迹的路段（颜色/线型靠它区分） */
  segments: JourneySegment[];
  bbox: { minLng: number; minLat: number; maxLng: number; maxLat: number };
  /** 根据站点中位间距算出的建议缩放级别 */
  suggestedZoom: number;
}
