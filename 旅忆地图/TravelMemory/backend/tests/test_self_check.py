"""self_check 测试：引用率硬校验的行为必须稳定可预期。"""

from __future__ import annotations

import uuid

from app.agent.nodes.pipeline.self_check import collect_violations
from app.agent.state import EvidenceBlock, ParsedQuery, PipelineState
from app.schemas.document import DocumentRef
from app.utils.intent import INTENT_FACT, INTENT_PLAN, INTENT_RECOMMEND


def _state(answer: str, *, n_blocks: int = 2, refused: bool = False) -> PipelineState:
    state = PipelineState(user_id=uuid.uuid4(), session_id="test", query="测试问题")
    state.answer = answer
    state.refused = refused
    state.blocks = [
        EvidenceBlock(
            index=i + 1,
            ref=DocumentRef(source_type="trip", source_id=f"t{i + 1}"),
            content=f"第 {i + 1} 条证据",
            score=0.9 - i * 0.1,
        )
        for i in range(n_blocks)
    ]
    return state


def test_all_sentences_cited_passes():
    assert collect_violations(_state("你在 2025 年去过乌镇[1]。西塘是第二站[2]。")) == []


def test_uncited_fact_is_violation():
    violations = collect_violations(_state("你在 2025 年去过乌镇[1]。你还去过另外三个地方。"))
    assert len(violations) == 1
    assert "缺少引用" in violations[0]


def test_out_of_range_citation_is_violation():
    violations = collect_violations(_state("你在 2025 年去过乌镇[9]。"))
    assert any("越界" in v for v in violations)


def test_refusal_without_citation_passes():
    state = _state(
        "抱歉，我在你的照片和行程记录里没有找到能支撑这个问题的证据。", refused=True, n_blocks=0
    )
    assert collect_violations(state) == []


def test_refusal_with_citation_fails():
    """拒答却带引用编号是自我矛盾，必须判失败。"""
    state = _state("抱歉，没有找到乌镇的记录[1]。", refused=True, n_blocks=1)
    assert collect_violations(state) != []


def test_empty_answer_is_violation():
    assert collect_violations(_state("")) != []


# ---------------------------------------------------------------- 阶段 5.3：意图豁免
def test_plan_intent_skips_citation_check():
    """阶段 5.3 真实事故的回归：规划题豁免逐句引用。

    事故原句："若需规划类似路线，建议寻找拥有丰富历史遗迹和老城区美食的城市。"
    —— 合理的规划建议没有 [n] 可带，却被按事实题判"缺少引用"，重试后仍拒答。
    """
    state = _state("若需规划类似路线，建议寻找拥有丰富历史遗迹和老城区美食的城市。")
    state.parsed = ParsedQuery(intent=INTENT_PLAN)
    assert collect_violations(state) == []


def test_recommend_intent_skips_citation_check():
    state = _state("推荐去自然风光类的地方。")
    state.parsed = ParsedQuery(intent=INTENT_RECOMMEND)
    assert collect_violations(state) == []


def test_exempt_intent_still_checks_out_of_range():
    """豁免的是"要不要引用"，不是"引用能不能瞎编"。"""
    state = _state("建议去洛阳[9]。")
    state.parsed = ParsedQuery(intent=INTENT_PLAN)
    violations = collect_violations(state)
    assert any("越界" in v for v in violations)


def test_exempt_intent_still_rejects_empty_answer():
    state = _state("")
    state.parsed = ParsedQuery(intent=INTENT_PLAN)
    assert collect_violations(state) != []


def test_fact_intent_still_requires_citations():
    """默认（fact）不受影响 —— 防止豁免被悄悄写宽。"""
    state = _state("你在 2025 年去过乌镇。")
    state.parsed = ParsedQuery(intent=INTENT_FACT)
    violations = collect_violations(state)
    assert any("缺少引用" in v for v in violations)


def test_parsed_none_is_treated_as_fact():
    """没跑解析（parsed=None）时按事实题处理：宁可多杀，不能放水。"""
    state = _state("你在 2025 年去过乌镇。")
    assert state.parsed is None
    violations = collect_violations(state)
    assert any("缺少引用" in v for v in violations)
