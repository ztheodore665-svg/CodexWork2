"""检索编排（阶段 2 W2-2 的主战场）。

职责（设计文档 §5.1）：查询串组织、结构化过滤、两路召回、分数融合、top-k 组装。
**不做**embedding 写入（那是 indexer 的事），也**不做**重排（阶段 3 的 reranker）。

## 这一版相比阶段 0 多了三层梯子

```
第 1 层：结构化过滤   WHERE 时间相交 / 地名 / 主题 / bbox   ← 先缩小候选集
第 2 层：两路召回     向量（语义像不像） + 词面（字面有没有） ← 互补
第 3 层：分数融合     各自归一化后加权求和                  ← 让两路可比
```

外加两道**安全阀**（别小看它们，过滤是把双刃剑）：

- **过滤兜底**：过滤后命中太少 → 补一次不过滤的检索。宁可多召回，不要漏召回。
- **min_score 闸门**：向量分低于阈值的证据不进上下文（阶段 0 就有的拒答基石）。

## 为什么本阶段不做 RRF

RRF（Reciprocal Rank Fusion）用 `1/(k + rank)` 融合，优点是不需要归一化、对量纲免疫。
设计文档把 RRF 排在阶段 4（W4-3），本阶段先用**归一化加权求和**：
它更直观（能直接看出"这一路贡献了多少分"），缺点是依赖归一化口径。
等阶段 3 引入 reranker 分数、阶段 4 再加入记忆检索后，
三路以上的融合用 RRF 才明显更稳 —— 那时候再换，代码只动 `_fuse` 一个函数。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.agent.rag.filters import StructuredFilter
from app.agent.rag.ports import LexicalSearchPort, VectorSearchPort
from app.agent.rag.reranker import BaseReranker
from app.agent.state import ParsedQuery, PipelineState
from app.core.embedding import BaseEmbedder
from app.schemas.document import SearchHit


@dataclass
class RetrieverConfig:
    top_k: int = 8
    #: 相似度下限：低于此值的**向量**证据不参与生成，宁可拒答也不硬凑引用
    #: 与 `config.retrieval_min_score` 保持一致（默认 0.01）。
    #: 这里给的是"直接用默认配置构造 Retriever"时的兜底值，不是产品默认值 ——
    #: 产品默认值在 config.py 里，且那里写清了为什么不能设成 0.30。
    min_score: float = 0.01
    #: 两路融合权重（会各自先归一化到 0~1）
    vector_weight: float = 0.65
    lexical_weight: float = 0.35
    #: 词面召回的最低关键词覆盖率
    lexical_min_coverage: float = 0.5
    #: 过滤后命中数少于此值 → 补一次不过滤的检索（防误过滤漏召回）
    filter_min_hits: int = 3
    enable_lexical: bool = True
    enable_filter: bool = True
    #: HNSW 近似检索总开关（feature flag `hnsw` 映射到这里）
    enable_hnsw: bool = False
    #: 是否**允许**走近似检索。与 `enable_hnsw` 是两道门，任一为 False 都走精确扫描。
    #: 默认 False：小库（万条以下）上 HNSW 的近似误差比它省下的时间更致命。
    use_ann: bool = False
    #: HNSW 过采样倍数：先取 top_k × oversample 个近似近邻，再套过滤
    hnsw_oversample: int = 4
    #: 重排后保留的条数
    rerank_top_n: int = 8
    #: 阶段 4 W4-3：融合策略。`weighted`（默认，阶段 2 行为）| `rrf`
    fusion: str = "weighted"
    #: RRF 的平滑常数 k（见 `reciprocal_rank_fusion` 的文档）
    rrf_k: int = 60


@dataclass
class RetrieverDebug:
    """一次检索的过程记录，写进 trace 便于回溯"为什么召回这些"。"""

    filter: dict = field(default_factory=dict)
    filter_fallback: bool = False
    vector_hits: int = 0
    lexical_hits: int = 0
    fused_hits: int = 0
    #: 走的是精确扫描 / 近似检索 / 近似不足回退
    index_path: str = "exact"
    #: 本次是否真的做了重排（远程失败时为 False）
    reranked: bool = False


class Retriever:
    """把「状态里的自然语言」变成「一批带分数的证据」。"""

    def __init__(
        self,
        searcher: VectorSearchPort,
        embedder: BaseEmbedder,
        config: RetrieverConfig | None = None,
        lexical: LexicalSearchPort | None = None,
        reranker: BaseReranker | None = None,
    ) -> None:
        self.searcher = searcher
        self.embedder = embedder
        self.config = config or RetrieverConfig()
        #: 词面检索器可选：不传就退化为纯向量（便于单测与降级）
        self.lexical = lexical
        #: 重排器可选：None 表示完全不重排（阶段 2 行为）
        self.reranker = reranker

    # ---------------------------------------------------------------- 主入口
    async def retrieve(self, state: PipelineState) -> list[SearchHit]:
        """执行一次混合检索。"""
        query_text = self.build_search_text(state)
        if not query_text.strip():
            return []
        vector = await self.embedder.embed_one(query_text)

        flt = self.build_filter(state.parsed) if self.config.enable_filter else None
        debug = RetrieverDebug(filter=flt.describe() if flt else {})

        # ---- 第 1 路：向量 ----
        # 走近似检索要**同时满足**两道门（feature flag + 数据量判断），
        # 并且 searcher 得真的有这个方法。任一条不满足都走精确扫描 ——
        # 精确扫描是这个项目在万条数据以下的默认正确路径。
        ann_ok = self.config.enable_hnsw and self.config.use_ann
        if ann_ok and hasattr(self.searcher, "ann_search"):
            debug.index_path = "ann"
            vec_hits = await self.searcher.ann_search(
                state.user_id,
                vector,
                top_k=self.config.top_k,
                oversample=self.config.hnsw_oversample,
                flt=flt,
            )
            vec_hits = [hit for hit in vec_hits if hit.score >= self.config.min_score]
            if len(vec_hits) < self.config.filter_min_hits:
                # 这就是 pgvector HNSW "先图后过滤" 会返回 0 条的那条路径。
                # 回退到精确扫描，宁可慢一点也不能漏召回。
                debug.index_path = "exact_fallback"
                vec_hits = await self.searcher.similar_search(
                    state.user_id, vector, top_k=self.config.top_k, flt=flt
                )
                vec_hits = [hit for hit in vec_hits if hit.score >= self.config.min_score]
        else:
            vec_hits = await self.searcher.similar_search(
                state.user_id, vector, top_k=self.config.top_k, flt=flt
            )
            vec_hits = [hit for hit in vec_hits if hit.score >= self.config.min_score]

        # ---- 安全阀 1：过滤兜底 ----
        if flt is not None and not flt.is_empty() and len(vec_hits) < self.config.filter_min_hits:
            debug.filter_fallback = True
            unfiltered = await self.searcher.similar_search(
                state.user_id, vector, top_k=self.config.top_k
            )
            unfiltered = [hit for hit in unfiltered if hit.score >= self.config.min_score]
            vec_hits = _merge_keep_best(vec_hits, unfiltered)

        debug.vector_hits = len(vec_hits)

        # ---- 第 2 路：词面 ----
        lex_hits: list[SearchHit] = []
        if self.config.enable_lexical and self.lexical is not None:
            terms = self.build_lexical_terms(state.parsed)
            if terms:
                lex_hits = await self.lexical.lexical_search(
                    state.user_id,
                    terms,
                    top_k=self.config.top_k,
                    flt=flt,
                    min_coverage=self.config.lexical_min_coverage,
                    query_text=query_text,
                )
        debug.lexical_hits = len(lex_hits)

        # ---- 融合 ----
        hits = self._fuse(vec_hits, lex_hits)
        debug.fused_hits = len(hits)

        # ---- 重排（阶段 3 W3-4）----
        # 重排接管的是"谁在最前面"，它**不增加候选** —— 召回不足应该去改召回，
        # 而不是指望重排把没召回的东西变出来。
        if self.reranker is not None:
            reranked = await self.reranker.rerank(query_text, hits, self.config.rerank_top_n)
            debug.reranked = reranked is not None
            if reranked:
                hits = reranked
            # 返回 None = 重排没做成（远程失败），保留原顺序，不算失败

        state.retrieve_debug = debug
        return hits[: self.config.top_k]

    # ---------------------------------------------------------------- 过滤
    @staticmethod
    def build_filter(parsed: ParsedQuery | None) -> StructuredFilter | None:
        """把解析结果翻译成结构化过滤条件。

        `degraded=True` 时**丢弃 LLM 给的 places / themes**（它们不可信），
        但保留规则算出的时间范围 —— 规则是确定性计算，不属于"猜"。
        """
        if parsed is None:
            return None

        date_start = date_end = None
        if parsed.time_range:
            try:
                date_start = date.fromisoformat(parsed.time_range["start"])
                date_end = date.fromisoformat(parsed.time_range["end"])
            except (KeyError, ValueError):
                # 时间范围格式不对：宁可不过滤，也不要静默漏召回
                date_start = date_end = None

        places = [] if parsed.degraded else list(parsed.places)
        themes = [] if parsed.degraded else list(parsed.themes)

        flt = StructuredFilter(
            date_start=date_start,
            date_end=date_end,
            places=places,
            themes=themes,
        )
        return None if flt.is_empty() else flt

    # ---------------------------------------------------------------- 查询串
    @staticmethod
    def build_search_text(state: PipelineState) -> str:
        """拼接检索文本：问句为主，关键词补语义。"""
        parts = [state.search_query]
        if state.parsed and state.parsed.keywords:
            parts.extend(state.parsed.keywords[:5])
        return " ".join(p for p in parts if p).strip()

    @staticmethod
    def build_lexical_terms(parsed: ParsedQuery | None) -> list[str]:
        """词面检索用的关键词。

        优先级：地名 > 主题 > 关键词。地名词最"值钱"——
        "乌镇"出现在正文里几乎必然是相关证据，而泛关键词（"哪些""时候"）只是噪声。

        顺序去重后返回，保证覆盖率分母稳定（同样的词不会被算两遍）。
        """
        if parsed is None:
            return []
        raw: list[str] = []
        if not parsed.degraded:
            raw.extend(parsed.places)
            raw.extend(parsed.themes)
        raw.extend(parsed.keywords)
        return list(dict.fromkeys(t.strip() for t in raw if t and t.strip()))

    # ---------------------------------------------------------------- 融合
    def _fuse(self, vec_hits: list[SearchHit], lex_hits: list[SearchHit]) -> list[SearchHit]:
        """两路融合的**分发口**：策略由 `config.fusion` 决定。

        两条策略的取舍写在各自的函数文档里。分发而不是二选一地硬编码，
        是为了让"换融合策略"变成一次配置变更，而不是一次代码改动 ——
        `make eval-ablation` 才能干净地比出谁更好。
        """
        if not lex_hits:
            return vec_hits
        if not vec_hits:
            return lex_hits
        if self.config.fusion == "rrf":
            # 加权 RRF：权重与 weighted 策略共用同一组配置，
            # 这样"语义强、词面弱"这个判断只需要在一个地方表达
            return reciprocal_rank_fusion(
                [vec_hits, lex_hits],
                k=max(1, int(self.config.rrf_k)),
                weights=[self.config.vector_weight, self.config.lexical_weight],
            )
        return self._fuse_weighted(vec_hits, lex_hits)

    def _fuse_weighted(
        self, vec_hits: list[SearchHit], lex_hits: list[SearchHit]
    ) -> list[SearchHit]:
        """两路融合：**各自归一化 → 加权求和**。

        为什么必须先归一化：向量分是余弦相似度（哈希向量下只有 0.05~0.13），
        词面分是覆盖率（0~1）。直接相加的话，向量路会被词面路彻底淹没 ——
        融合就变成了"词面单点决定"，完全失去混合的意义。
        """
        vec_norm = _min_max(vec_hits)
        lex_norm = _min_max(lex_hits)
        wv, wl = self.config.vector_weight, self.config.lexical_weight

        merged: dict[tuple, SearchHit] = {}
        scores: dict[tuple, float] = {}

        for hit in vec_hits:
            key = hit.ref.key
            merged.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + wv * vec_norm[key]
        for hit in lex_hits:
            key = hit.ref.key
            merged.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + wl * lex_norm[key]

        out: list[SearchHit] = []
        for key, hit in merged.items():
            out.append(
                SearchHit(
                    document_id=hit.document_id,
                    ref=hit.ref,
                    content=hit.content,
                    metadata={**hit.metadata, "fused_score": round(scores[key], 6)},
                    score=round(scores[key], 6),
                )
            )
        out.sort(key=lambda h: h.score, reverse=True)
        return out


def _competition_ranks(hits: list[SearchHit]) -> list[tuple[SearchHit, int]]:
    """按分数**分档**给名次：同分同名次（1224 式），不是 1234 式。

    为什么必须分档：词面覆盖率只有 1.0 / 0.5 / 0.0 三档，同一档内往往有一大批文档。
    用 1234 式排名时，同一档的第 2、第 3 个文档会**仅仅因为列表里的先后顺序**
    拿到更差的名次 —— 而这个先后顺序是 SQL 返回顺序，没有任何语义。
    实测这正是"排第一的正确文档被挤下去"的直接机制。

    分档之后，同档文档名次相同，谁前谁后交给**另一路**（向量路有细粒度分数）决定。
    返回的是 (hit, rank) 对，调用方不需要再排一次。
    """
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)
    pairs: list[tuple[SearchHit, int]] = []
    previous_score: float | None = None
    previous_rank = 0
    for position, hit in enumerate(ordered, start=1):
        if previous_score is not None and hit.score == previous_score:
            pairs.append((hit, previous_rank))
        else:
            pairs.append((hit, position))
            previous_rank = position
            previous_score = hit.score
    return pairs


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchHit]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[SearchHit]:
    """加权 RRF：`score(d) = Σ w_i / (k + rank_i)`，rank 从 1 开始且**按分数分档**。

    ## 为什么阶段 4 才上 RRF

    阶段 2 用的是归一化加权求和，它有一个隐含前提：**两路的分数可比**。
    这个前提靠 min-max 归一化勉强成立，但很脆：

    - 只有 1 条结果时归一化会返回 1.0（"它是最好的"），于是这一条的加权分
      恒等于权重上限，跟它到底有多像没关系；
    - 阶段 3 加了 reranker、阶段 4 又加了记忆检索，三路以上的分数
      量纲各不相同，归一化口径会越补越乱。

    RRF 用**排名**而不是分数，天然对量纲免疫。代价是丢掉"领先多少"的信息。

    ## 为什么必须加权（W4-3 真实数据）

    无差别 RRF（每路权重都是 1）在真实 embedding 下**打不过加权求和**：

    | 融合策略 | Recall@5 | MRR |
    | --- | --- | --- |
    | weighted（阶段 2） | 0.857 | **0.778** |
    | RRF 无权重 | 0.833 | 0.679 |

    原因：词面覆盖率只有 1.0 / 0.5 / 0.0 三档，**离散**。RRF 只看排名，
    于是一条覆盖率 0.5 的弱匹配与一条 1.0 的强匹配可能只差一两个名次，
    贡献几乎相同；而真实语义向量的分数是连续的、区分度很高，
    被这种"伪平等"一平均，头部就塌了。

    加权把每路的影响力显式交给配置（复用 `vector_weight` / `lexical_weight`），
    让"语义强、词面弱"这个事实体现在分数里，而不是被 RRF 的对称性抹平。

    ## k 为什么仍是 60

    k 越大名次差异越被抹平。实测把 k 从 60 提到 100/120 只能缓解症状：
    它同时把**好的区分度**也一起抹平了（头部与尾部的差距一起变小），
    MRR 该掉还是掉。真正该调的是权重 —— 所以 k 保持 RRF 原论文的 60，
    需要更平滑时用 `RETRIEVAL_RRF_K` 显式调，并在评测里对比后再定。
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(f"权重数量({len(weights)})与路数({len(ranked_lists)})不一致")

    scores: dict[tuple, float] = {}
    merged: dict[tuple, SearchHit] = {}

    for weight, hits in zip(weights, ranked_lists, strict=True):
        if weight <= 0:
            continue  # 权重 0 = 这一路不参与；不跳过会让 0 权重路的名次仍影响并列
        for hit, rank in _competition_ranks(hits):
            key = hit.ref.key
            merged.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)

    out: list[SearchHit] = []
    for key, hit in merged.items():
        out.append(
            SearchHit(
                document_id=hit.document_id,
                ref=hit.ref,
                content=hit.content,
                metadata={**hit.metadata, "fused_score": round(scores[key], 6)},
                score=round(scores[key], 6),
            )
        )
    out.sort(key=lambda h: h.score, reverse=True)
    return out


def _merge_keep_best(primary: list[SearchHit], extra: list[SearchHit]) -> list[SearchHit]:
    """把 extra 里"primary 没有"的条目补进去，重复的以 primary 为准。"""
    seen = {hit.ref.key for hit in primary}
    return primary + [hit for hit in extra if hit.ref.key not in seen]


def _min_max(hits: list[SearchHit]) -> dict[tuple, float]:
    """把一路的分数线性归一化到 0~1。

    只有一个元素时返回 1.0（它就是本路最好的），避免除以 0。
    全部相等时也返回 1.0 —— 此时"排名"没有信息量，给满分等价于"都在第一梯队"。
    """
    if not hits:
        return {}
    values = {hit.ref.key: hit.score for hit in hits}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-9:
        return {key: 1.0 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}
