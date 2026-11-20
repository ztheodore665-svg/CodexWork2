"""端到端管道测试（mock LLM + 本地哈希向量 + 内存检索）。

这套测试的意义：在没有 Docker、没有 API Key 的机器上，
依然能验证六个环节的串联、引用是否生成、拒答是否触发、trace 是否落盘。
它验证的是**工程链路**，不是检索质量（质量由真实 embedding + `make eval` 验证）。
"""

from __future__ import annotations

import pytest
from fakes import InMemoryVectorSearch, load_demo_corpus

from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.state import PipelineState
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.llm import build_llm
from app.utils.citation import citation_rate


@pytest.fixture
async def store(user_id) -> InMemoryVectorSearch:
    """把演示语料向量化塞进内存检索器（替代 pgvector）。"""
    return await load_demo_corpus(user_id, build_embedder(get_settings()))


def _build_pipeline(search: InMemoryVectorSearch, *, min_score: float) -> AgentPipeline:
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(search, embedder, RetrieverConfig(top_k=5, min_score=min_score))
    return AgentPipeline(build_llm(settings), retriever, max_context_chars=2000, settings=settings)


async def test_retriever_ranks_lexically_related_document_first(store, user_id):
    """检索排序 sanity check。

    注意：这里用「关键词式」提问而不是自然语言提问。哈希向量只能捕捉字面重合，
    理解不了"去年秋天"这类表达；自然语言提问的召回质量由真实 embedding + `make eval` 负责。
    """
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=0.05))
    state = PipelineState(user_id=user_id, session_id="test", query="乌镇 西塘 摇橹船 夜景")

    hits = await retriever.retrieve(state)

    assert hits, "应当能检索到文档"
    top_ids = [hit.ref.source_id for hit in hits[:3]]
    assert {"p10", "t1"} & set(top_ids), f"前三条应命中水乡古镇相关记录，实际：{top_ids}"


async def test_pipeline_answers_with_citations(store, user_id):
    # 哈希向量的余弦分数整体偏低，这里放宽阈值只看链路是否通顺
    pipeline = _build_pipeline(store, min_score=0.05)
    trace = Trace(session=None, user_id=user_id, session_id="test", query="乌镇 西塘 摇橹船 夜景")

    state = await pipeline.run(
        user_id=user_id,
        session_id="test",
        query="乌镇 西塘 摇橹船 夜景",
        trace=trace,
    )

    assert state.refused is False, f"不该拒答：{state.answer}"
    assert state.blocks, "必须检索到证据"
    assert state.citations, "答案必须带引用"
    assert citation_rate(state.answer) == 1.0, "self_check 应保证引用率 100%"


async def test_pipeline_refuses_when_no_evidence(store, user_id):
    """用极高的相似度阈值模拟"库里确实没有这条记录"。"""
    pipeline = _build_pipeline(store, min_score=0.99)
    trace = Trace(session=None, user_id=user_id, session_id="test", query="我攀登珠峰用了几天？")

    state = await pipeline.run(
        user_id=user_id,
        session_id="test",
        query="我攀登珠峰用了几天？",
        trace=trace,
    )

    assert state.refused is True
    assert state.refuse_reason == "no_evidence"
    assert state.citations == []
    assert citation_rate(state.answer) == 1.0  # 拒绝话术里没有事实句


async def test_trace_records_every_step(store, user_id):
    settings = get_settings()
    pipeline = _build_pipeline(store, min_score=0.05)
    trace = Trace(
        session=None, user_id=user_id, session_id="test", query="我去过哪些爬山看云海的地方？"
    )

    await pipeline.run(
        user_id=user_id,
        session_id="test",
        query="我去过哪些爬山看云海的地方？",
        trace=trace,
    )

    step_types = [step["step_type"] for step in trace.steps]
    for expected in (
        "rewrite",
        "query_parse",
        "retrieve",
        "build_context",
        "generate",
        "self_check",
    ):
        assert expected in step_types, f"trace 缺少环节 {expected}"

    # JSONL 必须真的写出来了（可通过 make cli 之后 grep trace 目录验证）
    jsonl_files = list(settings.resolved_trace_dir.glob("*.jsonl"))
    assert jsonl_files, "应至少生成一个 trace 文件"
