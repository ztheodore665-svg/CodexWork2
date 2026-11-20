"""测试替身：语料装载 + 内存检索的 re-export。

`InMemoryVectorSearch` 的实现已经搬到 `app/agent/rag/inmemory.py`
（原因见那里的模块文档：评测器与 CLI 的离线模式也要用它）。
这里保留 re-export，既有用例的 `from fakes import InMemoryVectorSearch` 不受影响。

留在本文件的只有 `load_demo_corpus` —— 它依赖 `get_settings()` 等测试夹具，
放在测试侧更合适。
"""

from __future__ import annotations

import json
import uuid

from app.agent.rag.inmemory import (  # noqa: F401 - 兼容既有用例的 `from fakes import ...`
    CORPUS_FILES,
    InMemoryVectorSearch,
)
from app.core.config import get_settings
from app.schemas.document import DocumentRef, SearchHit


async def load_demo_corpus(
    user_id: uuid.UUID,
    embedder,
    target: InMemoryVectorSearch | None = None,
) -> InMemoryVectorSearch:
    """把演示语料向量化塞进内存检索器（替代 pgvector）。

    放在 `fakes.py` 而不是某个测试文件里，是为了让多个测试共用同一份装载逻辑：
    语料装载一旦被改坏，所有端到端用例一起红，而不是"有的红有的不红"。
    """
    store = target or InMemoryVectorSearch()
    settings = get_settings()
    for name in CORPUS_FILES:
        path = settings.backend_dir / "data" / name
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            content = record["content"]
            hit = SearchHit(
                ref=DocumentRef(
                    source_type=record["source_type"],
                    source_id=str(record["source_id"]),
                    chunk_index=0,
                ),
                content=content,
                metadata=record.get("metadata") or {},
                score=1.0,
            )
            await store.add(user_id, hit, await embedder.embed_one(content))
    return store
