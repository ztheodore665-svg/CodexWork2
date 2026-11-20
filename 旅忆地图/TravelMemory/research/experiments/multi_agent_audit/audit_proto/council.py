"""审计委员会：级联 + 异质信息 + 有界预算。

编排顺序（成本从低到高，一旦有结论就停）：

  ① 确定性规则（0 次 LLM）       数字出处 / 时间窗 / 否定极性
  ② Verifier + Falsifier（2 次） 语义蕴含 + 反向找反证
  ③ Judge（1 次，仅冲突时）      只读双方给的原文片段，不读原始证据

三个停止条件缺一不可：
  - 单条 claim 最多走完 ①②③ 一次，不迭代（RECONCILE 的实测是两轮后饱和，
    第三轮起只是在烧钱）；
  - 单次问答最多审计 `max_claims` 条；
  - 墙钟超过 `budget_ms` 立即降级为"未审计"，绝不为了审完而拖垮 P95。
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from .checks import run_rules
from .contracts import AuditResult, Claim, Decision, Evidence, Opinion, Verdict
from .split import rank_by_risk, split_claims

#: 角色权重。反驳者权重高于支持者：在"证据是否支持"这件事上，
#: 假阴性（放过一句错话）的代价远大于假阳性（多标一个存疑）。
_ROLE_WEIGHT = {"auditor:verify": 1.0, "auditor:falsify": 1.3, "auditor:judge": 2.0}


class AuditBackend(Protocol):
    """审计后端。生产实现走 `core/llm.py`，原型用 `lexical.LexicalBackend`。

    三个方法都是**批处理**语义：一次调用处理一组 claim，
    而不是一条 claim 一次调用。6 条 claim × 3 个角色 = 18 次调用，
    在按次计费的现实里不可接受；批量后固定为 2~3 次。
    """

    async def verify(self, question: str, claims: list[Claim], cited: dict[str, list[Evidence]]) -> list[Opinion]:
        """只看到 claim 与它引用的证据，判断是否被支持。"""
        ...

    async def falsify(
        self,
        question: str,
        claims: list[Claim],
        cited: dict[str, list[Evidence]],
        extra: dict[str, list[Evidence]],
    ) -> list[Opinion]:
        """除所引证据外，还能看到未被引用的候选证据，任务是找出反证。

        这是本设计里唯一真正制造"独立性"的地方：LLM 生成时只用到了 top-k 里
        被它挑中的那几条，反驳者看到的是它**没挑**的那几条。
        换 persona 只改措辞，换信息集才改判断。
        """
        ...

    async def judge(self, question: str, claim: Claim, opinions: list[Opinion]) -> Opinion:
        """只在支持与反驳冲突时调用。输入是双方给出的片段，不是原始长文。"""
        ...


async def audit(
    question: str,
    answer: str,
    evidence: list[Evidence],
    *,
    backend: AuditBackend | None,
    time_window: tuple[str, str] | None = None,
    evidence_scores: dict[str, float] | None = None,
    max_claims: int = 6,
    budget_ms: int = 1500,
    call_timeout_ms: int = 700,
) -> AuditResult:
    started = time.monotonic()
    evidence_by_id = {item.evidence_id: item for item in evidence}
    scores = evidence_scores or {}
    claims = split_claims(answer)
    #: 只审带引用的句子。没引用的句子由 self_check 直接判失败，不需要再来一次
    cited_claims = [claim for claim in claims if claim.cited]
    ranked = rank_by_risk(cited_claims, scores)

    result = AuditResult(claims=claims, gated_claim_ids=[c.claim_id for c in ranked])
    undecided: list[Claim] = []

    # ---- ① 确定性规则（对全部带引用的句子跑，成本为 0）------------------
    for claim in ranked:
        opinions = run_rules(claim, evidence_by_id, time_window)
        result.opinions.extend(opinions)
        decided = _decide_by_rules(opinions)
        if decided is not None:
            result.decisions.append(decided)
        else:
            undecided.append(claim)

    #: 只有这部分会花 LLM 调用；排序后截断，超出预算的显式记为未审计
    llm_ids = {claim.claim_id for claim in undecided[:max_claims]}
    undecided = undecided[:max_claims]

    # ---- ② 语义审计（批量两次调用）-------------------------------------
    if undecided and backend is not None:
        remaining = budget_ms - int((time.monotonic() - started) * 1000)
        if remaining <= 0:
            result.degraded = True
            result.degrade_reason = "budget_exhausted"
            undecided = _degrade(undecided, result, "budget_exhausted")
        else:
            cited_map = {c.claim_id: [evidence_by_id[i] for i in c.cited if i in evidence_by_id] for c in undecided}
            extra_map = {
                c.claim_id: [e for e in evidence if e.evidence_id not in c.cited][:4]
                for c in undecided
            }
            verify_op, falsify_op = await _gather_pair(
                backend, question, undecided, cited_map, extra_map, call_timeout_ms, result
            )
            result.opinions.extend(verify_op + falsify_op)
            by_claim = _index_opinions(verify_op + falsify_op)

            conflicts: list[Claim] = []
            for claim in undecided:
                group = _valid_span(by_claim.get(claim.claim_id, []), evidence_by_id)
                verdict = _merge(claim, group)
                if verdict == Verdict.PARTIAL and _is_conflict(group):
                    conflicts.append(claim)
                else:
                    result.decisions.append(
                        Decision(
                            claim_id=claim.claim_id,
                            verdict=verdict,
                            confidence=_avg_conf(group),
                            span=_first_span(group),
                            action="keep" if verdict == Verdict.SUPPORTED else "annotate",
                        )
                    )

            # ---- ③ 冲突才请裁判 ------------------------------------------
            for claim in conflicts:
                remaining = budget_ms - int((time.monotonic() - started) * 1000)
                if remaining <= 0 or backend is None:
                    result.degraded = True
                    result.degrade_reason = "budget_exhausted"
                    _degrade([claim], result, "budget_exhausted")
                    continue
                group = _valid_span(by_claim.get(claim.claim_id, []), evidence_by_id)
                try:
                    verdict_op = await asyncio.wait_for(
                        backend.judge(question, claim, group), timeout=call_timeout_ms / 1000
                    )
                    result.calls += 1
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - 审计失败必须降级而非抛错
                    result.degraded = True
                    result.degrade_reason = "judge_failed"
                    _degrade([claim], result, "judge_failed")
                    continue
                group.append(verdict_op)
                result.opinions.append(verdict_op)
                verdict = _merge(claim, _valid_span(group, evidence_by_id))
                result.decisions.append(
                    Decision(
                        claim_id=claim.claim_id,
                        verdict=verdict,
                        confidence=_avg_conf(group),
                        span=_first_span(_valid_span(group, evidence_by_id)),
                        action="keep" if verdict == Verdict.SUPPORTED else "annotate",
                        note="judge_resolved",
                    )
                )

    # ---- 未进入审计的 claim：显式记录，不假装通过 ----------------------
    audited_ids = {d.claim_id for d in result.decisions}
    for claim in claims:
        if claim.claim_id in audited_ids:
            continue
        if claim.claim_id not in result.gated_claim_ids:
            note = "not_cited"  # 无引用：交给 self_check，不属于审计范围
        elif claim.claim_id not in llm_ids:
            note = "budget_cap"  # 有引用但超出本次 LLM 预算
        else:
            note = "degraded"
        result.decisions.append(
            Decision(
                claim_id=claim.claim_id,
                verdict=Verdict.SUPPORTED,
                confidence=0.0,
                action="keep",
                note=note,
            )
        )

    result.decisions.sort(key=lambda d: _claim_order(d.claim_id))
    return result


async def _gather_pair(
    backend: AuditBackend,
    question: str,
    claims: list[Claim],
    cited: dict[str, list[Evidence]],
    extra: dict[str, list[Evidence]],
    timeout_ms: int,
    result: AuditResult,
) -> tuple[list[Opinion], list[Opinion]]:
    """并发发起两个角色，任一失败按"该角色缺席"处理，不拖垮另一次。"""

    async def _safe(coro, role: str) -> list[Opinion]:
        try:
            out = await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
            result.calls += 1
            return out
        except Exception as exc:  # noqa: BLE001 - 角色失败是预期降级路径
            result.degraded = True
            result.degrade_reason = f"{role}_failed:{type(exc).__name__}"
            return []

    verify_op, falsify_op = await asyncio.gather(
        _safe(backend.verify(question, claims, cited), "verify"),
        _safe(backend.falsify(question, claims, cited, extra), "falsify"),
    )
    return list(verify_op), list(falsify_op)


def _decide_by_rules(opinions: list[Opinion]) -> Decision | None:
    """确定性规则一旦给出坏结论就直接采纳（规则不猜测，只做包含/区间判定）。"""
    bad = [op for op in opinions if op.verdict.is_bad]
    if not bad:
        return None
    worst = max(bad, key=lambda op: op.confidence)
    return Decision(
        claim_id=worst.claim_id,
        verdict=worst.verdict,
        confidence=worst.confidence,
        span=worst.span,
        action="annotate",
        note=worst.role,
    )


def _valid_span(opinions: list[Opinion], evidence: dict[str, Evidence]) -> list[Opinion]:
    """证伪必须有原文片段，且片段必须真的存在于某条证据里。

    这条校验的作用不是防模型偷懒，是防它**编造反证**：一个不存在的片段
    会让裁判采信一个假的否定结论，比不做审计更糟。
    """
    corpus = "\n".join(item.text for item in evidence.values())
    kept: list[Opinion] = []
    for op in opinions:
        if op.verdict == Verdict.CONTRADICTED:
            if not op.span or op.span not in corpus:
                continue
        kept.append(op)
    return kept


def _index_opinions(opinions: list[Opinion]) -> dict[str, list[Opinion]]:
    out: dict[str, list[Opinion]] = {}
    for op in opinions:
        out.setdefault(op.claim_id, []).append(op)
    return out


def _is_conflict(group: list[Opinion]) -> bool:
    verdicts = {op.verdict for op in group}
    return Verdict.SUPPORTED in verdicts and (Verdict.UNSUPPORTED in verdicts or Verdict.PARTIAL in verdicts)


def _merge(claim: Claim, group: list[Opinion]) -> Verdict:
    """置信度加权合并；有冲突时按保守方向取 PARTIAL。

    保守方向的理由：审计的目标是"别让用户读到没根据的话"，
    在两种判断势均力敌时，标"存疑"的代价远小于放过一句错的。
    """
    if not group:
        return Verdict.SUPPORTED
    contradiction = [op for op in group if op.verdict == Verdict.CONTRADICTED]
    if contradiction:
        return Verdict.CONTRADICTED
    scores: dict[Verdict, float] = {}
    for op in group:
        weight = _ROLE_WEIGHT.get(op.role, 1.0) * max(op.confidence, 0.01)
        scores[op.verdict] = scores.get(op.verdict, 0.0) + weight
    if not scores:
        return Verdict.SUPPORTED
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) < 0.15:
        return Verdict.PARTIAL
    return ranked[0][0]


def _avg_conf(group: list[Opinion]) -> float:
    if not group:
        return 0.0
    return round(sum(op.confidence for op in group) / len(group), 3)


def _first_span(group: list[Opinion]) -> str:
    for op in group:
        if op.span:
            return op.span
    return ""


def _degrade(claims: list[Claim], result: AuditResult, reason: str) -> list[Claim]:
    for claim in claims:
        result.decisions.append(
            Decision(claim_id=claim.claim_id, verdict=Verdict.SUPPORTED, confidence=0.0,
                     action="keep", note=f"degraded:{reason}")
        )
    return []


def _claim_order(claim_id: str) -> int:
    try:
        return int(claim_id.lstrip("c"))
    except ValueError:
        return 10**6

