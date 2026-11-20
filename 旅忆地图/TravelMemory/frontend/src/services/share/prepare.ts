/** 数据融合层：TrailShare 信物协议 × TravelMemory 的地理模糊旁路。
 *
 * ## 这一层为什么单独存在
 *
 * `pack()` 必须保持"规范原样"：它的输入是 Trip + 可见性 + 收件人，
 * 不该知道坐标、不该发网络请求、也不该知道后端存在。于是把 TravelMemory 特有的
 * "先把坐标模糊掉，再打进 `PlaceMeta`"放在这一层 —— 协议移植过来时，
 * 差异被压缩成一个文件。
 *
 * ## 失败必须降级（PROJECT.md §二、[8]）
 *
 * 扩展模块走 feature flag，默认全关，**失败必须降级而非抛错**。
 * 所以这里对每一个地点的模糊调用都单独 try/catch：
 *   - 成功 → `PlaceMeta` 多写 `lat/lng/radius`；
 *   - 失败（网络断了、后端 404 因为开关没开、参数被 422 拒）→ **不写坐标**，
 *     退化成规范原版的 `PlaceMeta`，并在 `warnings` 里留一条。
 *
 * 绝不能做的是"模糊失败就用原坐标"：那等于静默地分享精确位置。
 * 也绝不能"模糊失败就整个打包失败"：隐私旁路不应该拖垮主线。
 *
 * ## 只对"可见"的 place 调用
 *
 * 被屏蔽的地点不会进包，因此也不会进 `PlaceMeta`。假如不加区分地遍历整个 Trip，
 * 就会向服务端暴露"用户其实去了哪些地方" —— 屏蔽的语义（不打包）会被旁路绕过。
 */

import type {
  ObfuscatedPoint,
  ObfuscationMode,
  PlaceGeoHint,
  SharePackage,
  FilterResult,
  ChainRecord,
} from '../../types/share';
import { applyVisibility } from './filter';
import { pack, type PackGroupInput, type PackInput } from './pack';

/** 模糊函数的最小契约。生产实现是 `api/privacy.ts` 的 `obfuscateLocation`；
 *  测试注入一个假的（真机/网络在单测里都不可用，也不该用）。 */
export type ObfuscateFn = (
  lat: number,
  lng: number,
  mode: ObfuscationMode,
  epsilon: number,
) => Promise<ObfuscatedPoint>;

export interface PrepareOptions {
  mode?: ObfuscationMode;
  epsilon?: number;
  /** 注入点。不传则在真正调用 `prepareShare` 时由调用方自行注入 —— 本模块不 import
   *  `api/privacy.ts`，为的是让 `share/*` 保持"不碰网络"的分层边界（规范 §2.3）。 */
  obfuscate?: ObfuscateFn;
  now?: Date;
}

export interface PrepareResult {
  pkg: SharePackage;
  visible: FilterResult[];
  chainRecord: ChainRecord | null;
  /** 降级记录：每一条都是"这个地点没能模糊，已按规范原版打包（不写坐标）"。 */
  warnings: string[];
}

/**
 * 先模糊坐标，再打包。
 *
 * @param input  与 `pack()` 相同的输入（`groups[i].trip` 上带可选 `lat/lng`）
 * @param opts   `obfuscate` 缺省时**跳过整个旁路**（不发网络请求，全部地点不写坐标）
 */
export async function prepareShare(input: PackInput, opts: PrepareOptions = {}): Promise<PrepareResult> {
  const warnings: string[] = [];
  const mode = opts.mode ?? 'approx';
  const epsilon = opts.epsilon ?? 1.0;
  const obfuscate = opts.obfuscate;

  // 没有注入模糊函数 = 旁路未启用：直接退化成规范原版打包，不产生 warning
  // （"没启用"不是"失败了"，把它记进 warnings 会让用户以为出了错）。
  if (!obfuscate) {
    const result = await pack(input);
    return { ...result, warnings };
  }

  const groupsWithHints: PackGroupInput[] = [];

  for (const group of input.groups) {
    // 只对**可见**的 place 调模糊：被屏蔽的地点不该向服务端暴露存在
    const filtered = applyVisibility(group.trip, group.visibility);
    const geoHints: Record<string, PlaceGeoHint> = {};

    for (const entry of filtered.places) {
      const place = entry.place;
      // 没坐标的 place 不写这几个字段 —— PlaceMeta 退化成规范原版
      if (!hasCoords(place.lat, place.lng)) continue;
      try {
        const point = await obfuscate(place.lat as number, place.lng as number, mode, epsilon);
        geoHints[place.id] = { lat: point.lat, lng: point.lng, radius: point.radius };
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        warnings.push(`地点「${place.name}」的地理模糊失败，已按规范原版打包（不写入坐标）：${reason}`);
      }
    }

    groupsWithHints.push({ ...group, geoHints });
  }

  const result = await pack({ ...input, groups: groupsWithHints });
  return { ...result, warnings };
}

/** 坐标有效性检查：两端都要是有限数，纬度经度各自在值域内。
 *  少判一个 `Number.isFinite` 就会把 `NaN` 送进后端，换来一个 422 和一条看不懂的 warning。 */
function hasCoords(lat: number | undefined, lng: number | undefined): boolean {
  if (typeof lat !== 'number' || typeof lng !== 'number') return false;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  return lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
}
