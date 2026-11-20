"""RAG 子包：索引、检索、上下文构建。

与 `nodes/` 的分工：这里是可复用的策略，节点只负责编排与埋点。

## 为什么 `DocumentIndexer` 是惰性导出

`indexer.py` 需要 `services/document_service.py`（它要写库），而
`document_service.py` 又要 `rag/filters.py` 的 `StructuredFilter`。
如果这里直接 `from app.agent.rag.indexer import DocumentIndexer`，
那么「先 import `app.services`」就会走成：
`app.services` → `document_service` → `app.agent.rag` → `indexer` → `app.services.document_service`
（此时该模块还没执行完）→ `ImportError: cannot import name 'DocumentService'`。

改成 PEP 562 的 `__getattr__` 之后，`app.agent.rag.DocumentIndexer` 依然可用，
但只在**真的被访问**时才导入，环就从"导入期"推迟到"使用期"，不再成立。
"""

from typing import TYPE_CHECKING

from app.agent.rag.chunker import ChunkConfig, chunk_text
from app.agent.rag.context_builder import BuiltContext, build_context
from app.agent.rag.retriever import Retriever, RetrieverConfig

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from app.agent.rag.indexer import DocumentIndexer

__all__ = [
    "BuiltContext",
    "ChunkConfig",
    "DocumentIndexer",
    "Retriever",
    "RetrieverConfig",
    "build_context",
    "chunk_text",
]

_LAZY = {"DocumentIndexer": "app.agent.rag.indexer"}


def __getattr__(name: str):  # noqa: ANN202 - PEP 562 模块级惰性属性
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module), name)
