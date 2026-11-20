/** 六类语义标签的**前端校验层**（阶段 7 W7-1 的前端部分）。
 *
 * ## 分工：前端只校验与保底，绝不重新分类
 *
 * POI / 地点名 → 语义标签的**分类算法**在后端 `app/utils/semantic_tags.py`
 * （词表 `TAG_KEYWORDS` + `classify_semantic_tag()`）与 `app/services/semantic_service.py`。
 * 那份词表是"唯一真相源"，前端**不复制、不复算**：
 *
 * - 复制词表 = 两端各有一份规则，改一端忘另一端，语义序列就对不上，
 *   而群体模式库是按序列统计的，错一个词会污染整条模式；
 * - 前端复算还意味着要把几百个中文关键词打进 bundle，为一个"保底"付两份维护成本。
 *
 * 前端的职责只有两件事：
 * 1. **校验**：信物里的 `Place.tag` 必须是六类之一（`isSemanticTag`）；
 * 2. **保底**：不是六类之一时退回默认值，让打包永远产出一个合法标签
 *    （`normalizeSemanticTag`）。协议要求 `tag` 必填，给它一个兜底值好过打包抛错。
 *
 * ## 顺序即契约
 *
 * `ALL_SEMANTIC_TAGS` 必须与后端 `SEMANTIC_TAGS` **逐项同序**：后端平票时按数组顺序取，
 * 前端 `ALL_TAGS`（`types/share.ts`）也是同一顺序。**只能追加，不能重排**。
 * 阶段 16 起两者都来自 `src/generated/semanticTags.ts`（由 Python 词表生成），
 * `semanticTags.test.ts` 仍保留"从 py 源码抠数组比对"的第二道守卫。
 *
 * ## 本次不改动分享协议
 *
 * `Place.tag` 本来就在 TrailShare 协议里（`EncryptedPlace.tag` 是明文、参与承诺），
 * 这里只是确保它**一定是六类之一**，没有新增字段、没有改线格式。
 */

import { SEMANTIC_TAGS } from '../../generated/semanticTags';
import type { Tag } from '../../types/share';

/**
 * 六类语义标签。**顺序必须与后端 `SEMANTIC_TAGS` 逐项一致**，禁止重排。
 *
 * 阶段 16 起这份数组由 `tools/gen_semantic_tags.py` 生成，不再手写 —— 手写的副本
 * 是"六份名单"里最容易漏改的一份（它甚至不在 `types/share.ts` 里，grep 都容易漏）。
 */
export const ALL_SEMANTIC_TAGS: readonly Tag[] = SEMANTIC_TAGS;

/** 缺省兜底标签：无法归类时用它（"休闲度假"是最中性的一类，不会凭空暗示地点性质）。 */
export const FALLBACK_SEMANTIC_TAG: Tag = 'leisure';

/** 是否是六类语义标签之一。收窄成 `Tag`，方便调用方直接当 `Tag` 用。 */
export function isSemanticTag(value: unknown): value is Tag {
  return typeof value === 'string' && (ALL_SEMANTIC_TAGS as readonly string[]).includes(value);
}

/**
 * 校验并保底：合法原样返回，非法返回 `fallback`（缺省 `'leisure'`）。
 *
 * 返回类型保留 `Tag | null` 是为了让将来的调用方能显式表达"连兜底都不要"
 * （届时传 `null` 语义的重载）；当前实现在缺省参数下永不返回 `null`。
 */
export function normalizeSemanticTag(
  raw: string | undefined,
  fallback: Tag = FALLBACK_SEMANTIC_TAG,
): Tag | null {
  return isSemanticTag(raw) ? raw : fallback;
}
