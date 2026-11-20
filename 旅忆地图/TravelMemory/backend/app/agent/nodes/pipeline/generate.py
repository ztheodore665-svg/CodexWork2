"""generate 节点：带证据生成答案（或明确拒答）。

两条硬约束来自设计文档 §7.4：
  1. 没有证据 → **直接拒答**，连 LLM 都不调用（省一次钱，也避免模型自由发挥）
  2. 有证据 → 让模型在 system 规则下作答，要求每句事实都带 [n]
"""

from __future__ import annotations

from app.agent.nodes.pipeline.common import today_text
from app.agent.observability import Trace
from app.agent.prompts.loader import load, render
from app.agent.state import PipelineState
from app.core.llm import BaseLLM, LLMMessage
from app.schemas.document import DocumentRef
from app.utils.citation import CITATION_RE, extract_citation_indices

#: 无证据时的标准话术：既明确"不知道"，又给一个可执行的替代动作
NO_EVIDENCE_ANSWER = (
    "抱歉，我在你的照片和行程记录里没有找到能支撑这个问题的证据，所以不替你编一个答案。"
    "你可以换个更具体的说法（比如指明城市或年份），或者先把相关行程的照片导入进来。"
)


class GenerateNode:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        async with trace.step(
            "generate",
            {"question": state.query, "evidence_count": len(state.blocks)},
        ) as step:
            # ---- 情况一：没有证据，直接拒答 ----
            if not state.blocks:
                self._refuse(state)
                step.output = {"refused": True, "reason": "no_evidence"}
                return

            # ---- 情况二：有证据，按 system 规则生成 ----
            result = await self.llm.chat(
                self._messages(state),
                purpose="generate",
            )
            self._finalize(state, result.content.strip())
            state.token_usage = {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "cost_cents": result.cost_cents,
            }
            step.output = {
                "answer": state.answer,
                "citations": [str(ref) for ref in state.citations],
                "latency_ms": result.latency_ms,
                "cost_cents": result.cost_cents,
            }

    async def astream(self, state: PipelineState, trace: Trace):
        """流式生成（阶段 2 W2-3）：逐段 yield 文本，结束后回填 state。

        关键约束：**引用校验必须在完整答案上做**。
        流式每来一段就检查一次是错的 —— 答案才写到一半时当然没有引用，
        会误判成"缺少引用"。所以 self_check 只在流结束后跑一次。
        """
        async with trace.step(
            "generate",
            {"question": state.query, "evidence_count": len(state.blocks), "stream": True},
        ) as step:
            if not state.blocks:
                self._refuse(state)
                step.output = {"refused": True, "reason": "no_evidence"}
                yield state.answer
                return

            chunks: list[str] = []
            async for chunk in self.llm.astream_chat(self._messages(state), purpose="generate"):
                chunks.append(chunk)
                yield chunk

            self._finalize(state, "".join(chunks).strip())
            usage = self.llm.last_stream_usage
            if usage is not None:
                state.token_usage = {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "cost_cents": usage.cost_cents,
                }
            step.output = {
                "answer": state.answer,
                "citations": [str(ref) for ref in state.citations],
                "streamed": True,
            }

    # ---------------------------------------------------------------- 内部
    def _messages(self, state: PipelineState) -> list[LLMMessage]:
        """system 规则 + 带编号证据 + 问题。两条路径共用，避免 prompt 走样。"""
        prompt = render(
            "generate",
            context=_ensure_numbered(state),
            question=state.query,
            today=today_text(),
        )
        return [
            LLMMessage(role="system", content=load("system")),
            LLMMessage(role="user", content=prompt),
        ]

    @staticmethod
    def _refuse(state: PipelineState) -> None:
        """无证据：连 LLM 都不调用（省一次钱，也避免模型自由发挥）。"""
        state.refused = True
        state.refuse_reason = "no_evidence"
        state.answer = NO_EVIDENCE_ANSWER
        state.citations = []

    @staticmethod
    def _finalize(state: PipelineState, answer: str) -> None:
        state.answer = answer
        state.refused = False
        state.refuse_reason = ""
        state.citations = _cited_refs(state)


def _ensure_numbered(state: PipelineState) -> str:
    """确保上下文里每条证据都带 [n] 编号。

    引用错位是最难查的一类 bug（模型引用 [3]，但 [3] 其实在上下文里是第 2 条），
    这里做一次兜底编号。
    """
    if state.context_text and CITATION_RE.search(state.context_text):
        return state.context_text
    return "\n\n".join(f"[{b.index}] {b.content}" for b in state.blocks)


def _cited_refs(state: PipelineState) -> list[DocumentRef]:
    """根据答案里出现的引用编号，反查出对应的文档引用。

    这一步让前端能实现「点 [1] → 地图飞到那张照片」（设计文档 §14.1 环节 4）。
    """
    by_index = {b.index: b.ref for b in state.blocks}
    refs: list[DocumentRef] = []
    for idx in dict.fromkeys(extract_citation_indices(state.answer)):
        ref = by_index.get(idx)
        if ref is not None and ref not in refs:
            refs.append(ref)
    return refs
