"""阶段 5 W5-1 评测指标测试：口径收口 + judge 接线 + 评测集契约。

全程 Mock：不连数据库、不烧真实 LLM token。
"""

from __future__ import annotations

import uuid

from app.agent.eval.dataset import EvalCaseItem, load_dataset
from app.agent.eval.metrics import (
    EvalRow,
    aggregate,
    citation_rate_for,
    parse_faithfulness,
    refusal_correct,
)
from app.agent.eval.runner import run_full_eval
from app.agent.nodes.pipeline.self_check import SELF_CHECK_FAILED_ANSWER
from app.agent.state import EvidenceBlock, PipelineState
from app.core.config import Settings
from app.core.llm import LLMResult
from app.schemas.document import DocumentRef, SearchHit


# ---------------------------------------------------------------- refusal_correct
def test_refusal_correct_refusal_kind():
    assert refusal_correct("refusal", refused=True) == 1.0
    assert refusal_correct("refusal", refused=False) == 0.0


def test_refusal_correct_other_kinds():
    """误拒答是错误（0.0）；正常回答不计入（None）而不是送分。"""
    assert refusal_correct("rag", refused=True) == 0.0
    assert refusal_correct("rag", refused=False) is None


def test_refusal_correct_unscored_kinds_always_none():
    """阶段 5.2 真实事故的回归：recommend / plan 不计入拒答率。

    "帮我照着上次那趟再规划一个类似的路线。"曾被标成 rag：规划题引用不出
    未来行程，self_check 一失败就拒答，于是在 refusal_correct 上记 0.0 ——
    用 RAG 的尺子量规划的题。现在这类 kind 恒为 None。
    """
    for kind in ("recommend", "plan"):
        assert refusal_correct(kind, refused=True) is None
        assert refusal_correct(kind, refused=False) is None


# ---------------------------------------------------------------- citation_rate_for
def test_citation_rate_for_refused_is_none():
    assert citation_rate_for("rag", "任何话术", refused=True) is None


def test_citation_rate_for_unscored_kinds():
    """recommend / plan 允许没有逐句引用，即使文本里带了 [1] 也不计。"""
    assert citation_rate_for("recommend", "推荐去自然风光类的地方[1]。", refused=False) is None
    assert citation_rate_for("plan", "第一天乌镇，第二天西塘。", refused=False) is None


def test_citation_rate_for_rag_computes_real_value():
    assert citation_rate_for("rag", "去过乌镇[1]。其他地方也去过。", refused=False) == 0.5


def test_citation_rate_for_self_check_fallback_is_none():
    """阶段 4 已知坑的回归：self_check 兜底话术含非软句，直接算会得 0.0。

    但它是拒答，必须记 None —— 拿安全话术的质量惩罚生成质量是另一种错。
    """
    assert citation_rate_for("rag", SELF_CHECK_FAILED_ANSWER, refused=True) is None


# ---------------------------------------------------------------- parse_faithfulness
def test_parse_faithfulness_accepts_bare_bit():
    assert parse_faithfulness("1") == 1.0
    assert parse_faithfulness("0") == 0.0
    assert parse_faithfulness(" 1\n") == 1.0
    assert parse_faithfulness("0。") == 0.0
    assert parse_faithfulness("1.") == 1.0


def test_parse_faithfulness_rejects_everything_else():
    """只认单独一个 0/1：解释、多个数字、非数字一律 None，绝不猜。"""
    assert parse_faithfulness("") is None
    assert parse_faithfulness("2") is None
    assert parse_faithfulness("答案：1") is None
    assert parse_faithfulness("1 0") is None
    assert parse_faithfulness("true") is None


# ---------------------------------------------------------------- aggregate
def test_aggregate_faithfulness_participates_and_skips_none():
    rows = [
        EvalRow(kind="rag", question="q1", faithfulness=1.0),
        EvalRow(kind="rag", question="q2", faithfulness=0.0),
        EvalRow(kind="rag", question="q3", faithfulness=None),
    ]
    stats = aggregate(rows)
    assert stats["rag.faithfulness"] == 0.5
    assert stats["rag.count"] == 3.0


def test_aggregate_faithfulness_all_none_stays_none():
    stats = aggregate([EvalRow(kind="rag", question="q", faithfulness=None)])
    assert stats["rag.faithfulness"] is None


# ---------------------------------------------------------------- judge 接线（替身）
class _FakeLLM:
    """judge 专用替身：不发任何网络请求，按脚本返回 / 抛错。"""

    def __init__(self, content: str = "1", *, boom: bool = False) -> None:
        self._content = content
        self._boom = boom

    async def chat(self, messages, *, purpose="generate", temperature=None, max_tokens=None):
        if self._boom:
            raise RuntimeError("judge 调用失败（替身）")
        return LLMResult(content=self._content, model="fake-llm", provider="mock")


