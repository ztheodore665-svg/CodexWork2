"""回答 → claim 拆分 + 风险门控。

两件事都在这里，因为它们共享同一个输入（回答文本）且都是纯函数：
纯函数才好单测，也才好离线复算历史 trace —— 审计逻辑一旦依赖运行环境，
"上周这条回答当时为什么没被标记"就永远查不清。

门控是成本开关：不是每句话都值得三个审计员看一遍。
高分才送审，低分直接放过并显式记录"未审计"，而不是假装审计过了。
"""

from __future__ import annotations

import re

from .contracts import Claim

#: 引用编号。与项目 `app/utils/citation.py` 的口径保持一致：`[1]` / `[2]`
CITATION_RE = re.compile(r"\[(\d{1,3})\]")

#: 句末终止符。不切分号，因为中文回答里分号常连接同一论点的两个分句
_TERMINATORS = "。！？!?"

#: 行首列表符号（`- ` / `* ` / `1. ` / `1、`）。不用宽松的 `^\s*\d+`
#: 是因为那会把"12 个城市"开头的数字吃掉
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.、)])\s+")

#: 最高级 / 全量断言。这类词最容易把"三次"说成"总是"
_ABSOLUTE_RE = re.compile(r"最|唯一|第一|一共|总共|全都|总是|每次|从未|从来")

#: 停留类断言（住宿、天数）。旅行记忆里"住了几晚"是最高频的具体事实，
#: 也最容易把"当天往返"写成"住了两晚"
_STAY_RE = re.compile(r"住|待了|停留|晚|天|往返")

#: 否定。否定错位（"没去成"被写成"去了"）是二值判断，语义相似度看不出来
_NEGATION_RE = re.compile(r"没|不|未|无|别")

#: 数字 / 日期 / 时长
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

#: 时间断言：claim 在陈述"某件事发生过"，才需要用元数据校验时间窗
_TEMPORAL_ASSERTION_RE = re.compile(r"去了|去过|到过|在.{0,6}(住|待|玩)|那次|当时")


def split_claims(answer: str) -> list[Claim]:
    """把回答拆成 claim 列表。

    刻意按句切而不是按"事实单元"切：句是用户在 UI 上看到的最小可标记单位，
    也是 `self_check` 已经在用的单位（`assertive_sentences`）。
    换成更细的单元，前端就无法把判定结果标回具体的句子。
    """
    claims: list[Claim] = []
    for sentence in _split_sentences(answer):
        text = sentence.strip()
        if not text:
            continue
        cited = tuple(_uniq(CITATION_RE.findall(text)))
        claims.append(
            Claim(
                claim_id=f"c{len(claims) + 1}",
                text=text,
                cited=cited,
            )
        )
    return claims


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw_line in text.splitlines():
        line = _BULLET_RE.sub("", raw_line).strip()
        if not line:
            continue
        buf = ""
        for ch in line:
            buf += ch
            if ch in _TERMINATORS:
                out.append(buf)
                buf = ""
        if buf.strip():
            out.append(buf)
    return out


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def risk_tags(claim: Claim, evidence_scores: dict[str, float], min_score: float) -> list[str]:
    """给一条 claim 打风险标签。标签只用于排序与事后归因，不参与最终判定。"""
    tags: list[str] = []
    plain = claim.plain_text
    if _NUMBER_RE.search(plain):
        tags.append("number")
    if _ABSOLUTE_RE.search(plain):
        tags.append("absolute")
    if _STAY_RE.search(plain):
        tags.append("stay")
    if _NEGATION_RE.search(plain):
        tags.append("negation")
    if len(claim.cited) >= 2:
        tags.append("multi_citation")
    if len(plain) >= 60:
        tags.append("long")
    if claim.cited:
        lowest = min((evidence_scores.get(cid, 1.0) for cid in claim.cited), default=1.0)
        if lowest < min_score:
            tags.append("weak_retrieval")
    else:
        tags.append("no_citation")
    return tags


#: 标签权重 = 这条 claim 判错之后对用户的伤害量级，不是"难度"
_TAG_WEIGHT: dict[str, int] = {
    "number": 2,
    "absolute": 2,
    "negation": 2,
    "stay": 2,
    "multi_citation": 1,
    "weak_retrieval": 2,
    "long": 1,
    "no_citation": 0,  # 缺引用由 self_check 兜，不占审计预算
}


def rank_by_risk(claims: list[Claim], evidence_scores: dict[str, float], *,
                 min_score: float = 0.3) -> list[Claim]:
    """按风险权重降序排列，并把标签写回 claim 供事后归因。

    门控分两层，别混在一起：
      - **进规则层**：所有带引用的 claim 都进。规则是纯字符串比较，成本为 0，
        没有理由"省"它；省掉反而会把时间挂错、数字编造这类硬错误漏掉。
      - **进 LLM 层**：只进规则判不了、且条数受 `max_claims` 限制的那些。
    排序而不是截断：预算打满时优先审权重高的，否则长回答里靠后的数字型断言
    会被系统性漏掉 —— 而那正是错误率最高的一类。
    """
    scored: list[tuple[int, int, Claim]] = []
    for idx, claim in enumerate(claims):
        tags = risk_tags(claim, evidence_scores, min_score)
        claim.risk_tags = tuple(tags)
        weight = sum(_TAG_WEIGHT.get(tag, 0) for tag in tags)
        scored.append((weight, idx, claim))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [claim for _weight, _idx, claim in scored]
