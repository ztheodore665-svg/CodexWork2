"""查询意图分类测试（阶段 5.3）。

这个文件守两条线：
1. 分类器本身的行为（含"哪趟行程"这类反例）
2. **分类器与评测集标注必须一致** —— 否则运行时豁免与指标豁免错位，
   plan 题又会被 self_check 按事实题误杀，而指标还看不出来
"""

from __future__ import annotations

import uuid

from app.agent.eval.dataset import load_dataset
from app.utils.intent import (
    INTENT_FACT,
    INTENT_PLAN,
    INTENT_RECOMMEND,
    UNSCORED_INTENTS,
    classify_intent,
)


def test_plan_questions_are_classified():
    assert classify_intent("帮我照着上次那趟再规划一个类似的路线。") == INTENT_PLAN
    assert classify_intent("帮我把去年秋天江南古镇那趟安排成一个两天的行程计划。") == INTENT_PLAN


def test_recommend_questions_are_classified():
    assert classify_intent("去完洛阳该去哪？") == INTENT_RECOMMEND
    assert classify_intent("推荐几个适合拍照的地方") == INTENT_RECOMMEND


def test_fact_questions_stay_fact():
    assert classify_intent("我去年秋天去过哪些古镇？") == INTENT_FACT
    assert classify_intent("我在杭州最喜欢哪类地点？") == INTENT_FACT
    assert classify_intent("前年五一我在哪？") == INTENT_FACT


def test_trip_word_alone_is_not_a_plan():
    """ "行程"单独出现不是规划题 —— 防误判关掉 rag 的引用校验。

    "哪趟行程是以看石窟和古城为主的？"问的是**过去哪一趟**，是事实题。
    标记词表若收了"行程"，这条 rag 就会被豁免引用校验，指标被反向污染。
    """
    assert classify_intent("哪趟行程是以看石窟和古城为主的？") == INTENT_FACT


def test_unscored_intents_match_dataset_vocabulary():
    """豁免名单用的就是数据集 kind 的取值，两个世界共用一套词。"""
    assert INTENT_PLAN in UNSCORED_INTENTS
    assert INTENT_RECOMMEND in UNSCORED_INTENTS
    assert INTENT_FACT not in UNSCORED_INTENTS
    assert len(UNSCORED_INTENTS) == 2, "豁免名单恰好两个，多一个都是悄悄放水"


def test_classifier_agrees_with_eval_dataset():
    """评测集标注与分类器判定一致：plan/recommend 必须被认出，rag/refusal 不许误判。

    这是防复发的关键测试：将来加了 plan 题但措辞里没有标记词，
    这里会直接红，而不是等到真实 API 验证时才发现又被 self_check 杀了。
    """
    for item in load_dataset():
        expected = item.kind if item.kind in UNSCORED_INTENTS else INTENT_FACT
        got = classify_intent(item.question)
        assert got == expected, f"{item.question!r} 标为 {item.kind}，分类器却给了 {got}"


async def test_query_parse_node_fills_intent():
    """QueryParseNode 把意图写进 parsed —— 豁免链路的起点。"""
    from app.agent.nodes.pipeline.query_parse import QueryParseNode
    from app.agent.observability import Trace
    from app.agent.state import PipelineState
    from app.core.config import Settings
    from app.core.llm import MockLLM

    node = QueryParseNode(MockLLM(Settings.model_construct()))
    state = PipelineState(user_id=uuid.uuid4(), session_id="s", query="帮我规划一个类似上次的路线")
    trace = Trace(session=None, user_id=state.user_id, session_id="s", query=state.query)
    await node(state, trace)

    assert state.parsed is not None
    assert state.parsed.intent == INTENT_PLAN


async def test_query_parse_node_fills_intent_when_degraded():
    """LLM 解析失败（降级）时意图照填 —— 降级不该把规划题打回事实题口径。"""
    from app.agent.nodes.pipeline.query_parse import QueryParseNode
    from app.agent.observability import Trace
    from app.agent.state import PipelineState
    from app.core.config import Settings
    from app.core.llm import BaseLLM, LLMResult

    class _BrokenLLM(BaseLLM):
        """永远返回非 JSON，逼节点走降级分支。"""

        @property
        def provider(self) -> str:
            return "mock"

        @property
        def model(self) -> str:
            return "broken"

        async def _completion(self, messages, *, purpose, temperature, max_tokens, json_mode):
            return LLMResult(content="这不是 JSON", model="broken", provider="mock")

        async def _astream_completion(self, messages, *, purpose, temperature, max_tokens, usage):
            yield ""  # pragma: no cover - 本测试不走流式

    node = QueryParseNode(_BrokenLLM(Settings.model_construct()))
    state = PipelineState(user_id=uuid.uuid4(), session_id="s", query="去完洛阳该去哪？")
    trace = Trace(session=None, user_id=state.user_id, session_id="s", query=state.query)
    await node(state, trace)

    assert state.parsed is not None
    assert state.parsed.degraded is True
    assert state.parsed.intent == INTENT_RECOMMEND
