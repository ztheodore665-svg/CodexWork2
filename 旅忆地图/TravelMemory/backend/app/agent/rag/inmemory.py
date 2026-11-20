"""内存检索：**同时实现向量端口与词面端口**的离线替身（阶段 4 W4-3 起从 tests 提到 app）。

## 为什么要搬出 tests/

`make eval-ablation` 的出口条件（Recall@5 不低于阶段 3 基线）在**没有数据库**的机器上
也必须能判定 —— 否则"起 Docker 才能验收"会让这条红线在本机长期处于未验证状态。
把它放在 `app/` 下，评测器、CLI 的离线模式与单测就能共用同一份实现：

- 少一份实现 = 少一处"两边语义不一致"的风险（过滤逻辑写错一边，另一边发现不了）
- `tests/fakes.py` 保留 re-export，既有用例的 import 不用改

## 结构化过滤为什么要在内存里再实现一遍

过滤逻辑有一份真实实现（SQL，`DocumentService`）和一份内存实现（Python，这里）。
看似重复，实则是有意为之：它逼着两边的语义保持一致，
任一边改错了，单测就会红 —— 这类"双实现互检"比任何文档都可靠。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import date

from app.agent.rag.filters import StructuredFilter
from app.schemas.document import SearchHit

#: 演示语料：与 `app/scripts/seed_demo.py` 用的是同一批文件
CORPUS_FILES = ("demo_corpus.jsonl", "demo_places.jsonl")


class InMemoryVectorSearch:
    """内存检索：向量用余弦相似度，词面用关键词覆盖率，过滤用 Python 判定。"""

    def __init__(self) -> None:
        #: (user_id, SearchHit, vector)
        self.rows: list[tuple[uuid.UUID, SearchHit, list[float]]] = []

    async def add(self, user_id: uuid.UUID, hit: SearchHit, vector: Sequence[float]) -> None:
        self.rows.append((user_id, hit, list(vector)))

    # -------------------------------------------------------------- 向量
    async def similar_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        source_types: Sequence[str] | None = None,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]:
        scored: list[tuple[float, SearchHit]] = []
        for row_user, hit, vector in self.rows:
            if row_user != user_id:
                continue
            if source_types and hit.ref.source_type not in source_types:
                continue
            if not _match_filter(hit, flt):
                continue
            scored.append((_cosine(query_vector, vector), hit))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            SearchHit(**{**hit.model_dump(), "score": round(score, 6)})
            for score, hit in scored[:top_k]
        ]

    # -------------------------------------------------------------- 近似（HNSW 语义替身）
    async def ann_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        oversample: int = 4,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]:
        """模拟 HNSW "先图遍历、后过滤" 的行为。

        关键点：**先取 top_k × oversample 个近邻，再在这批候选里套过滤**。
        与 `DocumentService.ann_search` 的两步顺序完全一致 ——
        唯有一致，"ANN 返回 0 条 → 回退精确扫描"这条路径才能在**没有数据库**的情况下被验证。
        """
        scored: list[tuple[float, SearchHit]] = []
        for row_user, hit, vector in self.rows:
            if row_user != user_id:
                continue
            scored.append((_cosine(query_vector, vector), hit))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        # 与 DocumentService.ann_search 完全一致：candidates = top_k × oversample
        candidates = scored[: top_k * max(1, oversample)]

        kept: list[SearchHit] = []
        for score, hit in candidates:
            if not _match_filter(hit, flt):
                continue
            kept.append(SearchHit(**{**hit.model_dump(), "score": round(score, 6)}))
            if len(kept) >= top_k:
                break
        return kept

    # -------------------------------------------------------------- 词面
    async def lexical_search(
        self,
        user_id: uuid.UUID,
        terms: Sequence[str],
        *,
        top_k: int = 8,
        flt: StructuredFilter | None = None,
        min_coverage: float = 0.5,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        cleaned = [t.strip() for t in terms if t and t.strip()]
        if not cleaned:
            return []

        scored: list[tuple[float, SearchHit]] = []
        for row_user, hit, _vector in self.rows:
            if row_user != user_id:
                continue
            if not _match_filter(hit, flt):
                continue
            haystack = hit.content + " " + _metadata_text(hit)
            hit_count = sum(1 for term in cleaned if term in haystack)
            coverage = hit_count / len(cleaned)
            if coverage < min_coverage:
                continue
            scored.append((coverage, hit))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            SearchHit(**{**hit.model_dump(), "score": round(score, 6)})
            for score, hit in scored[:top_k]
        ]


def _metadata_text(hit: SearchHit) -> str:
    """把 metadata 摊平成可搜索文本（对齐 SQL 里的 `metadata::text ILIKE`）。"""
    parts: list[str] = []
    for _key, value in (hit.metadata or {}).items():
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        else:
            parts.append(str(value))
    return " ".join(parts)


def _match_filter(hit: SearchHit, flt: StructuredFilter | None) -> bool:
    """内存版结构化过滤（语义必须与 DocumentService 的 SQL 条件一致）。"""
    if flt is None or flt.is_empty():
        return True
    meta = hit.metadata or {}

    # ---- 时间：区间相交；没有时间字段的文档放行 ----
    doc_start = _as_date(meta.get("date_start") or meta.get("date"))
    doc_end = _as_date(meta.get("date_end") or meta.get("date"))
    if flt.date_start is not None and doc_end is not None and doc_end < flt.date_start:
        return False
    if flt.date_end is not None and doc_start is not None and doc_start > flt.date_end:
        return False

    # ---- 地名：正文或 metadata 任一字段包含即可 ----
    if flt.places:
        pool = hit.content + " " + _metadata_text(hit)
        if not any(name in pool for name in flt.places):
            return False

    # ---- 主题 ----
    if flt.themes:
        themes = [str(t) for t in (meta.get("themes") or [])]
        if not any(theme in themes for theme in flt.themes):
            return False

    # ---- 包围盒：相交 ----
    if flt.bbox is not None:
        values = [meta.get(k) for k in ("min_lat", "max_lat", "min_lng", "max_lng")]
        if all(v is not None for v in values):
            # `all(v is not None)` 已经保证了非空，但静态类型看不出来 ——
            # 显式收敛成 float 元组，比在每个下标处 `# type: ignore` 干净
            coords = tuple(float(v) for v in values if v is not None)
            if len(coords) != 4:  # pragma: no cover - 由上面的 all() 保证
                return True
            min_lat, max_lat, min_lng, max_lng = coords
            if min_lat > flt.bbox.max_lat or max_lat < flt.bbox.min_lat:
                return False
            if min_lng > flt.bbox.max_lng or max_lng < flt.bbox.min_lng:
                return False
    return True


def _as_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, str):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度（向量已归一化时等价于点积，这里仍按通用公式算）。"""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return dot / (norm_l * norm_r)
