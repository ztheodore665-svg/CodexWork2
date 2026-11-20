/**
 * 演示旅程数据（阶段 13 / W13-2：从 `showcase/src/data/demoJourneys.ts` **复制**）。
 *
 * 数据一行未改：坐标是真实地理走向的 WGS84 值，照片用程序化渐变生成，
 * 不依赖任何网络图片，因此永远不会白块 / 加载失败；
 * 抖动全部用下标做种子的确定性哈希，每次刷新完全一致。
 */

import {
  buildJourney,
  type BuildJourneyInput,
  type Journey,
  type Photo,
  type TransportMode,
} from '@travelmemory/trail';

interface Station {
  name: string;
  lat: number;
  lng: number;
  /** 从这一站到下一站用什么（缺省 = 自驾）。最后一站忽略。 */
  mode?: TransportMode;
}

const QINGGAN_STATIONS: Station[] = [
  { name: '西宁', lat: 36.6171, lng: 101.7782, mode: 'drive' },
  { name: '塔尔寺', lat: 36.4827, lng: 101.5698, mode: 'drive' },
  { name: '日月山', lat: 36.4, lng: 100.9833, mode: 'drive' },
  { name: '青海湖·二郎剑', lat: 36.5333, lng: 100.5, mode: 'drive' },
  { name: '黑马河', lat: 36.7333, lng: 100.0833, mode: 'drive' },
  { name: '茶卡盐湖', lat: 36.7, lng: 99.0833, mode: 'drive' },
  { name: '德令哈', lat: 37.3667, lng: 97.3667, mode: 'drive' },
  // ⚠️ 这段真实行程是自驾（400km 戈壁公路）。演示里标成飞机，
  // 只为让人看到第四种线型（点线·淡紫）；真实行程不该这么走。
  { name: '大柴旦翡翠湖', lat: 37.7167, lng: 95.1833, mode: 'flight' },
  { name: '当金山口', lat: 39.2, lng: 94.3, mode: 'drive' },
  { name: '敦煌鸣沙山', lat: 40.0833, lng: 94.6667, mode: 'drive' },
  { name: '莫高窟', lat: 40.0422, lng: 94.8053, mode: 'drive' },
  // 嘉峪关→张掖有兰新高铁，这一段坐车更合理
  { name: '嘉峪关', lat: 39.8, lng: 98.2833, mode: 'train' },
  { name: '张掖七彩丹霞', lat: 38.95, lng: 100.1167, mode: 'drive' },
  { name: '祁连卓尔山', lat: 38.1833, lng: 100.2333, mode: 'drive' },
  { name: '门源油菜花', lat: 37.3833, lng: 101.6167, mode: 'drive' },
  { name: '西宁', lat: 36.6171, lng: 101.7782 },
];

const JIANGNAN_STATIONS: Station[] = [
  { name: '上海外滩', lat: 31.24, lng: 121.49, mode: 'drive' },
  { name: '朱家角', lat: 31.11, lng: 121.05, mode: 'train' },
  { name: '苏州拙政园', lat: 31.3239, lng: 120.6294, mode: 'walk' },
  { name: '平江路', lat: 31.318, lng: 120.633, mode: 'drive' },
  { name: '同里', lat: 31.156, lng: 120.718, mode: 'drive' },
  { name: '乌镇', lat: 30.745, lng: 120.487, mode: 'drive' },
  { name: '西塘', lat: 30.94, lng: 120.89, mode: 'train' },
  { name: '杭州西湖', lat: 30.245, lng: 120.15 },
];

