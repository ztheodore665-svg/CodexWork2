"""`AGENT_RUNTIME` 运行时选择（阶段 4 W4-2）。

设计文档 §7.1 明确要求：阶段 0~3 是纯 Python 管道，阶段 4 起迁 LangGraph，
**两者可切换、可回滚、评测结果可对比**。本模块就是那个开关的唯一实现处。

## 三条硬性要求

1. **旧路径必须一直可用**：默认仍是 `pipeline`，`graph` 是显式选择。
   任何 graph 侧的问题都不应让整条主流程不可用。
2. **失败必须降级而不是崩**（PROJECT.md §二、[8]）：
   langgraph 没装 / 装了但版本不兼容时，记一条醒目警告并回退 `pipeline`。
3. **上层不感知**：两种运行时返回的对象都提供 `run()` 与 `run_stream()`，
   事件序列逐条一致，因此 `api/v1/agent.py` 与评测器都不需要分支。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

from app.agent.memory.service import MemoryService
from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.rag.ports import LexicalSearchPort, VectorSearchPort
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.state import PipelineState
from app.core.config import Settings, get_settings
from app.core.embedding import BaseEmbedder, build_embedder
from app.core.feature_flags import is_enabled
from app.core.llm import BaseLLM, build_llm
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查器需要，避免运行期 import langgraph
    from app.agent.nodes.graph import LangGraphAgent

logger = get_logger(__name__)

#: 两种运行时支持的取值
RUNTIME_PIPELINE = "pipeline"
RUNTIME_GRAPH = "graph"

AgentRunner = Union[AgentPipeline, "LangGraphAgent"]  # noqa: UP007 - 前向引用需要 Union


def resolve_runtime(settings: Settings | None = None) -> str:
    """解析并归一化运行时名；未知取值一律回落 `pipeline`（宁可老路径，不要炸）。"""
    target = settings or get_settings()
    raw = (target.agent_runtime or "").strip().lower()
    if raw == RUNTIME_GRAPH:
        return RUNTIME_GRAPH
    if raw != RUNTIME_PIPELINE:
        logger.warning(
            "未知的 AGENT_RUNTIME=%r，回落 %s（可选值：%s / %s）",
            target.agent_runtime,
            RUNTIME_PIPELINE,
            RUNTIME_PIPELINE,
            RUNTIME_GRAPH,
        )
    return RUNTIME_PIPELINE


def is_graph_runtime(settings: Settings | None = None) -> bool:
    return resolve_runtime(settings) == RUNTIME_GRAPH


def build_retriever(
    searcher: VectorSearchPort,
    *,
    embedder: BaseEmbedder,
    settings: Settings,
    lexical: LexicalSearchPort | None = None,
) -> Retriever:
    """按配置构造 Retriever（两种运行时共用，保证检索侧口径一致）。"""
    from app.agent.rag.reranker import build_reranker

    return Retriever(
        searcher,
        embedder,
        RetrieverConfig(
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
            vector_weight=settings.retrieval_vector_weight,
            lexical_weight=settings.retrieval_lexical_weight,
            lexical_min_coverage=settings.retrieval_lexical_min_coverage,
            filter_min_hits=settings.retrieval_filter_min_hits,
            enable_lexical=settings.retrieval_enable_lexical,
            enable_filter=settings.retrieval_enable_filter,
            enable_hnsw=is_enabled("hnsw", settings),
            # 第二道门：数据量没过万就不该走近似索引（理由见 config.retrieval_use_ann）
            use_ann=settings.retrieval_use_ann,
            hnsw_oversample=settings.retrieval_hnsw_oversample,
            rerank_top_n=settings.rerank_top_n,
            # 阶段 4 W4-3：融合策略（weighted = 阶段 2 行为，rrf = 倒数排名融合）
            fusion=settings.retrieval_fusion,
            rrf_k=settings.retrieval_rrf_k,
        ),
        reranker=build_reranker(settings),
        lexical=lexical
        if lexical is not None
        else (searcher if isinstance(searcher, LexicalSearchPort) else None),
    )


def build_agent(
    searcher: VectorSearchPort,
    *,
    embedder: BaseEmbedder | None = None,
    llm: BaseLLM | None = None,
    settings: Settings | None = None,
    lexical: LexicalSearchPort | None = None,
    memory: MemoryService | None = None,
) -> AgentRunner:
    """按 `AGENT_RUNTIME` 构造运行时。返回对象的对外接口完全一致。"""
    settings = settings or get_settings()
    embedder = embedder or build_embedder(settings)
    llm = llm or build_llm(settings)
    retriever = build_retriever(searcher, embedder=embedder, settings=settings, lexical=lexical)

    if not is_graph_runtime(settings):
        return AgentPipeline(
            llm,
            retriever,
            max_context_chars=settings.context_max_chars,
            settings=settings,
            memory=memory,
        )

    try:
        from app.agent.nodes.graph import LangGraphAgent
    except ImportError as exc:  # pragma: no cover - 仅在依赖缺失时触发
        logger.warning(
            "langgraph 不可用（%s），回退纯 Python 管道：AGENT_RUNTIME=graph 未生效", exc
        )
        return AgentPipeline(
            llm,
            retriever,
            max_context_chars=settings.context_max_chars,
            settings=settings,
            memory=memory,
        )

    return LangGraphAgent(
        llm,
        retriever,
        max_context_chars=settings.context_max_chars,
        settings=settings,
        memory=memory,
    )


__all__ = [
    "RUNTIME_GRAPH",
    "RUNTIME_PIPELINE",
    "build_agent",
    "build_retriever",
    "is_graph_runtime",
    "resolve_runtime",
]

#: 供类型标注使用（`api/v1/agent.py` 需要声明运行时变量的类型）
_ = (PipelineState, Trace)