class _FakePipeline:
    """最小假管道：只提供 run_full_eval / judge_faithfulness 用到的三个成员。"""

    def __init__(self, settings: Settings, llm: _FakeLLM, state: PipelineState) -> None:
        self.settings = settings
        self.llm = llm
        self._state = state

    async def run(self, *, user_id, session_id, query, history=None, trace=None):
        return self._state


def _make_state(*, refused: bool = False) -> PipelineState:
    ref = DocumentRef(source_type="trip", source_id="t9", chunk_index=0)
    return PipelineState(
        user_id=uuid.uuid4(),
        session_id="eval-full",
        query="厦门那趟我清晨拍了什么？",
        hits=[SearchHit(ref=ref, content="清晨的鼓浪屿很安静。", metadata={}, score=0.9)],
        blocks=[EvidenceBlock(index=1, ref=ref, content="清晨的鼓浪屿很安静。", score=0.9)],
        answer="你在鼓浪屿拍了清晨的海滩[1]。",
        refused=refused,
        latency_ms=5,
    )


def _make_item() -> EvalCaseItem:
    return EvalCaseItem(
        kind="rag",
        question="厦门那趟我清晨拍了什么？",
        expected_evidence=[DocumentRef(source_type="trip", source_id="t9", chunk_index=0)],
    )


async def test_full_eval_judge_off_by_default():
    """开关默认关闭：不额外花 judge 的 token，忠实度记 None。"""
    pipeline = _FakePipeline(
        Settings.model_construct(eval_llm_judge=False), _FakeLLM(), _make_state()
    )
    rows = await run_full_eval(pipeline, [_make_item()], user_id=uuid.uuid4())
    assert rows[0].faithfulness is None


async def test_full_eval_judge_on_scores_answer():
    pipeline = _FakePipeline(
        Settings.model_construct(eval_llm_judge=True), _FakeLLM("1"), _make_state()
    )
    rows = await run_full_eval(pipeline, [_make_item()], user_id=uuid.uuid4())
    assert rows[0].faithfulness == 1.0


async def test_full_eval_judge_failure_does_not_crash():
    """judge 抛异常按 None 计并告警，不带崩整场评测。"""
    pipeline = _FakePipeline(
        Settings.model_construct(eval_llm_judge=True), _FakeLLM(boom=True), _make_state()
    )
    rows = await run_full_eval(pipeline, [_make_item()], user_id=uuid.uuid4())
    assert rows[0].faithfulness is None
    # 其余指标不受牵连
    assert rows[0].recall_at_k == 1.0
    assert rows[0].citation_rate == 1.0


async def test_full_eval_refused_answer_not_judged():
    """拒答答案不送 judge：开关开着也不调用（faithfulness 记 None）。"""
    pipeline = _FakePipeline(
        Settings.model_construct(eval_llm_judge=True), _FakeLLM("1"), _make_state(refused=True)
    )
    rows = await run_full_eval(pipeline, [_make_item()], user_id=uuid.uuid4())
    assert rows[0].faithfulness is None
    assert rows[0].refused is True


async def test_full_eval_judge_skips_unscored_kinds():
    """plan/recommend 不送 judge：建议句没有"证据支撑"可言，评了只会得到虚假 0 分。"""
    pipeline = _FakePipeline(
        Settings.model_construct(eval_llm_judge=True), _FakeLLM("1"), _make_state()
    )
    item = _make_item()
    item.kind = "plan"
    rows = await run_full_eval(pipeline, [item], user_id=uuid.uuid4())
    assert rows[0].faithfulness is None
    assert rows[0].citation_rate is None, "plan 的引用率口径同样不计入"


# ---------------------------------------------------------------- 评测集契约
def test_dataset_contract():
    """评测集构成契约：22 rag + 4 refusal + 1 recommend + 2 plan（阶段 5.2 起）。"""
    items = load_dataset()
    kinds = [item.kind for item in items]
    assert kinds.count("rag") >= 10
    assert kinds.count("refusal") >= 3
    assert kinds.count("recommend") + kinds.count("plan") >= 1
    assert set(kinds) <= {"rag", "refusal", "recommend", "plan"}
    for item in items:
        if item.kind in {"recommend", "plan"}:
            assert not item.expected_evidence, f"{item.kind} 用例不应带 expected_evidence"

    # 阶段 5.2：规划/推荐类问题不许再被标成 rag（真实事故的回归）
    planning = [i for i in items if any(w in i.question for w in ("规划", "推荐", "该去哪"))]
    assert planning, "评测集应含规划/推荐类用例"
    for item in planning:
        assert item.kind in {"plan", "recommend"}, (
            f"规划类问题被标成了 {item.kind}: {item.question}"
        )
    by_question = {i.question: i for i in items}
    converted = by_question.get("帮我照着上次那趟再规划一个类似的路线。")
    assert converted is not None and converted.kind == "plan", (
        "该规划题必须保持 plan（阶段 5.2 回归）"
    )
