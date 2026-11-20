"""评测指标。

阶段 0 只测检索命中与拒答；阶段 5（W5-1）已补齐生成侧指标：
忠实度（LLM-as-a-Judge）与归因对比，口径全部收口在本文件的纯函数里。

指标口径必须与 self_check 节点保持一致，否则会出现「节点判失败但评测判通过」的错位：
  - Recall@k = 前 k 条召回里命中的期望文档数 / 期望文档总数
  - MRR      = 1 / 第一条命中期望文档的排名（没命中为 0）
  - citation_rate = 事实句中带引用的比例（来自 utils/citation，与 self_check 同口径）

口径函数分成"计分入口"（refusal_correct / citation_rate_for）与"裸计算"两层：
入口层负责决定**哪些场景根本不该参与某个指标**（None = 不计入），
裸计算层只回答"参与了的话得几分"。混在一起写，就会出现
「拒答话术因为不含引用而被判 0 分」这种拿安全话术的质量惩罚生成质量的错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.citation import citation_rate as _citation_rate

#: 数据集 kind 与运行时 intent 共用同一份豁免名单（plan / recommend）——
#: 指标层与 self_check 必须认同一套词，否则会出现"指标说不计引用率、
#: self_check 却按事实题校验"的错位（阶段 5.3 真实事故）。
from app.utils.intent import UNSCORED_INTENTS

#: 拒答识别：命中这些标记即认为模型明确拒答了
REFUSAL_MARKERS = (
    "没有找到能支撑",
    "没能落到具体证据",
    "没有找到相关",
    "无法回答这个问题",
)


@dataclass
class EvalRow:
    """一条用例的一次运行结果。"""

    kind: str
    question: str
    recall_at_k: float | None = None
    mrr: float | None = None
    citation_rate: float | None = None
    refusal_correct: float | None = None
    latency_ms: int = 0
    answer: str | None = None
    refused: bool = False
    case_id: str | None = None
    #: 阶段 5 W5-1：LLM-as-a-Judge 忠实度（1.0 / 0.0），未评或拒答为 None
    faithfulness: float | None = None
    #: 拒答原因（no_evidence / self_check_failed），仅报告用不入库
    refuse_reason: str = ""


def recall_at_k(retrieved: list[tuple], expected: list[tuple], k: int = 5) -> float | None:
    """召回率@k。expected 为空时返回 None（说明这条不该算检索召回）。"""
    if not expected:
        return None
    top = retrieved[:k]
    hits = sum(1 for key in expected if key in top)
    return round(hits / len(expected), 4)


def mrr(retrieved: list[tuple], expected: list[tuple]) -> float | None:
    """倒排排名：只看第一条命中的位置。"""
    if not expected:
        return None
    expected_set = set(expected)
    for rank, key in enumerate(retrieved, start=1):
        if key in expected_set:
            return round(1.0 / rank, 4)
    return 0.0


def is_refusal(answer: str) -> bool:
    """答案是否属于明确拒答。"""
    return any(marker in answer for marker in REFUSAL_MARKERS)


def citation_rate(answer: str) -> float:
    """引用覆盖率（转发到统一实现，保证与 self_check 口径一致）。"""
    return _citation_rate(answer)


def refusal_correct(kind: str, refused: bool) -> float | None:
    """拒答是否正确（计入指标的形式）。

    - refusal 用例：拒了 = 1.0，没拒 = 0.0（漏拒答是错误）
    - recommend / plan：**恒为 None** —— 只要求"生成合理"，
      该不该拒答不由这个指标度量
    - 其余（rag）：拒了 = 0.0（误拒答是错误），没拒 = None（不计入）

    为什么 recommend / plan 要整个豁免（阶段 5.2 真实事故）：
    "帮我照着上次那趟再规划一个类似的路线。"曾被标成 rag，而规划题
    不可能从库里"引用"出一个未来行程，self_check 一失败就拒答，
    于是在 refusal_correct 上记 0.0 —— **用 RAG 的尺子量规划的题**。
    指标被污染，真正的问题（题目标错了）反而被掩盖。

    为什么"没拒答的 rag"是 None 而不是 1.0：本指标只度量
    「系统拒答时拒得对不对」；正常回答的质量由 citation_rate 负责。
    两边都记 1.0 会让"什么都没发生"也变成加分项，指标失去分辨力。
    """
    if kind == "refusal":
        return 1.0 if refused else 0.0
    if kind in UNSCORED_INTENTS:
        return None
    return 0.0 if refused else None


def citation_rate_for(kind: str, answer: str, *, refused: bool) -> float | None:
    """计入引用率的取值。

    - 拒答（含 self_check 失败兜底）→ None：拒答话术不是事实性回答，
      对它算引用率是另一种错——拿安全话术的质量惩罚生成质量。
      self_check 失败的案例由 refusal_correct = 0.0 恰好记录一次，不双标。
    - recommend / plan → None：群体先验与规划类不计引用率
    - 其余 → citation_rate(answer)
    """
    if refused or kind in UNSCORED_INTENTS:
        return None
    return citation_rate(answer)


def parse_faithfulness(raw: str) -> float | None:
    """解析 LLM-as-a-Judge 的 0/1 输出，只认「单独一个 0 或 1，可带句读尾巴」。

    其余任何内容（解释、多个数字、拒答）一律 None 而不是猜一个值——
    猜出来的忠实度比没有更危险：它会看起来像个指标。
    """
    text = raw.strip()
    if not text:
        return None
    m = re.fullmatch(r"([01])[。.，,！!；;]?", text)
    return float(m.group(1)) if m else None


def _mean(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def aggregate(rows: list[EvalRow]) -> dict[str, float | None]:
    """汇总统计。按 kind 分开算，避免群体先验污染个人记忆的口径。"""
    result: dict[str, float | None] = {}
    for kind in {row.kind for row in rows}:
        kind_rows = [row for row in rows if row.kind == kind]
        prefix = f"{kind}."
        result[prefix + "count"] = float(len(kind_rows))
        result[prefix + "recall_at_k"] = _mean([row.recall_at_k for row in kind_rows])
        result[prefix + "mrr"] = _mean([row.mrr for row in kind_rows])
        result[prefix + "citation_rate"] = _mean([row.citation_rate for row in kind_rows])
        result[prefix + "refusal_correct"] = _mean([row.refusal_correct for row in kind_rows])
        result[prefix + "faithfulness"] = _mean([row.faithfulness for row in kind_rows])
        result[prefix + "latency_ms"] = _mean([float(row.latency_ms) for row in kind_rows])
    return result
