"""原型的"审计员"实现：字面覆盖度启发式。

**它不是产品实现**，只是把 `AuditBackend` 的三个方法填满，让协议、门控、
降级与评测骨架能在没有 API Key 的机器上跑通、跑得快、结果可复现。
生产实现应替换为 `core/llm.py` + `agent/prompts/audit/*.md`，
接口不变，因此 `run_demo.py` 的评测口径可以原样复用。

用字面启发式还有第二个作用：它是**下界**。如果连字面重叠都能抓到的错误
（数字、时间、否定），LLM 审计员抓不到，那说明 prompt 写坏了，不是任务太难。
"""

from __future__ import annotations

import re

from .checks import _coverage
from .contracts import Claim, Evidence, Opinion, Verdict

_ABSOLUTE_RE = re.compile(r"最|唯一|每次|总是|从来|全都|一共|总共")


class LexicalBackend:
    """确定性"审计员"。同样的输入永远得到同样的输出，便于回归测试。"""

    async def verify(
        self,
        question: str,
        claims: list[Claim],
        cited: dict[str, list[Evidence]],
    ) -> list[Opinion]:
        opinions: list[Opinion] = []
        for claim in claims:
            items = cited.get(claim.claim_id, [])
            if not items:
                opinions.append(
                    Opinion(role="auditor:verify", claim_id=claim.claim_id,
                            verdict=Verdict.UNSUPPORTED, confidence=0.8,
                            reason="没有可核对的引用")
                )
                continue
            best = max(items, key=lambda item: _coverage(claim.plain_text, item.text))
            # 多条引用是**合起来**支撑一句话的，按单条取最大值会误伤
            # "去了乌镇、西塘和杭州 [1][2][4]" 这种多跳陈述
            corpus = "；".join(item.text for item in items)
            score = _coverage(claim.plain_text, corpus)
            if score >= 0.55:
                verdict, conf = Verdict.SUPPORTED, min(0.55 + score / 2, 0.95)
            elif score >= 0.33:
                verdict, conf = Verdict.PARTIAL, 0.6
            else:
                verdict, conf = Verdict.UNSUPPORTED, min(0.95, 0.75 + (0.33 - score))
            opinions.append(
                Opinion(
                    role="auditor:verify",
                    claim_id=claim.claim_id,
                    verdict=verdict,
                    span=best.text[:60],
                    confidence=round(conf, 3),
                    reason=f"与所引证据的字面覆盖度 {score:.2f}",
                )
            )
        return opinions

    async def falsify(
        self,
        question: str,
        claims: list[Claim],
        cited: dict[str, list[Evidence]],
        extra: dict[str, list[Evidence]],
    ) -> list[Opinion]:
        opinions: list[Opinion] = []
        for claim in claims:
            items = cited.get(claim.claim_id, [])
            uncited = extra.get(claim.claim_id, [])
            best_cited = max((_coverage(claim.plain_text, item.text) for item in items), default=0.0)
            best_extra = max(
                ((_coverage(claim.plain_text, item.text), item) for item in uncited),
                default=(0.0, None),
                key=lambda pair: pair[0],
            )
            # 反证优先：未被引用的证据比所引证据更贴合这条 claim，
            # 说明生成时引错了来源（最常见的"张冠李戴"）
            if best_extra[1] is not None and best_extra[0] >= 0.55 and best_extra[0] > best_cited + 0.15:
                target = best_extra[1]
                opinions.append(
                    Opinion(
                        role="auditor:falsify",
                        claim_id=claim.claim_id,
                        verdict=Verdict.UNSUPPORTED,
                        span=target.text[:60],
                        confidence=0.7,
                        reason=f"更贴合的来源是证据 {target.evidence_id}，但回答没有引用它",
                    )
                )
                continue
            if best_cited < 0.33:
                opinions.append(
                    Opinion(
                        role="auditor:falsify",
                        claim_id=claim.claim_id,
                        verdict=Verdict.UNSUPPORTED,
                        confidence=0.65,
                        reason="反向检索也没有找到能支撑这句话的原文",
                    )
                )
                continue
            if _ABSOLUTE_RE.search(claim.plain_text) and not any(
                _ABSOLUTE_RE.search(item.text) for item in items
            ):
                opinions.append(
                    Opinion(
                        role="auditor:falsify",
                        claim_id=claim.claim_id,
                        verdict=Verdict.UNSUPPORTED,
                        confidence=0.6,
                        reason="全量/最高级断言在证据中没有对应表述",
                    )
                )
                continue
            opinions.append(
                Opinion(
                    role="auditor:falsify",
                    claim_id=claim.claim_id,
                    verdict=Verdict.SUPPORTED,
                    span=items[0].text[:60] if items else "",
                    confidence=0.4,
                    reason="未找到反证",
                )
            )
        return opinions

    async def judge(self, question: str, claim: Claim, opinions: list[Opinion]) -> Opinion:
        """裁判只读双方片段。原型的裁判规则：反证有片段则采信反证，否则保守判 PARTIAL。"""
        for op in opinions:
            if op.verdict == Verdict.CONTRADICTED:
                return Opinion(
                    role="auditor:judge", claim_id=claim.claim_id, verdict=Verdict.CONTRADICTED,
                    span=op.span, confidence=0.9, reason="反证片段成立",
                )
        return Opinion(
            role="auditor:judge",
            claim_id=claim.claim_id,
            verdict=Verdict.PARTIAL,
            span=next((op.span for op in opinions if op.span), ""),
            confidence=0.5,
            reason="支持与反驳证据强度接近，按保守口径标存疑",
        )
