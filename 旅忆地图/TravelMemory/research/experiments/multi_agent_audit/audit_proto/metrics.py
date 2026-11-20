"""评测口径：claim 级检测质量 + 回答级泄漏率。

指标定义必须写死在这里并且可离线复算，否则"多 Agent 有没有用"
会退化成一句主观判断。三条口径：

  - claim P/R/F1：把"判为坏"当成正类。审计的目标是抓坏句子，
    所以正类是 bad，不是 good。
  - 未标记错误率（unflagged error rate）：含坏句的回答里，
    有多少条回答把坏句**原样**交给了用户。这是产品真正要降的数。
  - 误伤率（false flag rate）：好句子被标成坏的比例。只降前者不控后者，
    等于把回答全标成"存疑"，指标好看但产品废了。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClaimScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return round(self.tp / denom, 3) if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return round(self.tp / denom, 3) if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 3) if p + r else 0.0


def score_claims(pairs: list[tuple[bool, bool]]) -> ClaimScore:
    """pairs: (gold_bad, predicted_bad)。"""
    score = ClaimScore()
    for gold_bad, pred_bad in pairs:
        if gold_bad and pred_bad:
            score.tp += 1
        elif not gold_bad and pred_bad:
            score.fp += 1
        elif gold_bad and not pred_bad:
            score.fn += 1
    return score


def leak_rate(rows: list[dict]) -> float:
    """rows: {"has_bad": bool, "leaked": bool}，只统计含坏句的回答。"""
    dirty = [row for row in rows if row["has_bad"]]
    if not dirty:
        return 0.0
    return round(sum(1 for row in dirty if row["leaked"]) / len(dirty), 3)
