"""LangGraph 图运行时（阶段 4 W4-2）。

## 关键决策：节点**复用**而不是重写

图里的每个节点都是对 `nodes/pipeline/*` 里同一批节点对象的**适配调用**，
参数在 `AgentState` ↔ `PipelineState` 之间做一次转换。

为什么宁可多写一层转换也不重写：

1. **行为必须逐条一致** —— 出口条件要求两种运行时的评测结果可对比。
   一旦各自实现一份 rewrite / query_parse，任何一处改动都会造成漂移，
   而漂移的表现是"指标悄悄变了"，极难归因。
2. **拒答红线只有一份实现** —— 引用率 100% 的硬校验在 `SelfCheckNode`，
   走同一份代码才能保证 graph 路径不会漏掉这道闸门。

## 条件边：self_check 之后的三岔路

```
self_check ─┬─ 无违规 ──────────────→ write_memory → END
            ├─ 有违规 且 还有重试次数 → generate（再来一次）
            └─ 有违规 且 次数用尽 ───→ refuse → write_memory → END
```

`refuse` 是**显式节点**而不是"在 self_check 里改 state 后直接 END"，
理由：拒答是一次有语义的状态变更（换话术、清空引用），
它应当在 trace 里留下独立的一步，否则排障时只看到"self_check 之后就没了"。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping
from dataclasses import asdict
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.events import done_payload, evidence_event, parsed_event
from app.agent.memory.service import MemoryService
from app.agent.nodes.graph.state import SLIDING_WINDOW_TURNS, AgentState, initial_state
from app.agent.nodes.pipeline import (
    BuildContextNode,
    GenerateNode,
    QueryParseNode,
    RetrieveNode,
    RewriteNode,
    SelfCheckNode,
    ToolCallNode,
)
from app.agent.nodes.pipeline.self_check import SELF_CHECK_FAILED_ANSWER
from app.agent.observability import Trace
from app.agent.rag.retriever import Retriever
from app.agent.state import EvidenceBlock, ParsedQuery, PipelineState
from app.core.config import Settings, get_settings
from app.core.llm import BaseLLM
from app.core.logging import get_logger
from app.schemas.document import DocumentRef, SearchHit
from app.schemas.memory import MemoryContext

logger = get_logger(__name__)

#: self_check 失败后允许的**重试次数**（与纯 Python 管道一致：只再生成一次）
MAX_GENERATE_RETRIES = 1

#: 图里递归上限。够跑完 rewrite→...→generate→self_check→generate→self_check 两轮还有富余
RECURSION_LIMIT = 25

#: 自定义流式事件（`stream_mode="custom"` 里传输）
_RESTART_EVENT = {"event": "restart"}
TraceKey = "trace"


# ---------------------------------------------------------------- 状态转换
def _to_pipeline(state: AgentState) -> PipelineState:
    """AgentState → PipelineState（让图节点能直接调阶段 0~3 的节点对象）。"""
    parsed_raw = state.get("parsed_query")
    ps = PipelineState(
        user_id=state["user_id"],
        session_id=state.get("session_id", ""),
        query=state.get("query", ""),
        history=[dict(m) for m in state.get("messages", [])],
    )
    ps.rewritten_query = state.get("rewritten_query", "")
    ps.parsed = ParsedQuery(**parsed_raw) if parsed_raw else None
    ps.hits = [SearchHit(**hit) for hit in state.get("evidences", [])]
    ps.blocks = [
        EvidenceBlock(
            index=block["index"],
            ref=DocumentRef(**block["ref"]) if isinstance(block["ref"], dict) else block["ref"],
            content=block.get("content", ""),
            score=block.get("score", 0.0),
            meta=dict(block.get("meta") or {}),
        )
        for block in state.get("blocks", [])
    ]
    ps.context_text = state.get("context_text", "")
    ps.answer = state.get("answer", "")
    raw_citations: list[Any] = list(state.get("citations") or [])
    ps.citations = [DocumentRef(**ref) if isinstance(ref, dict) else ref for ref in raw_citations]
    ps.refused = bool(state.get("refused", False))
    ps.refuse_reason = state.get("refuse_reason", "")
    ps.attempts = int(state.get("attempts", 0))
    ps.token_usage = dict(state.get("token_usage") or {})
    if state.get("memory_preferences") or state.get("memory_trip_context"):
        ps.memory = MemoryContext(
            preferences=list(state.get("memory_preferences") or []),
            trip_context=list(state.get("memory_trip_context") or []),
        )
    return ps


def _to_state_update(ps: PipelineState, keys: tuple[str, ...]) -> dict[str, Any]:
    """PipelineState → AgentState 的部分更新（只回写指定字段）。"""
    mapping: dict[str, Any] = {
        "rewritten_query": ps.rewritten_query,
        "parsed_query": asdict(ps.parsed) if ps.parsed else None,
        "evidences": [hit.model_dump() for hit in ps.hits],
        "blocks": [
            {
                "index": b.index,
                "ref": b.ref.model_dump(),
                "content": b.content,
                "score": b.score,
                "meta": dict(b.meta),
            }
            for b in ps.blocks
        ],
        "context_text": ps.context_text,
        "answer": ps.answer,
        "citations": [str(ref) for ref in ps.citations],
        "refused": ps.refused,
        "refuse_reason": ps.refuse_reason,
        "attempts": ps.attempts,
        "token_usage": ps.token_usage,
    }
    return {key: mapping[key] for key in keys}


# ---------------------------------------------------------------- 图运行时
class LangGraphAgent:
    """与 `AgentPipeline` 同签名的图运行时，可整块替换（`AGENT_RUNTIME=graph`）。"""

    def __init__(
        self,
        llm: BaseLLM,
        retriever: Retriever,
        *,
        max_context_chars: int = 4000,
        settings: Settings | None = None,
        memory: MemoryService | None = None,
        max_generate_retries: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm
        self.retriever = retriever
        self.memory = memory
        # 阶段 16：默认值从 Settings 取，与 `AgentPipeline` 共用同一个数字。
        # 传 None 而不是把 `settings` 写进默认参数（默认参数在 import 时求值）。
        self.max_generate_retries = (
            self.settings.agent_generate_retries
            if max_generate_retries is None
            else max_generate_retries
        )
        self.rewrite = RewriteNode(llm)
        self.query_parse = QueryParseNode(llm)
        self.retrieve = RetrieveNode(retriever)
        self.tool_call = ToolCallNode(llm, settings=self.settings)
        self.build_context = BuildContextNode(
            max_chars=max_context_chars, compress=self.settings.context_compress_enabled
        )
        self.generate = GenerateNode(llm)
        self.self_check = SelfCheckNode()
        self._graph: CompiledStateGraph | None = None

    # ------------------------------------------------------------ 图定义
    @property
    def graph(self) -> CompiledStateGraph:
        """惰性编译：图结构只依赖节点对象，编译一次即可复用。"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph

    def _build_graph(self) -> CompiledStateGraph:
        builder = StateGraph(AgentState)

        builder.add_node("rewrite", self._rewrite_node)
        builder.add_node("query_parse", self._query_parse_node)
        builder.add_node("retrieve", self._retrieve_node)
        builder.add_node("load_memory", self._load_memory_node)
        builder.add_node("tool_call", self._tool_call_node)
        builder.add_node("build_context", self._build_context_node)
        builder.add_node("generate", self._generate_node)
        builder.add_node("self_check", self._self_check_node)
        builder.add_node("refuse", self._refuse_node)
        builder.add_node("write_memory", self._write_memory_node)

        builder.add_edge(START, "rewrite")
        builder.add_edge("rewrite", "query_parse")
        builder.add_edge("query_parse", "retrieve")
        builder.add_edge("retrieve", "load_memory")
        builder.add_edge("load_memory", "tool_call")
        builder.add_edge("tool_call", "build_context")
        builder.add_edge("build_context", "generate")
        builder.add_edge("generate", "self_check")

        # ★ 条件边：唯一的分叉点，三选一
        builder.add_conditional_edges(
            "self_check",
            self._route_after_self_check,
            {
                "generate": "generate",  # 违规但还有重试机会
                "refuse": "refuse",  # 违规且次数用尽
                "write_memory": "write_memory",  # 通过
            },
        )
        builder.add_edge("refuse", "write_memory")
        builder.add_edge("write_memory", END)
        return builder.compile()

    def _route_after_self_check(self, state: AgentState) -> str:
        return route_after_self_check(state, self.max_generate_retries)

    # ------------------------------------------------------------ 节点
    def _trace(self, config: RunnableConfig | None) -> Trace:
        trace = (config or {}).get("configurable", {}).get(TraceKey)
        if trace is None:  # pragma: no cover - 仅在不传 trace 的异常路径触发
            return Trace(session=None, user_id=uuid.uuid4(), session_id="", query="")
        return trace

    async def _rewrite_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict:
        ps = _to_pipeline(state)
        await self.rewrite(ps, self._trace(config))
        return _to_state_update(ps, ("rewritten_query",))

    async def _query_parse_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        ps = _to_pipeline(state)
        await self.query_parse(ps, self._trace(config))
        return _to_state_update(ps, ("parsed_query",))

    async def _retrieve_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict:
        ps = _to_pipeline(state)
        await self.retrieve(ps, self._trace(config))
        return _to_state_update(ps, ("evidences",))

    async def _load_memory_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        """W4-1：装载记忆。必须在 build_context 之前 —— 区块顺序在那里定。"""
        trace = self._trace(config)
        if self.memory is None or not self.settings.memory_enabled:
            return {}
        async with trace.step("load_memory", {"user": str(state["user_id"])}) as step:
            context = await self.memory.build_context(state["user_id"])
            step.output = {
                "preferences": len(context.preferences),
                "trip_context": len(context.trip_context),
            }
            return {
                "memory_preferences": context.preferences,
                "memory_trip_context": context.trip_context,
                "memory_used_ids": [str(i) for i in context.used_ids],
            }

    async def _tool_call_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        ps = _to_pipeline(state)
        await self.tool_call(ps, self._trace(config))
        update: dict[str, Any] = {"evidences": _to_state_update(ps, ("evidences",))["evidences"]}
        return update

    async def _build_context_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        ps = _to_pipeline(state)
        if state.get("memory_preferences") or state.get("memory_trip_context"):
            ps.memory = MemoryContext(
                preferences=list(state.get("memory_preferences") or []),
                trip_context=list(state.get("memory_trip_context") or []),
            )
        await self.build_context(ps, self._trace(config))
        return _to_state_update(ps, ("blocks", "context_text"))

    async def _generate_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict:
        ps = _to_pipeline(state)
        trace = self._trace(config)
        writer = _safe_stream_writer()

        if ps.blocks:
            if int(state.get("attempts", 0)) > 0 and writer is not None:
                # 告诉前端丢弃已渲染的内容：下面要重新生成一遍
                writer(_RESTART_EVENT)
            chunks: list[str] = []
            async for chunk in self.generate.astream(ps, trace):
                chunks.append(chunk)
                if writer is not None:
                    writer({"event": "delta", "text": chunk})
            answer = "".join(chunks).strip()
        else:
            await self.generate(ps, trace)
            answer = ps.answer
            if writer is not None:
                writer({"event": "delta", "text": answer})

        return {
            **_to_state_update(
                ps, ("answer", "citations", "refused", "refuse_reason", "token_usage")
            ),
            "answer": answer or ps.answer,
            "restarted": int(state.get("attempts", 0)) > 0,
        }

    async def _self_check_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        ps = _to_pipeline(state)
        violations = await self.self_check(ps, self._trace(config))
        attempts = int(state.get("attempts", 0))
        return {
            "violations": list(violations),
            # 违规即计数：路由靠它判断"还有没有重试机会"
            "attempts": attempts + 1 if violations else attempts,
            "refused": ps.refused,
            "answer": ps.answer,
        }

    async def _refuse_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict:
        ps = _to_pipeline(state)
        async with self._trace(config).step("refuse", {"reason": "self_check_failed"}) as step:
            ps.refused = True
            ps.refuse_reason = "self_check_failed"
            ps.answer = SELF_CHECK_FAILED_ANSWER
            # 拒答还挂着引用是自相矛盾：话说"不知道"却给出处
            ps.citations = []
            step.output = {"refused": True}
            return _to_state_update(ps, ("refused", "refuse_reason", "answer", "citations"))

    async def _write_memory_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict:
        """W4-1 写回：抽到明确偏好才写，失败一律降级（不影响本次回答）。"""
        trace = self._trace(config)
        if self.memory is None or not self.settings.memory_enabled:
            return {}
        try:
            used = [uuid.UUID(raw) for raw in state.get("memory_used_ids", [])]
            await self.memory.touch(used)
            record = await self.memory.remember_from_text(state["user_id"], state.get("query", ""))
            async with trace.step("write_memory", {"user": str(state["user_id"])}) as step:
                step.output = {"written": record.key if record else None}
        except Exception:  # noqa: BLE001 - 写回失败不影响本次回答
            logger.warning("记忆写回失败，本次忽略", exc_info=True)
        return {}

    # ------------------------------------------------------------ 对外
    async def run(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        query: str,
        history: list[dict[str, str]] | None = None,
        trace: Trace | None = None,
    ) -> PipelineState:
        """非流式：把流式事件全部丢掉（与 `AgentPipeline.run` 完全同一套做法）。"""
        sink: dict[str, Any] = {}
        async for _event in self.run_stream(
            user_id=user_id,
            session_id=session_id,
            query=query,
            history=history,
            trace=trace,
            sink=sink,
        ):
            pass
        return _to_pipeline(sink.get("final", {}))

    async def run_stream(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        query: str,
        history: list[dict[str, str]] | None = None,
        trace: Trace | None = None,
        state: AgentState | None = None,
        sink: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """流式跑图，产出与 `AgentPipeline.run_stream` **完全相同**的事件序列。

        三种 stream_mode 各司其职，缺一不可：
          - `updates`：知道**哪个节点**跑完了（用来在正确时机发 parsed / evidence）
          - `custom`：generate 节点逐段推 token（首字可见就靠它）
          - `values`：拿到**完整状态**（更新只是增量，终态只能从这里拿）
        """
        initial = state or initial_state(
            user_id=user_id, session_id=session_id, query=query, history=history
        )
        trace = trace or Trace(session=None, user_id=user_id, session_id=session_id, query=query)
        await trace.start_run()
        config = cast(
            RunnableConfig,
            {"configurable": {TraceKey: trace}, "recursion_limit": RECURSION_LIMIT},
        )

        running: dict[str, Any] = dict(initial)
        try:
            async for chunk in self.graph.astream(
                initial,
                config,
                # 三种 mode 的返回类型各不相同（LangGraph 用重载区分），
                # 这里一次要三种，只能按 `Any` 处理再自行判别
                stream_mode=cast(Any, ["updates", "custom", "values"]),
            ):
                mode, payload = cast(tuple[str, Any], chunk)
                if mode == "values":
                    running = dict(payload)
                    continue
                if mode == "custom":
                    if payload.get("event") == "restart":
                        yield {"type": "restart", "payload": {"reason": "self_check_failed"}}
                    else:
                        yield {"type": "delta", "text": payload.get("text", "")}
                    continue

                # mode == "updates"：payload 形如 {节点名: 该节点的状态更新}
                for node_name, update in payload.items():
                    running.update(update or {})
                    if node_name == "query_parse":
                        yield parsed_event(_to_pipeline(cast(AgentState, running)))
                    elif node_name == "build_context":
                        yield evidence_event(_to_pipeline(cast(AgentState, running)))

            final = _to_pipeline(cast(AgentState, running))
            if sink is not None:
                sink["final"] = running
            final.latency_ms = await trace.finish_run(
                status="refused" if final.refused else "success", token_usage=final.token_usage
            )
            yield {"type": "done", "payload": done_payload(final)}
        except Exception:
            await trace.finish_run(status="failed")
            raise


# ---------------------------------------------------------------- 辅助
def route_after_self_check(
    state: Mapping[str, Any], max_retries: int = MAX_GENERATE_RETRIES
) -> str:
    """self_check 之后的三岔路（**纯函数**，单测直接钉死三条分支）。

    - 无违规 → `write_memory`（正常收尾）
    - 有违规 且 已尝试次数 ≤ 上限 → `generate`（再给模型一次机会）
    - 有违规 且 次数用尽 → `refuse`（拒答红线：宁可不说，不可编）

    `refused=True` 优先：无证据拒答不是"违规"，不该被 refuse 换掉话术，
    也不该浪费一次重试（没有证据，重试一百次也还是没有）。
    """
    if state.get("refused"):
        return "write_memory"
    if not state.get("violations"):
        return "write_memory"
    if int(state.get("attempts", 0)) <= max_retries:
        return "generate"
    return "refuse"


def _safe_stream_writer():
    """取 LangGraph 的自定义流写入器；非流式调用时返回 None（此时不需要推流）。"""
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001 - 非图上下文里没有 writer，属正常情况
        return None


__all__ = [
    "MAX_GENERATE_RETRIES",
    "SLIDING_WINDOW_TURNS",
    "LangGraphAgent",
]
