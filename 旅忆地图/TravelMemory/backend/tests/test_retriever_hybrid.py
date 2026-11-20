"""混合检索编排（W2-2 的核心行为）。

全部用内存替身跑，不需要数据库 —— 检索**编排**的正确性与存储无关，
把编排测稳了，剩下要真实库验证的就只有 SQL 那一层。
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.rag.filters import StructuredFilter
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.state import ParsedQuery, PipelineState
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.schemas.document import DocumentRef, SearchHit
from tests.fakes import InMemoryVectorSearch, load_demo_corpus

USER = uuid.uuid4()


def _hit(source_id: str, content: str, **metadata) -> SearchHit:
    return SearchHit(
        ref=DocumentRef(source_type="day", source_id=source_id, chunk_index=0),
        content=content,
        metadata=metadata,
    )


@pytest.fixture
def embedder():
    return build_embedder(get_settings())


async def _vec(embedder, text: str) -> list[float]:
    """查询向量与检索器同源，保证"语义相似"这件事在测试里是真的。"""
    return await embedder.embed_one(text)


@pytest.mark.asyncio
async def test_lexical_path_pulls_in_exact_name_match(embedder) -> None:
    """向量对专名很笨，词面路必须能补上"字面出现了"的那条。"""
    searcher = InMemoryVectorSearch()
    await searcher.add(
        USER,
        _hit("d1", "在嘉兴乌镇坐摇橹船", cities=["嘉兴"], places=["乌镇"]),
        await _vec(embedder, "乌镇"),
    )
    await searcher.add(
        USER,
        _hit("d2", "在杭州西湖边散步", cities=["杭州"], places=["西湖"]),
        await _vec(embedder, "西湖"),
    )

    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.0, enable_filter=False),
        lexical=searcher,
    )
    state = PipelineState(user_id=USER, session_id="s", query="乌镇")
    state.parsed = ParsedQuery(places=["乌镇"])

    hits = await retriever.retrieve(state)
    assert hits, "至少要有命中"
    assert hits[0].ref.source_id == "d1"


@pytest.mark.asyncio
async def test_filter_fallback_when_filtered_result_is_too_thin(embedder) -> None:
    """过滤后命中太少 → 必须补一次不过滤的检索（宁可多召回，不要漏召回）。"""
    searcher = InMemoryVectorSearch()
    await searcher.add(
        USER,
        _hit("d1", "秋天的古镇", date="2025-10-03", places=["乌镇"]),
        await _vec(embedder, "古镇"),
    )
    await searcher.add(
        USER,
        _hit("d2", "夏天的海边", date="2025-07-08", places=["厦门"]),
        await _vec(embedder, "海边"),
    )

    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.0, filter_min_hits=3),
        lexical=None,
    )
    state = PipelineState(user_id=USER, session_id="s", query="古镇")
    # 故意把时间窗指到一个没有文档的范围，逼出兜底
    state.parsed = ParsedQuery(
        time_range={"start": "2019-01-01", "end": "2019-12-31"}, time_source="rule"
    )

    hits = await retriever.retrieve(state)
    assert state.retrieve_debug.filter_fallback is True
    assert {h.ref.source_id for h in hits} == {"d1", "d2"}


@pytest.mark.asyncio
async def test_structural_filter_narrows_candidates(embedder) -> None:
    searcher = InMemoryVectorSearch()
    await searcher.add(
        USER, _hit("d1", "秋天的古镇", date="2025-10-03"), await _vec(embedder, "古镇")
    )
    await searcher.add(
        USER, _hit("d2", "夏天的海边", date="2025-07-08"), await _vec(embedder, "古镇")
    )

    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.0, filter_min_hits=0),
        lexical=None,
    )
    state = PipelineState(user_id=USER, session_id="s", query="古镇")
    state.parsed = ParsedQuery(
        time_range={"start": "2025-09-01", "end": "2025-11-30"}, time_source="rule"
    )
    hits = await retriever.retrieve(state)
    assert [h.ref.source_id for h in hits] == ["d1"]


@pytest.mark.asyncio
async def test_fusion_is_not_dominated_by_one_path(embedder) -> None:
    """两路量纲不同（相似度 0.0x vs 覆盖率 1.0），归一化前相加会毁掉混合。"""
    searcher = InMemoryVectorSearch()
    await searcher.add(
        USER, _hit("a", "语义很接近但没提到关键词"), await _vec(embedder, "乌镇 古镇 水乡")
    )
    await searcher.add(
        USER, _hit("b", "乌镇 古镇 水乡 都出现了"), await _vec(embedder, "完全不相关的文本")
    )

    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(
            top_k=5,
            min_score=0.0,
            vector_weight=0.5,
            lexical_weight=0.5,
            lexical_min_coverage=0.5,
        ),
        lexical=searcher,
    )
    state = PipelineState(user_id=USER, session_id="s", query="乌镇 古镇 水乡")
    state.parsed = ParsedQuery(places=["乌镇", "古镇", "水乡"])

    hits = await retriever.retrieve(state)
    scores = {h.ref.source_id: h.score for h in hits}
    # 两条都在：说明不是某一路吃掉另一路
    assert set(scores) == {"a", "b"}
    # 各自归一化后再加权，最好者不会超过 1.0
    assert max(scores.values()) <= 1.0


@pytest.mark.asyncio
async def test_min_score_still_gates_refusal(embedder) -> None:
    """融合不能绕过拒答闸门：向量分太低的证据不该进上下文。"""
    searcher = InMemoryVectorSearch()
    await searcher.add(USER, _hit("d1", "毫无关系的文本"), await _vec(embedder, "别的"))
    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.99, enable_filter=False),
        lexical=None,
    )
    state = PipelineState(user_id=USER, session_id="s", query="乌镇")
    hits = await retriever.retrieve(state)
    assert hits == []


@pytest.mark.asyncio
async def test_structured_filter_is_passed_through(embedder) -> None:
    """确认 flt 真的被传到底层（而不是被静默忽略）。"""
    captured: dict = {}

    class Recorder:
        async def similar_search(self, user_id, vector, *, top_k=8, source_types=None, flt=None):
            captured["flt"] = flt
            return []

    # filter_min_hits=0 关掉兜底，否则第二次（不过滤的）调用会把 captured 覆盖成 None
    retriever = Retriever(Recorder(), embedder, RetrieverConfig(filter_min_hits=0))
    state = PipelineState(user_id=USER, session_id="s", query="古镇")
    state.parsed = ParsedQuery(places=["乌镇"], themes=["古镇"])

    await retriever.retrieve(state)
    assert isinstance(captured["flt"], StructuredFilter)
    assert captured["flt"].places == ["乌镇"]


@pytest.mark.asyncio
async def test_ablation_harness_runs_without_database(embedder) -> None:
    """消融实验脚本本身要能跑通。

    `make eval --ablation` 是阶段 2 的出口判定，但它需要真实数据库；
    在没容器的机器上先跑一遍内存版，能提前发现"脚本根本跑不起来"这类问题
    （比如 `RetrieverConfig` 少个参数、拼写错误），而不是等到有库时才炸。
    """
    from app.agent.eval.dataset import load_dataset
    from app.agent.eval.runner import run_ablation

    settings = get_settings()
    store = await load_demo_corpus(USER, embedder)
    items = load_dataset()
    assert items, "演示评测集不能为空"

    # 只为验证"能跑完"：不断言指标高低（哈希向量下的 Recall 没有参考价值）
    await run_ablation(
        items, user_id=USER, k=5, embedder=embedder, searcher=store, settings=settings
    )


@pytest.mark.asyncio
async def test_time_range_intersection_not_containment(embedder) -> None:
    """一趟行程是 10-02~10-05，问"10 月 3 日"必须命中（相交，不是包含）。"""
    searcher = InMemoryVectorSearch()
    await searcher.add(
        USER,
        _hit("t1", "江南古镇之行", date_start="2025-10-02", date_end="2025-10-05"),
        await _vec(embedder, "古镇"),
    )
    retriever = Retriever(
        searcher,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.0, filter_min_hits=0),
        lexical=None,
    )
    state = PipelineState(user_id=USER, session_id="s", query="古镇")
    state.parsed = ParsedQuery(
        time_range={"start": "2025-10-03", "end": "2025-10-03"}, time_source="rule"
    )
    hits = await retriever.retrieve(state)
    assert [h.ref.source_id for h in hits] == ["t1"]
