/** `JourneyBundle`（`journey.json` 线格式）→ 主应用可消费的结构。
 *
 * 与 `export.ts` 的取舍不同：导出是"尽力而为"，导入必须**对版本号严格**。
 * 版本不符时抛中文错误 —— 调用方（主应用的导入按钮）需要能把这句错误原样给用户看，
 * 而"静默导入了半个旅程"比"明确拒绝"糟糕得多。
 */

import { JOURNEY_BUNDLE_VERSION, type JourneyBundle, type JourneyDay } from './types';
import { FALLBACK_TITLE, asNonEmptyString, asRecord, mapBundlePhoto, normalizeDays } from './export';

/** `tripFromBundle` 的返回值：主应用只认这两样东西就能重建旅程。 */
export interface ImportedJourney {
  title: string;
  days: JourneyDay[];
}

/**
 * 校验并解出 `title` 与 `days`。
 *
 * @param bundle 从 `journey.json` 读回来（或 postMessage 收到）的包。
 * @throws `Error` 中文错误：`bundle` 不是对象、或 `version` 不等于 `JOURNEY_BUNDLE_VERSION`。
 * @returns `days` 已按 `YYYY-MM-DD` 校验、照片已校验缩略图上限；非法条目被丢弃。
 */
export function tripFromBundle(bundle: JourneyBundle): ImportedJourney {
  const record = asRecord(bundle);
  if (!record) {
    throw new Error('导入失败：这不是一个有效的 journey 包（内容不是对象）。');
  }
  const version = record.version;
  if (version !== JOURNEY_BUNDLE_VERSION) {
    throw new Error(
      `导入失败：不支持的 journey 版本「${String(version)}」，当前只支持「${JOURNEY_BUNDLE_VERSION}」。请让导出方升级到同一版本后重新导出。`,
    );
  }
  return {
    title: asNonEmptyString(record.title) ?? FALLBACK_TITLE,
    days: normalizeDays(record.days, mapBundlePhoto),
  };
}
