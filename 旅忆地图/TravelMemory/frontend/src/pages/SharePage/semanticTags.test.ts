/** 语义标签的前端校验层（W7-1 前端部分）。
 *
 * 最关键的一条断言是**顺序一致性**：`ALL_SEMANTIC_TAGS` 必须与后端
 * `backend/app/utils/semantic_tags.py` 的 `SEMANTIC_TAGS` 逐项同序 —— 两端都拿
 * 数组顺序当"平票时取哪个"的依据，顺序一分叉，同一条 POI 在两端会得到不同标签。
 * 这里用正则直接从后端源码里抽数组来断言，改一端忘另一端会立刻变红。
 */

import { describe, expect, it } from 'vitest';

import { TAG_LABELS, type Tag } from '../../types/share';
// 直接把后端源码当文本读进来（`?raw` 由 vite/client 提供类型，无需 @types/node）：
// 顺序一致性必须拿**后端真实文件**来断言，硬编码一份期望值等于什么都没测。
import backendSource from '../../../../backend/app/utils/semantic_tags.py?raw';
import {
  ALL_SEMANTIC_TAGS,
  FALLBACK_SEMANTIC_TAG,
  isSemanticTag,
  normalizeSemanticTag,
} from './semanticTags';

/** 从后端源码里抽 `SEMANTIC_TAGS` 元组。 */
function backendSemanticTags(): string[] {
  const block = /SEMANTIC_TAGS[^=]*=\s*\(([\s\S]*?)\)/.exec(backendSource);
  if (!block) throw new Error('没能在后端源码里找到 SEMANTIC_TAGS');
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
}

describe('ALL_SEMANTIC_TAGS', () => {
  it('与后端 SEMANTIC_TAGS 逐项同序', () => {
    expect([...ALL_SEMANTIC_TAGS]).toEqual(backendSemanticTags());
  });

  it('是六类，且每一类都有中文名', () => {
    expect(ALL_SEMANTIC_TAGS).toHaveLength(6);
    for (const tag of ALL_SEMANTIC_TAGS) {
      expect(TAG_LABELS[tag]).toBeTruthy();
    }
  });
});

describe('isSemanticTag', () => {
  it('六类都认', () => {
    for (const tag of ALL_SEMANTIC_TAGS) {
      expect(isSemanticTag(tag)).toBe(true);
    }
  });

  it('非字符串与六类之外的值都不认', () => {
    expect(isSemanticTag('scenic')).toBe(false);
    expect(isSemanticTag('')).toBe(false);
    expect(isSemanticTag(undefined)).toBe(false);
    expect(isSemanticTag(null)).toBe(false);
    expect(isSemanticTag(42)).toBe(false);
  });
});

describe('normalizeSemanticTag', () => {
  it('合法值原样返回', () => {
    for (const tag of ALL_SEMANTIC_TAGS) {
      expect(normalizeSemanticTag(tag)).toBe(tag);
    }
  });

  it('非法值退回缺省的 leisure', () => {
    expect(normalizeSemanticTag('scenic')).toBe('leisure');
    expect(normalizeSemanticTag(undefined)).toBe('leisure');
    expect(normalizeSemanticTag('')).toBe('leisure');
    expect(FALLBACK_SEMANTIC_TAG).toBe('leisure');
  });

  it('非法值退回指定的 fallback', () => {
    expect(normalizeSemanticTag('scenic', 'heritage')).toBe('heritage');
    // 合法值不受 fallback 影响
    expect(normalizeSemanticTag('food', 'heritage')).toBe('food');
  });

  it('返回值始终是六类之一（保底不产生非法标签）', () => {
    const value: Tag | null = normalizeSemanticTag('not-a-tag');
    expect(isSemanticTag(value)).toBe(true);
  });
});
