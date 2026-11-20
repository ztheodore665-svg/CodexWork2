/**
 * 六类语义标签 —— **自动生成，不要手改**。
 *
 * 唯一真相源：`backend/app/utils/semantic_tags.py`。
 * 重新生成：`python tools/gen_semantic_tags.py`
 * CI 校验：`python tools/gen_semantic_tags.py --check`
 *
 * 顺序即契约（平票裁决依据），生成器按源文件顺序输出，**只能追加不能重排**。
 */

export const SEMANTIC_TAGS = ["heritage", "nature", "watertown", "metropolis", "food", "leisure"] as const;

export type SemanticTag = (typeof SEMANTIC_TAGS)[number];

export const SEMANTIC_TAG_VERSION = "sem-2026.2";

export const SEMANTIC_TAG_LABELS: Record<SemanticTag, string> = {
  "heritage": "人文历史",
  "nature": "自然风光",
  "watertown": "水乡古镇",
  "metropolis": "都市风光",
  "food": "美食探索",
  "leisure": "休闲度假",
};
