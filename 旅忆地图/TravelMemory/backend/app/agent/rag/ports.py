"""检索层依赖的端口（抽象）。

用 Protocol 而不是具体类，是为了让 downstream 实现可以替换：
  - 生产：`services/document_service.DocumentService`（pgvector 精确扫描）
  - 测试：`tests/fakes.InMemoryVectorSearch`（不依赖数据库也能跑通管道）

这样单元测试不需要 Docker 就能验证整条 Agent 逻辑，同时不污染生产代码。

阶段 2 把检索拆成**两路**，因此端口也拆成两个：

| 端口 | 回答的问题 |
| --- | --- |
| `VectorSearchPort` | 「语义上像不像」 |
| `LexicalSearchPort` | 「字面上有没有出现」 |

拆开的好处：检索编排（`retriever.py`）可以只用其中一路做单测，
也可以随时给某一路换实现（比如把词面检索换成 Elasticsearch）而不动另一路。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.agent.rag.filters import StructuredFilter
from app.schemas.document import SearchHit


@runtime_checkable
class VectorSearchPort(Protocol):
    """「给我一批语义相似的文档」的能力。"""

    async def similar_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        source_types: Sequence[str] | None = None,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]: ...

    async def ann_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        oversample: int = 4,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]:
        """近似检索（HNSW），阶段 3 W3-4 引入。

        **这个方法的存在本身就是一份文档**：pgvector 的 HNSW 是"先遍历图、后套 WHERE"，
        带 `user_id` + 结构化过滤时可能返回 **0 条**（设计文档第 241 行）。
        把它做成独立方法而不是 `similar_search` 的一个开关，就是为了让
        "走了 ANN 路径"这件事在调用方代码里肉眼可见 —— 隐性切换是最难排查的那类问题。

        返回值可能为空，调用方（`Retriever`）负责在不足时回退精确扫描。
        """
        ...


@runtime_checkable
class LexicalSearchPort(Protocol):
    """「给我一批字面上出现过这些词的文档」的能力。"""

    async def lexical_search(
        self,
        user_id: uuid.UUID,
        terms: Sequence[str],
        *,
        top_k: int = 8,
        flt: StructuredFilter | None = None,
        min_coverage: float = 0.5,
        query_text: str | None = None,
    ) -> list[SearchHit]: ...
