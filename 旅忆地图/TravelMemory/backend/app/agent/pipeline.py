"""阶段 0 的 Agent 管道（纯 Python，无框架）。

链路：rewrite → query_parse → retrieve → build_context → generate → self_check

为什么先不用 LangGraph（设计文档 §3.5）：先把 RAG 与 Memory 的本质跑明白，
再引入状态机；届时 `AGENT_RUNTIME=graph` 切换实现，两者对外接口保持一致，可回滚对比。

self_check 的失败处理：重试一次生成 → 仍不满足引用要求就**拒答**。
这是引用率 100% 这条硬指标的执行者。
"""

from __future__ import annotations

import uuid

from app.agent.events import (
    done_payload,
    evidence_event,
    parsed_event,
    tool_result_event,
    tool_start_event,
)
from app.agent.memory.service import MemoryService
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
from app.agent.rag.ports import LexicalSearchPort, VectorSearchPort
from app.agent.rag.retriever import Retriever
from app.agent.state import PipelineState
from app.core.config import Settings, get_settings
from app.core.embedding import BaseEmbedder, build_embedder
from app.core.feature_flags import is_enabled
from app.core.llm import BaseLLM, build_llm
from app.core.logging import get_logger
from app.schemas.memory import MemorySourceRef

logger = get_logger(__name__)


