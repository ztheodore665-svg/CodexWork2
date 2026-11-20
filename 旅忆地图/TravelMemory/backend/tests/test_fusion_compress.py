"""W4-3：RRF 融合 + 上下文压缩。

出口条件：
  1. RRF 排序逻辑可复算（`test_rrf*`）
  2. 压缩按「证据 > 历史对话 > 记忆」优先级，且有降级保护（`test_compress*`）
"""

from __future__ import annotations

import pytest

from app.agent.rag.context_builder import (
    HISTORY_HEADER,
    MIN_CONTEXT_CHARS,
    PREFERENCE_HEADER,
    TRIP_CONTEXT_HEADER,
    build_context,
)
from app.agent.rag.retriever import Retriever, RetrieverConfig, reciprocal_rank_fusion
from app.schemas.document import DocumentRef, SearchHit
from app.schemas.memory import MemoryContext


def _hit(source_id: str, content: str = "内容", score: float = 0.9) -> SearchHit:
    return SearchHit(
        ref=DocumentRef(source_type="trip", source_id=source_id),
        content=content,
        metadata={"city": "嘉兴"},
        score=score,
    )


# ---------------------------------------------------------------- RRF


def test_rrf_score_is_one_over_k_plus_rank():
    """`score = Σ 1/(k+rank)`，rank 从 1 开始 —— 手算一遍钉死。

    两条必须给**不同分数**：同分文档在竞争排名下同名次（见下一个用例），
    用默认同分构造就分不出 rank 1 与 rank 2 了。
    """
    a = [_hit("a", score=0.9), _hit("b", score=0.8)]
    out = reciprocal_rank_fusion([a], k=60)
    assert out[0].score == pytest.approx(1 / 61, abs=1e-6)
    assert out[1].score == pytest.approx(1 / 62, abs=1e-6)


def test_rrf_tied_scores_share_the_same_rank():
    """同分同名次（1224 式）—— 这是修正 RRF 噪声的核心机制。

    词面覆盖率只有 1.0/0.5/0.0 三档，同一档往往一大批文档。
    若用 1234 式，同档第 2、第 3 个会仅仅因为 SQL 返回顺序拿到更差名次，
    而这个顺序没有任何语义 —— 实测这正是"排第一的正确文档被挤下去"的机制。
    """
    a = [_hit("a", score=0.9), _hit("b", score=0.9), _hit("c", score=0.5)]
    out = reciprocal_rank_fusion([a], k=60)
    by_id = {h.ref.source_id: h.score for h in out}
    assert by_id["a"] == by_id["b"] == pytest.approx(1 / 61, abs=1e-6)
    # 1224 式：c 是第 3 个位置，名次取**位置号** 3（不是顺延成 2）——
    # 这正是"同分不占后续名次"的标准竞争排名，与 Cormack et al. 2009 一致。
    assert by_id["c"] == pytest.approx(1 / 63, abs=1e-6)


def test_rrf_sums_across_lists():
    # a 在两路都是第一；b 只在第一路排第二（给不同分数，否则同分同名次）
    fused = reciprocal_rank_fusion(
        [[_hit("a", score=0.9), _hit("b", score=0.8)], [_hit("a", score=0.9)]], k=60
    )
    by_id = {hit.ref.source_id: hit.score for hit in fused}
    assert by_id["a"] == pytest.approx(1 / 61 + 1 / 61, abs=1e-6)
    assert by_id["b"] == pytest.approx(1 / 62, abs=1e-6)
    assert by_id["a"] > by_id["b"]


def test_rrf_rewards_agreement_between_routes():
    """互相印证的条目必须排在只被一路召回的条目之前 —— 这是 RRF 的核心价值。"""
    vector = [_hit("only_vector", score=0.99), _hit("both", score=0.5)]
    lexical = [_hit("both", score=0.2)]
    fused = reciprocal_rank_fusion([vector, lexical], k=60)
    assert fused[0].ref.source_id == "both"


def test_rrf_is_scale_invariant():
    """RRF 只看排名：分数整体放大 100 倍，排序必须完全不变。"""
    small = [_hit("x", score=0.01), _hit("y", score=0.02), _hit("z", score=0.03)]
    big = [_hit("x", score=1.0), _hit("y", score=2.0), _hit("z", score=3.0)]
    left = [h.ref.source_id for h in reciprocal_rank_fusion([small])]
    right = [h.ref.source_id for h in reciprocal_rank_fusion([big])]
    assert left == right


def test_larger_k_flattens_rank_differences():
    """k 越大，名次带来的分差越小（趋近"进榜即同分"）。"""
    hits = [_hit("first", score=0.9), _hit("second", score=0.8)]
    tight = reciprocal_rank_fusion([hits], k=1)
    loose = reciprocal_rank_fusion([hits], k=1000)
    gap_tight = tight[0].score - tight[1].score
    gap_loose = loose[0].score - loose[1].score
    assert gap_tight > gap_loose


def test_rrf_default_k_is_60():
    fused = reciprocal_rank_fusion([[_hit("a")]])
    assert fused[0].score == pytest.approx(1 / 61, abs=1e-6)


def test_retriever_dispatch_rrf_by_config():
    """融合策略由配置决定：换策略是改配置，不是改代码。"""
    searcher = _FakeSearcher()
    retriever = Retriever(searcher, _FakeEmbedder(), RetrieverConfig(fusion="rrf"))
    fused = retriever._fuse(
        [_hit("v1", score=0.9), _hit("v2", score=0.8)],
        [_hit("v2", score=0.9), _hit("l1", score=0.8)],
    )
    assert fused[0].ref.source_id == "v2", "两路都命中的应排第一"
    # 加权 RRF：权重与 weighted 策略共用（默认 0.65 / 0.35），不是等权的 1/61+1/62。
    # v2 = 向量第 2 名 + 词面第 1 名；v1 = 只有向量第 1 名。
    wv, wl = retriever.config.vector_weight, retriever.config.lexical_weight
    assert fused[0].score == pytest.approx(wv / 62 + wl / 61, abs=1e-6)


