"""离线索引：把「数据源」变成可检索的 Document + 向量。

职责边界（设计文档 §5.1）：
  - 本模块只做「切块 → 向量化 → 调 DocumentService 落库」
  - 不负责查询；调度与幂等重试由阶段 1 的 `jobs/` 负责

幂等性的关键：`Document` 的唯一键是 `(source_type, source_id, chunk_index)`，
所以同一份数据重复索引只会覆盖，不会堆积重复向量。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.rag.chunker import ChunkConfig, chunk_text
from app.core.cache import CacheClient, NullCache, build_cache, embedding_cache_key
from app.core.config import get_settings
from app.core.embedding import BaseEmbedder
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger
from app.schemas.document import DocumentRef, DocumentUpsert
from app.services.document_service import DocumentService
from app.utils.text import estimate_tokens

logger = get_logger(__name__)


@dataclass
class IndexResult:
    """一次索引的结果摘要，写日志与批处理统计都要用。"""

    source_type: str
    source_id: str
    chunks: int
    keywords_hit: bool = False

    def __str__(self) -> str:  # pragma: no cover - 日志用
        return f"{self.source_type}/{self.source_id} 写入 {self.chunks} 个 chunk"


class DocumentIndexer:
    """把一个数据源的文本变成向量文档。"""

    def __init__(
        self,
        session: AsyncSession,
        embedder: BaseEmbedder,
        *,
        chunk_config: ChunkConfig | None = None,
        cache: CacheClient | None = None,
    ) -> None:
        self.service = DocumentService(session)
        self.embedder = embedder
        self.chunk_config = chunk_config
        #: 阶段 3 W3-3 用途 ②：embedding 结果缓存。
        #: 关闭 `cache` 开关时用 NullCache —— "不缓存"必须是显式状态，
        #: 否则同一份代码在有/无 Redis 的机器上表现不同，问题无法复现。
        settings = get_settings()
        self.cache: CacheClient = cache or (
            build_cache(settings) if is_enabled("cache", settings) else NullCache()
        )
        # embedding 缓存没有自然过期时间（内容不变就永远有效），靠 LRU 淘汰；
        # 换模型时 key 里的 model_version 变了，自然不会误读旧向量。
        self.cache_ttl_sec: int | None = None
        #: 命中/未命中计数：W3-3 的验收要看"第二次跑批的 provider 调用数"
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    async def index_source(
        self,
        *,
        user_id: uuid.UUID,
        source_type: str,
        source_id: str,
        text: str,
        metadata: dict | None = None,
    ) -> IndexResult:
        """索引单个数据源（幂等：重复调用只会覆盖）。"""
        base_meta = metadata or {}
        chunks = chunk_text(text, self.chunk_config)
        if not chunks:
            return IndexResult(source_type, source_id, 0)

        items = [
            DocumentUpsert(
                ref=DocumentRef(source_type=source_type, source_id=source_id, chunk_index=i),
                content=chunk,
                metadata=base_meta,
                token_count=estimate_tokens(chunk),
                char_len=len(chunk),
            )
            for i, chunk in enumerate(chunks)
        ]

        vectors = await self._embed_with_cache([item.content for item in items])
        await self.service.upsert_many(
            user_id, items, vectors, embedding_model=self.embedder.model_version
        )
        # 若这次切出的块数比上次少，清理尾部残留（例如原文被缩短）
        await self.service.delete_stale_chunks(source_type, source_id, len(items), user_id=user_id)
        return IndexResult(source_type, source_id, len(items))

    async def _embed_with_cache(self, texts: list[str]) -> list[list[float]]:
        """带缓存的向量化（阶段 3 W3-3 用途 ②）。

        ## 为什么缓存 embedding 而不是缓存整篇文档

        重索引（`make reindex`）时绝大多数 chunk 的内容**并没有变**：
        改了一个错别字，只有那一个 chunk 需要重新算向量。
        按内容哈希缓存，重复跑批的 provider 调用数应当降到 **0**，
        这是 W3-3 出口条件之一。

        ## 为什么按 `内容 + 模型` 而不是只按内容哈希

        换 embedding 模型后，同一段文字的向量完全不同。
        只按内容哈希会让新模型读到旧模型的向量 —— 这就是"脏向量"，
        而脏向量的表现是"检索结果莫名变差"，极难归因。
        """
        if self.embedder.provider == "hashing" or not texts:
            # 本地哈希向量不花钱，缓存它没有意义，反而白白占内存
            return await self.embedder.embed(texts)

        keys = [embedding_cache_key(text, self.embedder.model_version) for text in texts]
        vectors: list[list[float] | None] = []
        missing: list[int] = []

        for index, key in enumerate(keys):
            cached = await self._cache_get(key)
            if cached is None:
                missing.append(index)
                vectors.append(None)
            else:
                vectors.append(cached)
        self.cache_hits += len(keys) - len(missing)
        self.cache_misses += len(missing)

        if missing:
            computed = await self.embedder.embed([texts[i] for i in missing])
            for index, vector in zip(missing, computed, strict=True):
                vectors[index] = vector
                await self._cache_set(keys[index], vector)

        return [vector for vector in vectors if vector is not None]

    async def _cache_get(self, key: str) -> list[float] | None:
        try:
            raw = await self.cache.get(key)
        except Exception as exc:  # noqa: BLE001 - 缓存是旁路
            logger.warning("embedding 缓存读取失败，按 miss 处理：%s", exc)
            return None
        if raw is None:
            return None
        try:
            loaded = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
        return loaded if isinstance(loaded, list) else None

    async def _cache_set(self, key: str, vector: list[float]) -> None:
        try:
            await self.cache.set(key, json.dumps(vector), self.cache_ttl_sec)
        except Exception as exc:  # noqa: BLE001 - 写失败不影响本次结果
            logger.warning("embedding 缓存写入失败（不影响本次结果）：%s", exc)
