"""阶段 3 W3-4：重排与 HNSW 回退的行为测试（全程内存，不需要数据库）。

守的是两类"看不见的失败"：

1. **重排改变了不该改变的东西**：关掉重排时顺序必须与阶段 2 逐条一致；
   远程重排失败时必须保留原顺序而不是丢证据。
2. **HNSW 静默返回 0 条**：这是设计文档第 241 行点名的风险。
   回退必须在内存替身上就能验证，否则只能等到真库上才发现问题。
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.agent.rag.filters import StructuredFilter
from app.agent.rag.reranker import NoOpReranker, RemoteReranker, _reorder, build_reranker
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.state import ParsedQuery, PipelineState
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.schemas.document import DocumentRef, SearchHit
from tests.fakes import InMemoryVectorSearch

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


async def _store(embedder, *hits: SearchHit) -> InMemoryVectorSearch:
    store = InMemoryVectorSearch()
    for hit in hits:
        await store.add(USER, hit, await embedder.embed_one(hit.content))
    return store


def _state(query: str) -> PipelineState:
    return PipelineState(user_id=USER, session_id="s", query=query)


# ---------------------------------------------------------------- 重排
async def test_noop_reranker_preserves_order_exactly() -> None:
    """关闭重排 = 阶段 2 行为：顺序必须逐条一致（test-degradation 的核心断言）。"""
    hits = [
        _hit("a", "第一条", score=0.9),
        _hit("b", "第二条", score=0.5),
        _hit("c", "第三条", score=0.1),
    ]
    result = await NoOpReranker().rerank("问题", hits, top_n=8)
    assert result is not None
    assert [h.ref.source_id for h in result] == ["a", "b", "c"]


async def test_remote_rerank_failure_keeps_all_evidence(monkeypatch) -> None:
    """重排失败只影响排序质量：证据**一条都不能少**，也不许抛错。"""
    settings = get_settings()

    async def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("模拟重排服务挂掉")

    monkeypatch.setattr("httpx.AsyncClient.post", _boom)
    monkeypatch.setattr("httpx.AsyncClient.__aenter__", _noop_aenter)
    monkeypatch.setattr("httpx.AsyncClient.__aexit__", _noop_aexit)

    hits = [_hit("a", "第一条"), _hit("b", "第二条")]
    result = await RemoteReranker(settings).rerank("问题", hits, top_n=2)
    assert result is None, "失败应当返回 None，由调用方保留原顺序"


def test_reorder_never_drops_hits() -> None:
    """外部接口返回的下标可能越界或缺漏：宁可顺序不完美，也不能静默丢证据。"""
    hits = [_hit("a", "一"), _hit("b", "二"), _hit("c", "三")]
    reordered = _reorder(hits, [2, 99])
    assert {h.ref.source_id for h in reordered} == {"a", "b", "c"}
    assert reordered[0].ref.source_id == "c"


async def test_build_reranker_defaults_to_noop(monkeypatch) -> None:
    """RERANK_ENABLED 默认 False（PROJECT.md §二、[8]）。"""
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        assert isinstance(build_reranker(), NoOpReranker)
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- HNSW
async def test_ann_search_filters_after_candidate_cutoff(embedder) -> None:
    """复现设计文档第 241 行的风险：先取近邻、后过滤 → 可能一条都不剩。

    构造是**刻意**的：与 query 最相似的那条（d1）不在时间窗内，
    符合时间窗的那条（d2）相似度排第二。top_k=1 × oversample=1 → 候选只有 d1 → 命中 0 条。
    同样的条件走精确扫描则能召回 d2 —— 这就是回退存在的理由。
    """
    store = await _store(
        embedder,
        _hit("d1", "古镇古镇古镇", date="2025-07-08"),
        _hit("d2", "秋天", date="2025-10-05"),
    )
    flt = StructuredFilter(date_start=date(2025, 10, 1), date_end=date(2025, 10, 31))
    vector = await embedder.embed_one("古镇")

    ann = await store.ann_search(USER, vector, top_k=1, oversample=1, flt=flt)
    exact = await store.similar_search(USER, vector, top_k=2, flt=flt)

    assert ann == [], "近似路径应当暴露'候选里没有符合条件的文档'"
    assert [h.ref.source_id for h in exact] == ["d2"], "精确扫描必须能召回它"


async def test_hnsw_falls_back_when_ann_returns_nothing(embedder) -> None:
    """ANN 返回 0 条时必须回退精确扫描 —— 本阶段最关键的一条断言。"""
    october_hits = [
        _hit("d1", "秋天的山", date="2025-10-03"),
        _hit("d2", "秋天的古镇", date="2025-10-05"),
    ]

    class StubAnnSearcher:
        """只暴露"ANN 空手而归"这一个事实，让回退决策被测得干净。"""

        async def similar_search(self, user_id, vector, *, top_k=8, source_types=None, flt=None):
            return october_hits[:top_k]

        async def ann_search(self, user_id, vector, *, top_k=8, oversample=4, flt=None):
            return []

    retriever = Retriever(
        StubAnnSearcher(),  # type: ignore[arg-type]
        embedder,
        RetrieverConfig(top_k=2, min_score=0.0, enable_hnsw=True, use_ann=True, filter_min_hits=1),
    )
    state = _state("古镇")
    state.parsed = ParsedQuery(
        time_range={"start": "2025-10-01", "end": "2025-10-31"}, time_source="rule"
    )

    result = await retriever.retrieve(state)

    assert state.retrieve_debug.index_path == "exact_fallback"
    assert [h.ref.source_id for h in result] == ["d1", "d2"], "回退后必须拿到精确扫描的结果"


async def test_exact_path_is_default(embedder) -> None:
    """默认不开 HNSW：index_path 必须是 exact（阶段 2 行为）。"""
    store = await _store(embedder, _hit("d1", "古镇"), _hit("d2", "海边"))
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=2, min_score=0.0))
    state = _state("古镇")

    await retriever.retrieve(state)

    assert state.retrieve_debug.index_path == "exact"
    assert state.retrieve_debug.reranked is False


async def _noop_aenter(self):  # noqa: ANN001, ANN202
    return self


async def _noop_aexit(self, *exc):  # noqa: ANN001, ANN002, ANN202
    return False


# ---------------------------------------------------------------- 强制精确扫描
async def test_use_ann_false_forces_exact_scan(embedder) -> None:
    """`use_ann=False` 时必须走精确扫描 —— 即使 searcher 有 ann_search 方法。

    这是本轮紧急修复的核心断言：小库上 HNSW 的近似误差足以让 Recall@5 归零，
    所以"允许走 ANN"必须是显式打开的第二道门，而不是有方法就用。
    """
    calls: list[str] = []

    class RecordingSearcher:
        async def similar_search(self, user_id, vector, *, top_k=8, source_types=None, flt=None):
            calls.append("exact")
            return [_hit("d1", "古镇")]

        async def ann_search(self, user_id, vector, *, top_k=8, oversample=4, flt=None):
            calls.append("ann")
            return []

    retriever = Retriever(
        RecordingSearcher(),  # type: ignore[arg-type]
        embedder,
        RetrieverConfig(top_k=2, min_score=0.0, use_ann=False, enable_hnsw=False),
    )
    state = _state("古镇")

    await retriever.retrieve(state)

    assert calls == ["exact"], f"应当只走精确扫描，实际调用：{calls}"
    assert state.retrieve_debug.index_path == "exact"


def _recording_searcher(calls: list[str]):
    """记录走了哪条索引路径的替身。

    `calls` 由参数传入而不是闭包捕获：在循环里定义闭包会绑到同一个变量，
    第二轮跑的时候第一轮的记录还挂着（ruff B023），断言就会时真时假。
    """

    class Searcher:
        async def similar_search(self, user_id, vector, *, top_k=8, source_types=None, flt=None):
            calls.append("exact")
            return [_hit("d1", "古镇")]

        async def ann_search(self, user_id, vector, *, top_k=8, oversample=4, flt=None):
            calls.append("ann")
            return []

    return Searcher()  # 返回实例：返回类的话 self 会被第一个实参吃掉


@pytest.mark.parametrize("kwargs", [{"enable_hnsw": True}, {"use_ann": True}])
async def test_both_gates_required_for_ann(embedder, kwargs) -> None:
    """两道门缺一不可：只开 enable_hnsw 或只开 use_ann 都不能走 ANN。"""
    calls: list[str] = []
    retriever = Retriever(
        _recording_searcher(calls),  # type: ignore[arg-type]
        embedder,
        RetrieverConfig(top_k=2, min_score=0.0, **kwargs),
    )
    await retriever.retrieve(_state("古镇"))
    assert calls == ["exact"], f"{kwargs} 仍走了近似检索：{calls}"


def test_use_ann_defaults_to_false_in_settings() -> None:
    """出厂设置就不该走 ANN：数据量没过万时近似索引是净损失。"""
    settings = get_settings().model_construct()
    assert settings.retrieval_use_ann is False
    from app.agent.rag.retriever import RetrieverConfig

    assert RetrieverConfig().use_ann is False
