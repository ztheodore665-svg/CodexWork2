"""rewrite 节点：把用户的自然语言改成更适合检索的问句。

为什么需要它：用户说话是给「人」听的，大量省略与指代——
"照着上次那趟再来一个"这种话直接丢给向量检索，几乎必然召回失败。
"""

from __future__ import annotations

from app.agent.nodes.pipeline.common import (
    clean_llm_text,
    format_history,
    strip_prefix_label,
    today_text,
)
from app.agent.observability import Trace
from app.agent.prompts.loader import render
from app.agent.state import PipelineState
from app.core.llm import BaseLLM, LLMMessage


class RewriteNode:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        async with trace.step("rewrite", {"query": state.query}) as step:
            prompt = render(
                "rewrite",
                today=today_text(),
                history=format_history(state.history),
                query=state.query,
            )
            result = await self.llm.chat(
                [LLMMessage(role="user", content=prompt)], purpose="rewrite"
            )
            rewritten = strip_prefix_label(clean_llm_text(result.content)) or state.query
            state.rewritten_query = rewritten
            step.output = {
                "rewritten_query": rewritten,
                "tokens": result.total_tokens,
                "provider": result.provider,
            }
