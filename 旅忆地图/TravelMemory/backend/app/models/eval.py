"""评测相关表：EvalCase（题目）与 EvalResult（结果）。

`EvalResult` 冗余记录 `prompt_version` + `embedding_model`，是实现归因的前提：
只有把结果与当时的版本绑定，改 prompt 之后才知道到底变好还是变坏。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, PrimaryKey


class EvalCase(Base):
    """评测题目。

    kind: rag（个人记忆检索）/ refusal（应当拒答）/ recommend（群体先验，阶段 7）
    expected_evidence 用 `source_type + source_id + chunk_index` 三元组，
    而不是数据库自增 id —— 重建库后评测集依然有效（设计文档 §4.5）。
    """

    __tablename__ = "eval_case"

    id: Mapped[PrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="rag")
    expected_evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class EvalResult(Base):
    """一次评测结果（每条 case 每次运行一行，便于横向对比）。"""

    __tablename__ = "eval_result"

    id: Mapped[PrimaryKey]
    eval_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    recall_at_k: Mapped[float | None] = mapped_column(Float)
    mrr: Mapped[float | None] = mapped_column(Float)
    #: 事实性句子的引用覆盖率，目标 100%
    citation_rate: Mapped[float | None] = mapped_column(Float)
    #: 应当拒答时是否正确拒答（1.0 / 0.0），不该拒答时置 None
    refusal_correct: Mapped[float | None] = mapped_column(Float)
    #: 阶段 5 W5-1：LLM-as-a-Judge 忠实度（1.0 / 0.0）；未评或拒答为 None
    faithfulness: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    prompt_version: Mapped[str | None] = mapped_column(String(32))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[CreatedAt]
