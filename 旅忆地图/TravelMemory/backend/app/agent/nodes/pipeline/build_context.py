"""build_context 节点：给证据编号并拼成 LLM 上下文。

编号从这里诞生，模型的引用就必须指回这里 —— 引用率能否做到 100%，
很大程度上取决于上下文是否清晰、编号是否有歧义。
"""

from __future__ import annotations

from app.agent.observability import Trace
from app.agent.rag.context_builder import PREFERENCE_HEADER, build_context
from app.agent.state import PipelineState


class BuildContextNode:
    def __init__(
        self,
        *,
        max_chars: int = 4000,
        include_history: bool = True,
        compress: bool = True,
    ) -> None:
        self.max_chars = max_chars
        #: 关掉后上下文只有「记忆 + 证据」，用于对照"历史是否影响了回答"
        self.include_history = include_history
        #: 阶段 4 W4-3：压缩总闸。关掉后回落阶段 3 行为 —— 上下文只装证据，
        #: 预算超了就砍证据（`build_context` 里"至少保留一条"的逻辑仍然生效）
        self.compress = compress

    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        async with trace.step("build_context", {"hits": len(state.hits)}) as step:
            built = build_context(
                state.hits,
                max_chars=self.max_chars,
                memory=state.memory if self.compress else None,
                history=state.history if (self.compress and self.include_history) else None,
            )
            state.blocks = built.blocks
            state.context_text = built.text
            step.output = {
                "blocks": len(built.blocks),
                "truncated": built.truncated,
                "dropped": built.dropped,
                "chars": len(built.text),
                "dropped_parts": built.dropped_parts,
                # 记忆真的进了上下文吗？没进的话原因（预算 / 无记忆）就在 dropped_parts 里
                "memory_injected": bool(state.memory)
                and PREFERENCE_HEADER not in built.dropped_parts,
            }