def test_retriever_weighted_is_still_the_default():
    searcher = _FakeSearcher()
    retriever = Retriever(searcher, _FakeEmbedder())
    assert retriever.config.fusion == "weighted"
    fused = retriever._fuse([_hit("a", score=0.9)], [_hit("b", score=0.4)])
    assert {h.ref.source_id for h in fused} == {"a", "b"}


class _FakeSearcher:
    async def similar_search(self, *args, **kwargs):  # pragma: no cover - 本用例不走检索
        return []


class _FakeEmbedder:
    async def embed_one(self, text):  # pragma: no cover
        return [0.0]


# ---------------------------------------------------------------- 上下文压缩


def test_memory_and_history_are_appended_in_fixed_order():
    memory = MemoryContext(preferences=["- 用户不喜欢人多"], trip_context=["- 2025 秋·乌镇"])
    built = build_context(
        [_hit("t1")], memory=memory, history=[{"role": "user", "content": "去过乌镇吗"}]
    )
    assert built.text.index(PREFERENCE_HEADER) < built.text.index(TRIP_CONTEXT_HEADER)
    assert built.text.index(TRIP_CONTEXT_HEADER) < built.text.index("[1]")
    assert built.text.index("[1]") < built.text.index(HISTORY_HEADER)


def test_memory_is_dropped_first_when_budget_is_tight():
    """优先级 证据 > 历史 > 记忆：预算不够先牺牲记忆。"""
    memory = MemoryContext(preferences=["- " + "偏好" * 200])
    history = [{"role": "user", "content": "一句话历史"}]
    built = build_context([_hit("t1", "内容" * 100)], max_chars=400, memory=memory, history=history)

    assert built.blocks, "证据必须保住"
    assert PREFERENCE_HEADER not in built.text
    assert PREFERENCE_HEADER in built.dropped_parts


def test_history_is_dropped_before_evidence():
    memory = MemoryContext(preferences=["- 用户不喜欢人多"])
    history = [{"role": "user", "content": "历史" * 300}]
    built = build_context([_hit("t1", "内容" * 100)], max_chars=400, memory=memory, history=history)
    assert built.blocks
    assert HISTORY_HEADER not in built.text
    assert HISTORY_HEADER in built.dropped_parts
    # 记忆比历史更"便宜"，预算够它
    assert PREFERENCE_HEADER in built.text


def test_evidence_survives_even_when_memory_and_history_are_huge():
    memory = MemoryContext(preferences=["- " + "偏好" * 500], trip_context=["- " + "行程" * 500])
    history = [{"role": "user", "content": "历史" * 500}]
    built = build_context([_hit("t1", "内容" * 50)], max_chars=600, memory=memory, history=history)
    assert len(built.blocks) >= 1
    assert PREFERENCE_HEADER not in built.text
    assert HISTORY_HEADER not in built.text


def test_tiny_budget_still_returns_one_block():
    """降级保护：预算配成 0 也不能让每条问答变成拒答。"""
    built = build_context([_hit("t1", "内容" * 100)], max_chars=0)
    assert len(built.blocks) == 1
    assert built.text
    assert len(built.text) >= MIN_CONTEXT_CHARS // 2 or built.text.startswith("[1]")


def test_negative_budget_does_not_crash():
    built = build_context([_hit("t1", "内容" * 50)], max_chars=-100, memory=None, history=None)
    assert len(built.blocks) == 1


def test_broken_memory_object_degrades_instead_of_raising():
    """降级保护（PROJECT.md §二、[8]）：记忆装配炸了也不能带崩整条问答。"""

    class _Boom:
        def is_empty(self):  # noqa: ANN202
            return False

        @property
        def preferences(self):
            raise RuntimeError("记忆源脏了")

        @property
        def trip_context(self):
            raise RuntimeError("记忆源脏了")

    built = build_context([_hit("t1")], memory=_Boom())  # type: ignore[arg-type]
    assert len(built.blocks) == 1
    assert PREFERENCE_HEADER not in built.text


def test_memory_items_are_dropped_whole_not_truncated():
    """半句话的偏好比没有更糟：宁可整条丢，也不截断。"""
    memory = MemoryContext(preferences=["- " + "长偏好" * 100, "- 短偏好"])
    built = build_context([_hit("t1", "内容" * 100)], max_chars=400, memory=memory)
    assert "短偏好" in built.text
    assert "长偏好" * 100 not in built.text
    assert built.dropped_memory_items >= 1


def test_history_citations_are_stripped():
    """历史里的 [n] 指的是**上一轮**证据，留着会让 self_check 报越界。"""
    history = [{"role": "assistant", "content": "你去过乌镇[3]。"}]
    built = build_context([_hit("t1")], history=history)
    assert "[3]" not in built.text
    assert "你去过乌镇" in built.text


def test_no_evidence_means_empty_context_even_with_memory():
    memory = MemoryContext(preferences=["- 用户不喜欢人多"])
    built = build_context([], memory=memory, history=[{"role": "user", "content": "x"}])
    assert built.text == ""
    assert built.blocks == []


def test_no_memory_no_history_keeps_stage3_behaviour():
    """`make test-degradation` 的等价物：不带记忆时文本与阶段 3 一致。"""
    built = build_context([_hit("t1")], max_chars=4000)
    assert built.text.startswith("[1]")
    assert PREFERENCE_HEADER not in built.text
    assert HISTORY_HEADER not in built.text
    assert built.dropped_parts == []
