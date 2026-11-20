"""查询意图分类（阶段 5.3）。

## 为什么放在 utils

`SelfCheckNode`（nodes 层）与 `eval/metrics.py`（agent 层）都要用同一份
「哪些意图不计引用率」的名单，而分层红线是
`agent → nodes → tools → services → models` 单向依赖 ——
nodes 不许 import agent.eval，共用的契约只能落在 utils。

## 为什么用规则而不是让 LLM 分类

意图分类必须是**确定性判定**：评测集里标好的 kind 要与运行时判定一致，
否则「指标说这条不计引用率、self_check 却按事实题校验」的错位会反复出现
（阶段 5.2 改指标、5.3 改 self_check，连着踩了两次）。
规则分类器可单测、可复现；LLM 分类每次都可能给出不同答案，
而"这次算了下次不算"的豁免比没有豁免更危险。

## 已知边界

关键词命中是启发式，漏判（plan 题没有标记词）的后果是该题被按事实题校验、
可能被 self_check 误杀 —— 这个失败模式由
`tests/test_intent.py::test_classifier_agrees_with_eval_dataset` 兜底：
新增评测题如果标了 plan/recommend 却没有标记词，测试会直接红。
"""

from __future__ import annotations

#: 事实问答（默认）：每句事实必须带引用
INTENT_FACT = "fact"
#: 行程规划：允许没有逐句引用
INTENT_PLAN = "plan"
#: 推荐：允许没有逐句引用
INTENT_RECOMMEND = "recommend"

#: 豁免引用率校验的意图。self_check（运行时）与 eval 指标（统计）共用同一份名单
#: —— 两层认的不是同一套词，就会出现"指标不计、self_check 照杀"的错位。
UNSCORED_INTENTS = frozenset({INTENT_PLAN, INTENT_RECOMMEND})

#: 规划类标记词。**刻意不含"行程"**：
#: "哪趟行程是以看石窟和古城为主的？"是事实题（问过去哪一趟），
#: 含"行程"会把它误判成规划题，反而关掉一条 rag 的引用校验。
_PLAN_MARKERS = ("规划", "计划", "安排", "路线")
_RECOMMEND_MARKERS = ("推荐", "该去哪", "去哪玩", "值得去")


def classify_intent(question: str) -> str:
    """按关键词判定查询意图（确定性，供 QueryParseNode 填写 `ParsedQuery.intent`）。"""
    if any(marker in question for marker in _PLAN_MARKERS):
        return INTENT_PLAN
    if any(marker in question for marker in _RECOMMEND_MARKERS):
        return INTENT_RECOMMEND
    return INTENT_FACT
