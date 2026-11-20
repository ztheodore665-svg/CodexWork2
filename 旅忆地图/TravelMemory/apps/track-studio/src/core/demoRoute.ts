/** 内置演示路线。
 *
 * 唯一目的：**用户相册里的照片没有 GPS 时**（截图、微信保存的图、被洗过的图），
 * 也能看到「生成轨迹 → 播放」这条路是通的。
 *
 * ## 三条不容妥协的规则
 *
 * 1. 坐标是**真实存在的杭州地点**，写死在这里，绝不来自用户数据；
 * 2. UI 上必须显式标注「演示路线，不是你的照片位置」——否则用户会以为
 *    我们读到了他照片里的位置，这是对信任的直接损害；
 * 3. 演示照片的 `bytes` 是空 `ArrayBuffer`：这些图不存在，也没有原图可给。
 *    缩略图用 1×1 透明 PNG 兜底（渲染器会用它当卡片背景）。
 */

import { FALLBACK_THUMBNAIL_DATA_URL, type SystemPhoto } from '@travelmemory/photo-source';

/** 坐标系统一 WGS84（与内核一致；只有渲染前才转 GCJ-02）。 */
export interface DemoPoint {
  name: string;
  lat: number;
  lng: number;
}

/** 西湖 → 灵隐寺 → 龙井村 → 宋城 → 钱塘江（之江路）。 */
export const DEMO_ROUTE: readonly DemoPoint[] = [
  { name: '西湖 · 断桥', lat: 30.2431, lng: 120.1459 },
  { name: '灵隐寺', lat: 30.2408, lng: 120.0985 },
  { name: '龙井村', lat: 30.2213, lng: 120.1147 },
  { name: '宋城', lat: 30.1728, lng: 120.1009 },
  { name: '钱塘江 · 之江路', lat: 30.1878, lng: 120.144 },
];

/** 演示路线的固定标注文案。UI 必须原样显示。 */
export const DEMO_NOTE = '演示路线，不是你的照片位置';

/** 演示数据的日期基准（UTC）。固定值 = 每次演示的分天/时间轴都一样。 */
const DEMO_DAY_UTC = Date.UTC(2026, 3, 18, 1, 0, 0);
/** 相邻两张的间隔。40 分钟 × 4 段 ≈ 2.7 小时，够让播放节奏自然。 */
const DEMO_STEP_MS = 40 * 60 * 1000;

/**
 * 造一组演示用的 `SystemPhoto`。
 *
 * @returns 5 张照片，全部带坐标、时间递增、文件名就是地点名（`placeName` 会用它）。
 */
export function demoPhotos(): SystemPhoto[] {
  return DEMO_ROUTE.map((point, index) => ({
    id: `p_demo${index}`,
    bytes: new ArrayBuffer(0),
    name: `${point.name}.jpg`,
    takenAt: new Date(DEMO_DAY_UTC + index * DEMO_STEP_MS).toISOString(),
    lat: point.lat,
    lng: point.lng,
    thumbnailDataUrl: FALLBACK_THUMBNAIL_DATA_URL,
  }));
}
