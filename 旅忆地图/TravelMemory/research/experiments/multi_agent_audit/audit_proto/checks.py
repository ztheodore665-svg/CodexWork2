"""确定性检查：能用代码判的，不花 token。

这一层是整个审计设计的成本基石。真实数据里最高频的三类引用错误
（数字没出处、时间挂错、地点串行）都不需要语义理解，只需要把 claim
里出现的数字/时间与它**引用的那条证据**的原文与元数据比一遍。

把这三类交给 LLM 判，等于花三次调用去算一个字符串包含关系；
而且 LLM 会给出"看起来合理"的错误答案，代码不会。
"""

from __future__ import annotations

import re

from .contracts import Claim, Evidence, Opinion, Verdict

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
#: 时间断言：只有"陈述某事发生过"的句子才需要用元数据校验时间窗
_TEMPORAL_ASSERTION_RE = re.compile(r"去了|去过|到过|住在|待在|那次|当时")


def rule_number(claim: Claim, evidence: dict[str, Evidence]) -> Opinion | None:
    """claim 里出现的数字，必须能在它所引的证据原文里找到。

    为什么这条规则这么硬：模型编数字时几乎不会顺手编一个"证据里刚好没有"的
    上下文——它会把数字塞进一个语义完全正确的句子里。语义相似度对这种情况
    完全失效（"一共走过 12 个城市"和证据里的"城市"高度相似），只有比对数字本身有效。
    """
    cited = [evidence[cid] for cid in claim.cited if cid in evidence]
    if not cited:
        return None
    numbers = _NUMBER_RE.findall(claim.plain_text)
    if not numbers:
        return None
    # 必须按"数字整体"比对，不能用子串：`"6" in "2024-10-06"` 为真，
    # 会让"6 个城市"这种编造数字恰好命中日期里的 6
    corpus_numbers = set(_NUMBER_RE.findall(" ".join(item.text for item in cited)))
    missing = [num for num in numbers if num not in corpus_numbers]
    if not missing:
        return None
    return Opinion(
        role="rule:number",
        claim_id=claim.claim_id,
        verdict=Verdict.UNSUPPORTED,
        span="",
        confidence=0.9,
        reason=f"数字 {missing} 在所引证据原文中不存在",
    )


def rule_time(
    claim: Claim,
    evidence: dict[str, Evidence],
    time_window: tuple[str, str] | None,
) -> Opinion | None:
    """所引证据的拍摄时间是否落在问题的时间窗内。

    这是"旅行记忆"场景独有的高频错误：模型把去年三月的洛阳照片
    挂到"去年秋天"的回答里，文本上完全通顺，只有元数据能发现。
    """
    if not time_window:
        return None
    if not _TEMPORAL_ASSERTION_RE.search(claim.plain_text):
        return None
    cited = [evidence[cid] for cid in claim.cited if cid in evidence]
    dated = [item for item in cited if item.taken_at]
    if not dated:
        return None
    start, end = time_window
    inside = [item for item in dated if start <= item.taken_at[:10] <= end]
    if inside:
        return None
    first = dated[0]
    return Opinion(
        role="rule:time",
        claim_id=claim.claim_id,
        verdict=Verdict.CONTRADICTED,
        # 证伪必须给得出原文片段；"证不实"才允许空片段（不存在的东西没有原文可引）
        span=first.text[:60],
        confidence=0.95,
        reason=f"所引证据拍摄于 {first.taken_at[:10]}，不在 {start}~{end} 内",
    )


def rule_place(claim: Claim, evidence: dict[str, Evidence]) -> Opinion | None:
    """地名一致性：回答说的城市，必须是所引证据拍摄的城市。

    "张冠李戴"是引用错配里最常见的一种：句子通顺、引用编号合法，
    但引的是另一次旅行的照片。文本相似度不一定低（每次旅行都有"住""逛""吃"），
    比对 `place` 元数据却是一眼可见的矛盾。
    """
    cited = [evidence[cid] for cid in claim.cited if cid in evidence]
    places = {item.place for item in cited if item.place}
    if not places:
        return None
    known = {item.place for item in evidence.values() if item.place}
    mentioned = [place for place in known if place and place in claim.plain_text]
    if not mentioned:
        return None
    if any(place in places for place in mentioned):
        return None
    first = next(item for item in cited if item.place)
    return Opinion(
        role="rule:place",
        claim_id=claim.claim_id,
        verdict=Verdict.CONTRADICTED,
        span=first.text[:60],
        confidence=0.9,
        reason=f"回答提到 {'/'.join(mentioned)}，所引证据却是 {first.place}",
    )


def run_rules(
    claim: Claim,
    evidence: dict[str, Evidence],
    time_window: tuple[str, str] | None,
) -> list[Opinion]:
    """跑一遍全部确定性规则。返回空列表 = 规则判不了，需要语义审计。"""
    opinions = [
        rule_number(claim, evidence),
        rule_time(claim, evidence, time_window),
        rule_place(claim, evidence),
    ]
    return [item for item in opinions if item is not None]


#: 功能字。它们组成的二元组几乎出现在任何句子里（"去了""这个""我们"），
#: 计入覆盖率会让"两句都提到吃饭"变成"高度相似"。
_STOP_CHARS = set("你我他她的了在也还和与是都就有着过这那个次上下里中后前时很更最只把被让给对从向往已经能要会可")

#: 数字与日期不参与语义相似度：它们由 `rule_number` / `rule_time` 精确判定，
#: 混进覆盖率只会让"12 个城市"因为"城市"二字而显得像被支持
_DIGIT_RE = re.compile(r"[0-9０-９]+")

#: 框架词：时间表达、话语连接、评价性说法。
#: 时间由 `rule_time` 管，措辞不构成事实主张 —— 把它们计入覆盖率，
#: 会让"你去年秋天去了乌镇"因为"去年秋天"四个字被判成不被支持。
_FRAMING_RE = re.compile(
    r"去年|今年|前年|明年|春天|夏天|秋天|冬天|上个|这一趟|一趟|这趟|那次|当时|当天|那几天|这几天"
    r"|周末|假期|主要|整体|基本|大概|大约|顺路|顺便|然后|后来|其实|一共|总共|左右"
    r"|出名|有名|著名|有名气"
)


def _content_bigrams(text: str) -> set[str]:
    """只保留实词二元组，用于语义相似度的字面近似。"""
    cleaned = _DIGIT_RE.sub(" ", text)
    cleaned = _FRAMING_RE.sub("", cleaned)
    cleaned = re.sub(r"[\s，。！？；：、,.!?;:()（）\"'“”‘’—\-]", "", cleaned)
    grams: set[str] = set()
    for i in range(len(cleaned) - 1):
        gram = cleaned[i : i + 2]
        if gram[0] in _STOP_CHARS or gram[1] in _STOP_CHARS:
            continue
        grams.add(gram)
    return grams or ({cleaned} if cleaned else set())


def _coverage(claim_text: str, evidence_text: str) -> float:
    """claim 的实词二元组有多大比例出现在证据里。

    用二元组而不是分词：中文地名/日期分词器会切错，二元组对短文本更稳，
    且不引入任何依赖。方向是"claim 被证据覆盖"，不是对称相似度 ——
    回答可以比证据长（它有措辞），但不能多出证据里没有的实词。
    """
    claim_bg = _content_bigrams(claim_text)
    if not claim_bg:
        return 0.0
    return len(claim_bg & _content_bigrams(evidence_text)) / len(claim_bg)
