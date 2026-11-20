"""阶段 3 W3-3 用途 ②：embedding 结果缓存。

出口条件是"第二次跑批的 provider 调用数 = 0"。这条能省下真金白银 ——
`make reindex` 全量重算是最贵的一次操作，而绝大多数 chunk 的内容其实没变。
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.rag.indexer import DocumentIndexer
from app.core.config import get_settings
from app.core.embedding import BaseEmbedder
from tests.fakes import InMemoryVectorSearch

USER = uuid.uuid4()


class CountingEmbedder(BaseEmbedder):
    """数一数到底调了几次 provider —— 出口条件就是看这个数字。"""

    settings = get_settings()

    #: `dim` 是基类上的只读属性，这里只记录调用，不覆盖它
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def provider(self) -> str:
        return "fake-remote"

    @property
    def model(self) -> str:
        return "fake-model"

    @property
    def model_version(self) -> str:
        return "fake-model@v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t) % 7) / 7 + 0.01 * i for i in range(4)] for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def _indexer(embedder: CountingEmbedder, cache) -> DocumentIndexer:
    return DocumentIndexer(
        None,  # type: ignore[arg-type] - 只测缓存，不落库
        embedder,
        cache=cache,
    )


async def test_second_pass_calls_provider_zero_times() -> None:
    """W3-3 出口条件：同样的内容第二次跑批，provider 调用数必须是 0。"""
    from app.core.cache import InMemoryCache

    cache = InMemoryCache()
    embedder = CountingEmbedder()
    indexer = _indexer(embedder, cache)

    texts = ["第一段文本", "第二段文本", "第三段文本"]
    first = await indexer._embed_with_cache(texts)
    second = await indexer._embed_with_cache(texts)

    assert first == second, "命中缓存不能改变结果"
    assert len(embedder.calls) == 1, f"第二次应当完全走缓存，实际调用 {len(embedder.calls)} 次"
    # 第一次 3 条全未命中，第二次 3 条全命中
    assert indexer.cache_hits == 3
    assert indexer.cache_misses == 3


async def test_model_change_does_not_reuse_stale_vectors() -> None:
    """换模型必须算新向量：读到旧模型的向量就是"脏向量"，检索质量会莫名变差。"""
    from app.core.cache import InMemoryCache

    cache = InMemoryCache()
    indexer = _indexer(CountingEmbedder(), cache)
    await indexer._embed_with_cache(["同一段文本"])

    class NewModel(CountingEmbedder):
        @property
        def model_version(self) -> str:
            return "fake-model@v2"

    other = _indexer(NewModel(), cache)
    await other._embed_with_cache(["同一段文本"])

    assert len(other.embedder.calls) == 1, "换了模型就不能复用旧向量"


async def test_local_hashing_embedder_is_not_cached() -> None:
    """本地哈希向量不花钱，缓存它只是白白占内存。"""
    from app.core.cache import InMemoryCache
    from app.core.embedding import build_embedder

    cache = InMemoryCache()
    indexer = DocumentIndexer(None, build_embedder(get_settings()), cache=cache)  # type: ignore[arg-type]
    await indexer._embed_with_cache(["文本"])

    assert cache.size == 0


async def test_cache_failure_still_returns_vectors(monkeypatch) -> None:
    """缓存挂了不该让索引失败：它是旁路设施。"""
    from app.core.cache import InMemoryCache

    cache = InMemoryCache()
    monkeypatch.setattr(cache, "get", lambda _key: (_ for _ in ()).throw(RuntimeError("缓存挂了")))
    embedder = CountingEmbedder()
    indexer = _indexer(embedder, cache)

    vectors = await indexer._embed_with_cache(["文本"])

    assert len(vectors) == 1
    assert len(embedder.calls) == 1


def test_in_memory_search_still_satisfies_ports() -> None:
    """顺带确认内存替身实现了新增的 ann_search（端口变更没漏改替身）。"""
    assert hasattr(InMemoryVectorSearch, "ann_search")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
