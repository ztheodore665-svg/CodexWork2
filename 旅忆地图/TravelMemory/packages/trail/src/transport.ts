/** 路段交通工具。
 *
 * ## 为什么要有这个
 *
 * 一条真实行程不是"匀速直线"：西宁到青海湖是自驾，敦煌到嘉峪关可能坐高铁，
 * 长途跨省甚至飞一段。如果整条线一个样式、一个速度，播放出来就是一根没有性格的线。
 *
 * ## 视觉怎么区分
 *
 * 只靠颜色不够——深色底上色相差异会糊。这里**颜色 + 线型**一起用：
 *
 * | 交通工具 | 颜色 | 线型 | 直觉 |
 * | --- | --- | --- | --- |
 * | 自驾 | 青（主色） | 实线 | 默认、最"实" |
 * | 火车 | 琥珀 | 长虚线 | 铁轨的节奏 |
 * | 飞机 | 淡紫 | 点线 | 航线图上那种虚线 |
 * | 步行 | 浅绿 | 短点线 + 细 | 慢、轻 |
 *
 * ## 为什么要有一个"继承"规则
 *
 * 同一景点常拍好几张，照片之间只差 1km 出头。如果每两张照片之间都算一个路段，
 * 一个景点内部就会插进一小段别的颜色，看起来像断点。
 * 所以「短于站点合并阈值的路段」一律继承上一段的交通工具（见 `journey.ts`）。
 */

/** 交通工具。刻意只留 4 种：再多就没人分得清线型了。 */
export type TransportMode = 'drive' | 'train' | 'flight' | 'walk';

/** 默认交通工具（旧数据没有这个字段时用它）。 */
export const DEFAULT_TRANSPORT: TransportMode = 'drive';

export const TRANSPORT_MODES: readonly TransportMode[] = ['drive', 'train', 'flight', 'walk'];

export interface TransportStyle {
  /** 设置面板与播放器里的中文名 */
  label: string;
  /** 一段话说明它长什么样，用于设置面板的提示 */
  hint: string;
  /** 描边颜色（已走过的轨迹用它的高亮版本） */
  color: string;
  /** 未走过时的描边颜色（低透明度） */
  colorDim: string;
  /** 虚线节拍；空数组 = 实线 */
  dash: number[];
  /** 线宽（已走过时会被三遍描边的中间那遍采用） */
  width: number;
}

export const TRANSPORT_STYLE: Record<TransportMode, TransportStyle> = {
  drive: {
    label: '自驾',
    hint: '实线 · 青色',
    color: '#f0f9ff',
    colorDim: 'rgba(125,211,252,0.20)',
    dash: [],
    width: 1.6,
  },
  train: {
    label: '火车',
    hint: '长虚线 · 琥珀',
    color: '#fde68a',
    colorDim: 'rgba(251,191,36,0.26)',
    dash: [14, 8],
    width: 1.8,
  },
  flight: {
    label: '飞机',
    hint: '点线 · 淡紫',
    color: '#ddd6fe',
    colorDim: 'rgba(196,181,253,0.30)',
    dash: [2, 9],
    width: 1.6,
  },
  walk: {
    label: '步行',
    hint: '细点线 · 浅绿',
    color: '#bbf7d0',
    colorDim: 'rgba(134,239,172,0.26)',
    dash: [3, 5],
    width: 1.2,
  },
};

export function isTransportMode(value: unknown): value is TransportMode {
  return typeof value === 'string' && (TRANSPORT_MODES as readonly string[]).includes(value);
}

/** 安全取值：脏数据一律退回默认，不抛错（PROJECT.md §二 [8]：失败必须降级）。 */
export function normalizeTransport(value: unknown): TransportMode {
  return isTransportMode(value) ? value : DEFAULT_TRANSPORT;
}
