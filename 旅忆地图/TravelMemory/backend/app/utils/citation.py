"""引用相关的纯函数。

放在 utils 是为了让三处共用同一套口径：
  1. `nodes/pipeline/self_check.py` —— 生成后硬校验引用是否存在
  2. `agent/eval/metrics.py`       —— 计算 citation_rate
  3. 将来自检节点与 EvalResult 的统计口径

口径必须与 prompt 约定一致：引用写作方括号编号 `[1]`。
"""

from __future__ import annotations

import re

from app.utils.text import split_sentences

#: 答案里的引用标记，例如 [1] / [12]
CITATION_RE = re.compile(r"\[(\d+)\]")

#: 「非事实句」开头白名单：礼貌语、引导语、建议句本身不需要引用。
#: 这是唯一的例外通道，**宁可少放行**：放行过宽会让引用率虚高，硬指标失效。
SOFT_SENTENCE_PREFIXES = (
    "抱歉",
    "对不起",
    "不好意思",
    "我可以",
    "你可以",
    "如果你",
    "建议",
    "试试",
    "换个",
    "暂时",
    "目前",
    "希望",
    "补充说明",
)


def extract_citation_indices(text: str) -> list[int]:
    """取出文本中出现的全部引用编号（按出现顺序，可能重复）。"""
    return [int(x) for x in CITATION_RE.findall(text)]


def is_soft_sentence(sentence: str) -> bool:
    """是否属于不需要引用的句子（礼貌/引导/建议）。"""
    stripped = sentence.strip().lstrip("，,、 ")
    return any(stripped.startswith(p) for p in SOFT_SENTENCE_PREFIXES)


def assertive_sentences(text: str) -> list[str]:
    """需要被引用支撑的句子：排除软句后的全部句子。"""
    return [s for s in split_sentences(text) if not is_soft_sentence(s)]


def citation_rate(answer: str) -> float:
    """事实句中带引用的比例。

    没有任何事实句时返回 1.0（0 除以 0 不该让指标失败），
    拒答场景的正确性由 `refusal` 单独判断。
    """
    targets = assertive_sentences(answer)
    if not targets:
        return 1.0
    cited = [s for s in targets if CITATION_RE.search(s)]
    return round(len(cited) / len(targets), 4)


def out_of_range_indices(answer: str, max_index: int) -> list[int]:
    """找出超出证据范围的引用编号（臆造引用）。"""
    return sorted({i for i in extract_citation_indices(answer) if i < 1 or i > max_index})
