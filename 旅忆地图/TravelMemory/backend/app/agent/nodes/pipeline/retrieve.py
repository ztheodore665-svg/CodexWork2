"""retrieve 节点：调检索器拿证据。

节点本身刻意很薄：真正的策略（查询串怎么拼、怎么过滤、要不要重排）
都在 `agent/rag/retriever.py`，节点只负责把结果放进 state 并写 trace。
"""

from __future__ import annotations

from app.agent.observability import Trace
from app.agent.rag.retriever import Retriever
from app.agent.state import PipelineState


class RetrieveNode:
    def __init__(self, retriever: Retriever) -> None:
        self.retriever = retriever

    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        search_text = Retriever.build_search_text(state)
        async with trace.step("retrieve", {"search_text": search_text}) as step:
            hits = await self.retriever.retrieve(state)
            state.hits = hits
            debug = getattr(state, "retrieve_debug", None)
            step.output = {
                "count": len(hits),
                "top_score": round(hits[0].score, 4) if hits else None,
                "refs": [str(hit.ref) for hit in hits],
                # 过滤条件是"漏召回"最常见的元凶，必须进 trace
                "filter": getattr(debug, "filter", {}),
                "filter_fallback": getattr(debug, "filter_fallback", False),
                "vector_hits": getattr(debug, "vector_hits", len(hits)),
                "lexical_hits": getattr(debug, "lexical_hits", 0),
            }
