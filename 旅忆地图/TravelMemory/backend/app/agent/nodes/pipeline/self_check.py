"""self_check 节点：引用率 100% 的硬校验。

引用率 100% 是核心硬指标（设计文档 §10.1），靠"让模型自觉"是不行的，
必须有一段确定性的代码来判。本节点就是那段代码。

判定口径（与 `utils/citation.py` 完全一致，评测共用）：
  - 每个非软句的事实句必须包含至少一个 `[n]`
  - 引用的编号不能超出本轮证据范围
  - 拒答时不允许出现任何引用编号（拒答还说"根据 [1]"是矛盾）
  - **plan / recommend 意图豁免逐句引用**（阶段 5.3）：规划与推荐不是事实题，
    拿"每句带 [n]"去量它们是错口径。豁免的只是"要不要引用"——
    编号越界（瞎编引用）照旧判失败。

由 pipeline 决定失败后怎么办：重试一次 → 仍失败则拒答。
"""

from __future__ import annotations

from app.agent.observability import Trace
from app.agent.state import PipelineState
from app.utils.citation import (
    CITATION_RE,
    assertive_sentences,
    extract_citation_indices,
    out_of_range_indices,
)
from app.utils.intent import INTENT_FACT, UNSCORED_INTENTS

#: self_check 最终失败时的兜底话术
SELF_CHECK_FAILED_ANSWER = (
    "抱歉，这次生成的回答里有没能落到具体证据的内容。"
    "为了保证不编造，我不把它说给你听。你可以把问题说得更具体一些，"
    "或者明确城市与时间范围，我再查一次。"
)


class SelfCheckNode:
    """注意：本节点返回值是违规描述列表（空列表 = 通过）。"""

    async def __call__(self, state: PipelineState, trace: Trace) -> list[str]:
        async with trace.step(
            "self_check",
            {"answer_len": len(state.answer), "refused": state.refused},
        ) as step:
            violations = collect_violations(state)
            step.output = {
                "violations": violations,
                "passed": not violations,
            }
            return violations


def collect_violations(state: PipelineState) -> list[str]:
    """纯函数形式的校验规则，便于单测与离线复算。"""
    max_index = len(state.blocks)

    # 拒答分支：不许带引用
    if state.refused:
        if extract_citation_indices(state.answer):
            return ["拒答答案里出现了引用编号"]
        return []

    if not state.answer.strip():
        return ["答案为空"]

    violations: list[str] = []

    bad = out_of_range_indices(state.answer, max_index)
    if bad:
        violations.append(f"引用编号越界 {bad}（本轮证据共 {max_index} 条）")

    # 阶段 5.3：plan / recommend 豁免"每句带引用"。
    # 真实事故：规划题给出"建议寻找拥有丰富历史遗迹和老城区美食的城市"这类
    # 合理建议，没有 [n] 可带，被按事实题口径判"缺少引用"→ 重试 → 拒答。
    # 编号越界在上面已经查过：豁免的是"要不要引用"，不是"引用能不能瞎编"。
    intent = state.parsed.intent if state.parsed is not None else INTENT_FACT
    if intent in UNSCORED_INTENTS:
        return violations

    for sentence in assertive_sentences(state.answer):
        if not CITATION_RE.search(sentence):
            violations.append(f"缺少引用：{sentence}")

    return violations