const PALETTE = [
  'linear-gradient(135deg,#0f2027,#203a43)',
  'linear-gradient(135deg,#1a2a6c,#2a5298)',
  'linear-gradient(135deg,#2c3e50,#4ca1af)',
  'linear-gradient(135deg,#153b2f,#2f6f4f)',
  'linear-gradient(135deg,#3a1c4a,#7b4397)',
  'linear-gradient(135deg,#4a2c1a,#8a5a2b)',
  'linear-gradient(135deg,#0b3a53,#146e8c)',
  'linear-gradient(135deg,#2b1055,#6a3ea1)',
  'linear-gradient(135deg,#123027,#2f7a5f)',
  'linear-gradient(135deg,#32203b,#6d3b6b)',
  'linear-gradient(135deg,#0e2a47,#1f5f8b)',
  'linear-gradient(135deg,#3b2f14,#8c6a2f)',
];

/** 确定性伪随机：返回 [-1, 1) */
function hashSigned(seedA: number, seedB: number): number {
  const raw = Math.sin(seedA * 127.1 + seedB * 311.7) * 43758.5453;
  const fractional = raw - Math.floor(raw);
  return fractional * 2 - 1;
}

interface DemoTrip {
  id: string;
  title: string;
  subtitle: string;
  photos: Photo[];
}

/**
 * 生成每个站点 2 张照片。
 *
 * 说明（对需求的一处修正）：需求写「每站 +5 小时」，但青甘 16 站 × 5h = 80h ≈ 3.3 天，
 * 与「青甘大环线（7 天）」自相矛盾。这里改成「把整条线均匀铺到 spanHours 小时」，
 * 站内两张仍间隔 40 分钟。于是 A 线恰好跨 7 个自然日、B 线恰好跨 4 个自然日，
 * 和路线标题、HUD 里的「第 x / N 天」全都对得上。
 */
function buildPhotos(
  prefix: string,
  stations: Station[],
  dayZeroUtc: number,
  spanHours: number,
): Photo[] {
  const photos: Photo[] = [];
  const stepHours = stations.length > 1 ? spanHours / (stations.length - 1) : 0;
  for (let i = 0; i < stations.length; i += 1) {
    const station = stations[i];
    for (let j = 0; j < 2; j += 1) {
      const lat = station.lat + hashSigned(i, j) * 0.012;
      const lng = station.lng + hashSigned(i + 41, j + 7) * 0.012;
      const takenAt = dayZeroUtc + (i * stepHours * 60 + j * 40) * 60 * 1000;
      photos.push({
        id: `${prefix}-${i}-${j}`,
        gradient: PALETTE[(i * 2 + j) % PALETTE.length],
        lat,
        lng,
        takenAt,
        placeName: station.name,
        locationSource: 'exif',
        // 交通工具挂在**该站的最后一张**上：它代表"从这一站出发去下一站"。
        // 挂在第一张会让站内那 1km 的抖动也带上颜色。
        ...(j === 1 && station.mode ? { transportToNext: station.mode } : {}),
      });
    }
  }
  return photos;
}

export const DEMO_QINGGAN: DemoTrip = {
  id: 'qinggan-loop',
  title: '青甘大环线',
  subtitle: '西宁 · 青海湖 · 敦煌 · 张掖 — 7 天',
  photos: buildPhotos('qg', QINGGAN_STATIONS, Date.UTC(2026, 6, 12, 8, 0, 0), 156),
};

export const DEMO_JIANGNAN: DemoTrip = {
  id: 'jiangnan-watertown',
  title: '江南水乡',
  subtitle: '上海 · 苏州 · 乌镇 · 杭州 — 4 天',
  photos: buildPhotos('jn', JIANGNAN_STATIONS, Date.UTC(2026, 9, 2, 8, 0, 0), 84),
};

export const DEMO_TRIPS: DemoTrip[] = [DEMO_QINGGAN, DEMO_JIANGNAN];

/** 已构建好的 Journey（供 App 直接使用） */
export function buildDemoJourneys(): Journey[] {
  const inputs: BuildJourneyInput[] = DEMO_TRIPS.map((trip) => ({
    id: trip.id,
    title: trip.title,
    subtitle: trip.subtitle,
    photos: trip.photos,
  }));
  return inputs.map((input) => buildJourney(input));
}