class AgentPipeline:
    """线性管道。所有协作者都从外部注入，便于替换实现与单测。"""

    def __init__(
        self,
        llm: BaseLLM,
        retriever: Retriever,
        *,
        max_context_chars: int = 4000,
        settings: Settings | None = None,
        memory: MemoryService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm
        self.retriever = retriever
        #: 阶段 4 W4-1：为 None 时完全不注入记忆（行为退回阶段 3）
        self.memory = memory
        self.rewrite = RewriteNode(llm)
        self.query_parse = QueryParseNode(llm)
        self.retrieve = RetrieveNode(retriever)
        #: 阶段 3：工具调用。`TOOLS_ENABLED=false`（默认）时节点直接返回，
        #: 因此它在链路里的存在本身不改变阶段 2 的行为（`make test-degradation` 守这条）
        self.tool_call = ToolCallNode(llm, settings=self.settings)
        self.build_context = BuildContextNode(
            max_chars=max_context_chars,
            compress=self.settings.context_compress_enabled,
        )
        self.generate = GenerateNode(llm)
        self.self_check = SelfCheckNode()

    @classmethod
    def build(
        cls,
        searcher: VectorSearchPort,
        *,
        embedder: BaseEmbedder | None = None,
        llm: BaseLLM | None = None,
        settings: Settings | None = None,
        lexical: LexicalSearchPort | None = None,
        memory: MemoryService | None = None,
    ) -> AgentPipeline:
        """便捷构造：按配置自动选 provider（真实 / mock）。

        `lexical` 不传时会自动尝试把 `searcher` 当词面检索器用 ——
        生产里 DocumentService 同时实现了两个端口，这样调用方不必关心。
        """
        settings = settings or get_settings()
        embedder = embedder or build_embedder(settings)
        llm = llm or build_llm(settings)
        # 构造逻辑集中在 runtime.build_retriever：两种运行时必须拿到**同一个** Retriever，
        # 否则"两种运行时对比"比的就是检索差异，而不是编排差异
        from app.agent.runtime import build_retriever

        retriever = build_retriever(searcher, embedder=embedder, settings=settings, lexical=lexical)
        return cls(
            llm,
            retriever,
            max_context_chars=settings.context_max_chars,
            settings=settings,
            memory=memory,
        )

    # ---------------------------------------------------------------- 记忆（W4-1）
    async def write_memory(
        self,
        user_id: uuid.UUID,
        *,
        layer: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        sources: list[MemorySourceRef] | None = None,
    ) -> object | None:
        """对外暴露的记忆写回入口（供 API / CLI 直接调用）。

        没有配置记忆服务时返回 None 而不是抛错 —— 记忆是加分项，
        不该让"没接记忆库"变成一次 500。
        """
        if self.memory is None:
            return None
        return await self.memory.write_memory(
            user_id,
            layer=layer,
            key=key,
            value=value,
            confidence=confidence,
            sources=sources,
        )

    async def run(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        query: str,
        history: list[dict[str, str]] | None = None,
        trace: Trace | None = None,
    ) -> PipelineState:
        """跑一次问答（非流式）。trace 由调用方创建（决定是否落库），否则只写 JSONL。

        实现上它就是"把流式事件全部丢掉"—— 流式与非流式共用一套编排，
        才能保证两条路径的行为完全一致（否则改了流式忘了改非流式，
        评测跑的是非流式、线上跑的是流式，指标根本对不上）。
        """
        state = PipelineState(
            user_id=user_id,
            session_id=session_id,
            query=query,
            history=history or [],
        )
        async for _event in self.run_stream(
            user_id=user_id,
            session_id=session_id,
            query=query,
            history=history,
            trace=trace,
            state=state,
        ):
            pass
        return state

    async def run_stream(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        query: str,
        history: list[dict[str, str]] | None = None,
        trace: Trace | None = None,
        state: PipelineState | None = None,
    ):
        """流式跑一次问答，产出事件字典（阶段 2 W2-3 供 SSE 使用）。

        事件类型：
          - `parsed`   结构化解析结果（前端可以展示"我理解了什么"）
          - `evidence` 证据清单，带 `[n]` 编号与文档引用
          - `delta`    一段回答文本（**首字可见**就靠它）
          - `restart`  self_check 未通过，前端需丢弃已渲染内容
          - `done`     收尾元信息（延迟 / token / 是否拒答 / 引用）

        `state` 由调用方传入时，调用方能在流结束后拿到完整状态（非流式路径复用）。
        """
        state = state or PipelineState(
            user_id=user_id,
            session_id=session_id,
            query=query,
            history=history or [],
        )
        trace = trace or Trace(session=None, user_id=user_id, session_id=session_id, query=query)
        await trace.start_run()

        try:
            await self.rewrite(state, trace)
            await self.query_parse(state, trace)
            yield parsed_event(state)

            await self.retrieve(state, trace)
            # 记忆必须在 build_context **之前**装载：上下文区块的顺序是在那里定下来的，
            # 之后再补就会出现"偏好被塞到证据后面"，模型会把它当成可引用材料。
            #
            # 阶段 16：这一步与 `graph.py` 的边顺序对齐（retrieve → load_memory →
            # tool_call → build_context）。两套运行时的节点顺序不同，会让 trace 与
            # 工具事件的位置对不上；"两个运行时等价"不能只靠一句注释。
            await self._load_memory(state, trace)
            # 工具证据必须在 build_context 之前追加：上下文编号是在 build_context 里定的，
            # 之后再加就会出现"模型引用了不存在的 [n]"。
            if is_enabled("tools", self.settings):
                yield tool_start_event(state)
            await self.tool_call(state, trace)
            if is_enabled("tools", self.settings):
                yield tool_result_event(state)

            await self.build_context(state, trace)
            yield evidence_event(state)

            if not state.blocks:
                # 无证据：generate 节点会直接拒答，这里照样以 delta 吐出去，
                # 让前端的渲染路径只有一条（不用区分"流式/拒答"两种模式）
                await self.generate(state, trace)
                yield {"type": "delta", "text": state.answer}
            else:
                async for chunk in self.generate.astream(state, trace):
                    yield {"type": "delta", "text": chunk}

                violations = await self.self_check(state, trace)
                # 最多重试 `agent_generate_retries` 次（默认 1）：再给模型一次机会，
                # 仍不行就拒答。次数与 graph 共用同一个 Settings 字段。
                if (
                    violations
                    and state.attempts < self.settings.agent_generate_retries
                    and not state.refused
                ):
                    logger.info("self_check 未通过，重试一次生成：%s", violations[:2])
                    state.attempts += 1
                    yield {"type": "restart", "payload": {"reason": "self_check_failed"}}
                    async for chunk in self.generate.astream(state, trace):
                        yield {"type": "delta", "text": chunk}
                    violations = await self.self_check(state, trace)

                if violations:
                    state.refused = True
                    state.refuse_reason = "self_check_failed"
                    state.answer = SELF_CHECK_FAILED_ANSWER
                    state.citations = []
                    yield {"type": "restart", "payload": {"reason": "self_check_failed"}}
                    yield {"type": "delta", "text": state.answer}

            await self._write_back(state)

            status = "refused" if state.refused else "success"
            state.latency_ms = await trace.finish_run(status=status, token_usage=state.token_usage)
            yield {"type": "done", "payload": done_payload(state)}
        except Exception:
            # 失败也必须收尾，否则 trace 里会永远是 running
            await trace.finish_run(status="failed", token_usage=state.token_usage)
            raise

    # ---------------------------------------------------------------- 记忆（W4-1）
    async def _load_memory(self, state: PipelineState, trace: Trace) -> None:
        """装载记忆到 `state.memory`（`[用户偏好]` / `[当前行程上下文]` 两个区块）。"""
        if self.memory is None or not self.settings.memory_enabled:
            return
        async with trace.step("load_memory", {"user": str(state.user_id)}) as step:
            context = await self.memory.build_context(state.user_id)
            state.memory = context
            step.output = {
                "preferences": len(context.preferences),
                "trip_context": len(context.trip_context),
            }

    async def _write_back(self, state: PipelineState) -> None:
        """回答结束后写回记忆（克制原则：只在抽到明确偏好时写）。

        两条失败降级（PROJECT.md §二、[8]）：记忆服务不可用、用户没说偏好，
        都直接返回，绝不影响本次回答。
        """
        if self.memory is None or not self.settings.memory_enabled:
            return
        try:
            # 用上了的记忆刷新 last_seen_at（衰减重新计时，避免"常用偏好被遗忘"）
            if state.memory is not None:
                await self.memory.touch(state.memory.used_ids)
            record = await self.memory.remember_from_text(state.user_id, state.query)
            if record is not None:
                logger.info("memory.write_back key=%s value=%s", record.key, record.value)
        except Exception:  # noqa: BLE001 - 写回失败不影响本次回答
            logger.warning("记忆写回失败，本次忽略", exc_info=True)
