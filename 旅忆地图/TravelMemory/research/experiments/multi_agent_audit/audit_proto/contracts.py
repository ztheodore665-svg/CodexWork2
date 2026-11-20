"""引用审计协议的数据契约。

这份文件只放**结构**，不放任何判定逻辑：角色之间的输入输出一旦不稳定，
后面所有指标都不可比。生产接入时这些 dataclass 应替换为 pydantic 模型
（需要 JSON Schema 给 LLM 做 structured output），字段名保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """一条 claim 相对于它引用的证据的结论。

    四档而不是二值，是因为"部分支持"是真实存在且最常见的一档：
    "你去年秋天去了乌镇和西塘"里，乌镇对、西塘错，整句判 0 会误伤对的部分，
    判 1 又放过了错的部分。只有 PARTIAL 能触发"删掉不被支持的分句"这种手术式修复。
    """

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"

    @property
    def is_bad(self) -> bool:
        """是否属于"不能说给用户听"的结论。"""
        return self in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED)


@dataclass(frozen=True)
class Evidence:
    """一条可被引用的证据（对应项目里的 EvidenceBlock）。

    `taken_at` / `place` 是给时间审计员用的：旅行记忆里最高频的错误不是"编造事实"，
    而是"把 A 次旅行的照片挂到 B 次旅行的时间上"。纯文本蕴含判断看不出来，
    必须比对元数据。
    """

    evidence_id: str
    text: str
    taken_at: str = ""
    place: str = ""
    source_id: str = ""


@dataclass
class Claim:
    """一句话 + 它引用的证据编号。"""

    claim_id: str
    text: str
    cited: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()

    @property
    def plain_text(self) -> str:
        """去掉引用编号后的正文，用于语义比对。"""
        import re

        return re.sub(r"\[\d+\]", "", self.text).strip()


@dataclass
class Opinion:
    """一个审计员对一条 claim 的一次判断。

    `span` 是硬约束：任何负面结论都必须给出证据原文片段，
    且该片段必须能在对应证据里逐字找到（`council.py` 会校验）。
    没有 span 的意见直接作废——这是防止审计员自己幻觉的最后一道闸。
    """

    role: str
    claim_id: str
    verdict: Verdict
    span: str = ""
    confidence: float = 0.5
    reason: str = ""


@dataclass
class Decision:
    """裁决结果 + 要执行的动作。"""

    claim_id: str
    verdict: Verdict
    confidence: float
    span: str = ""
    action: str = "keep"  # keep / annotate / drop
    note: str = ""


@dataclass
class AuditResult:
    """一次问答的审计总账，直接写进 trace 与 AuditRecord。"""

    claims: list[Claim] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    opinions: list[Opinion] = field(default_factory=list)
    gated_claim_ids: list[str] = field(default_factory=list)
    calls: int = 0
    degraded: bool = False
    degrade_reason: str = ""

    @property
    def bad_claims(self) -> list[Decision]:
        return [d for d in self.decisions if d.verdict.is_bad]

    @property
    def unflagged_bad_claims(self) -> list[Decision]:
        """判为坏、但动作仍是 keep 的 claim —— 用户会把它当成事实读。"""
        return [d for d in self.bad_claims if d.action == "keep"]
