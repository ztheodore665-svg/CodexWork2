"""query_parse 节点：结构化解析（阶段 2 W2-1）。

## 双路解析：LLM 认字，规则算数

| | 负责 | 不负责 |
| --- | --- | --- |
| LLM | 从句子里认出实体（"古镇"是主题、"乌镇"是地名） | 日历换算（经常算错一两年） |
| `rag/time_parser.py` 规则 | 把"去年秋天""前年五一"换算成确定日期 | 认实体 |

合并策略（**确定性优先**）：

1. 规则解析器先跑；命中就用它的结果，`time_source="rule"`
2. 规则没命中，但 LLM 给了合法 ISO 范围 → 用 LLM 的，`time_source="llm"`
3. 都没有 → `time_range=None`，**不加时间过滤条件**

## 关键容错：解析失败一律退化为「不过滤」

`state.parsed.degraded=True` 时，检索阶段只丢弃 LLM 给出的 places / themes / keywords，
**规则算出的时间范围仍然保留** —— 因为它是确定性计算的结果，不属于"猜"。

这与设计文档 §7.3 "解析不确定时退化为不过滤" 是一致的：那条原则针对的是
**不确定的推断**，而规则解析是确定的。把两者混为一谈会导致离线模式（mock LLM）
下时间过滤永远不生效，白白丢掉本阶段最重要的能力。
"""

from __future__ import annotations

from datetime import date

from app.agent.nodes.pipeline.common import extract_json_object, today_text
from app.agent.observability import Trace
from app.agent.prompts.loader import render
from app.agent.rag.time_parser import parse_time_range
from app.agent.state import ParsedQuery, PipelineState
from app.core.llm import BaseLLM, LLMMessage
from app.utils.intent import classify_intent


class QueryParseNode:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        async with trace.step("query_parse", {"query": state.search_query}) as step:
            # ① 规则解析（同步、零成本、可复现）—— 先跑，结果优先级最高
            rule_range = parse_time_range(state.search_query)

            # ② LLM 解析（认实体）
            prompt = render("query_parse", today=today_text(), query=state.search_query)
            result = await self.llm.chat(
                [LLMMessage(role="user", content=prompt)],
                purpose="query_parse",
                json_mode=True,
                temperature=0.0,
            )
            data = extract_json_object(result.content)

            if data is None:
                # 解析不出来就明文降级，不能假装成功
                state.parsed = ParsedQuery(
                    keywords=[],
                    degraded=True,
                    time_range=rule_range.to_dict() if rule_range else None,
                    time_source="rule" if rule_range else "none",
                    time_label=rule_range.label if rule_range else "",
                    # 意图走规则分类，不依赖 LLM：降级时也必须知道这是不是规划题，
                    # 否则 self_check 会拿事实题的口径去杀它（阶段 5.3 真实事故）
                    intent=classify_intent(state.query),
                )
                step.output = {
                    "degraded": True,
                    "reason": "json parse failed",
                    "time_source": state.parsed.time_source,
                    "intent": state.parsed.intent,
                }
                return

            # ③ 合并：时间以规则为准，LLM 只做补充
            time_range = rule_range.to_dict() if rule_range else None
            time_source = "rule" if rule_range else "none"
            if time_range is None:
                llm_range = _valid_range(data.get("time_range"))
                if llm_range is not None:
                    time_range = llm_range
                    time_source = "llm"

            state.parsed = ParsedQuery(
                keywords=_str_list(data.get("keywords")),
                time_range=time_range,
                places=_str_list(data.get("places")),
                themes=_str_list(data.get("themes")),
                degraded=False,
                time_source=time_source,
                time_label=rule_range.label if rule_range else "",
                intent=classify_intent(state.query),
            )
            step.output = {
                "keywords": state.parsed.keywords,
                "places": state.parsed.places,
                "themes": state.parsed.themes,
                "time_range": state.parsed.time_range,
                "time_source": time_source,
                "intent": state.parsed.intent,
                "tokens": result.total_tokens,
            }


def _valid_range(value: object) -> dict[str, str] | None:
    """校验 LLM 给的时间范围：必须是 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}。

    宁可不用也不能用一个格式错误的范围去过滤 —— 那会静默漏召回。
    """
    if not isinstance(value, dict):
        return None
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    if start_date > end_date:
        return None
    return {"start": start_date.isoformat(), "end": end_date.isoformat()}


def _str_list(value: object) -> list[str]:
    """把 LLM 返回的字段规范成字符串列表（它可能给字符串、None、或混合类型）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
