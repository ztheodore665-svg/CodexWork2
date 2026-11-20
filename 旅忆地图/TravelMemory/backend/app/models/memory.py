"""MemoryItem / MemorySource —— 分层记忆的统一存储（设计文档 §4.1）。

设计要点：

- **`weight` 不落库**：衰减是时间的函数，存快照第二天就是错的。库里只存
  `w0`（`weight0`）与 `decay_rate`（λ），实时权重由 `agent/memory/decay.py` 现算。
- **`status` 而不是物理删除**：设计文档 §7.5 要求「weight < 0.05 或已过期 → 归档」。
  归档而不删除，才能在用户某天重新提起旧偏好时把它"复活"（`touch` 会刷新 last_seen_at）。
- **`MemorySource` 是关联表不是数组**：数组列无法建外键、无法反查，
  而引用率 100% 这条红线要求每条记忆都能追到它的来源证据。
- `chain_commitment` 先占位（阶段 7 的零知识承诺），阶段 4 不使用。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base, CreatedAt, PrimaryKey, UpdatedAt

#: 与 Document 保持同一维度（设计文档 §4.3：两者必须一致，否则换模型会留脏向量）
_EMBEDDING_DIM = get_settings().embedding_dim


class MemoryItem(Base):
    """一条记忆（三层共用一张表，用 `type` 区分层）。"""

    __tablename__ = "memory_item"
    __table_args__ = (
        # 同 (user_id, type, key) 只保留一条：这是 upsert 的依据，
        # 也是「新偏好覆盖旧偏好」不产生重复行的保证
        UniqueConstraint("user_id", "type", "key", name="uq_memory_item_user_type_key"),
    )

    id: Mapped[PrimaryKey]

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 记忆层：short_term / episodic / semantic（取值见 app/schemas/memory.py）
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="semantic")
    #: 稳定键。同 (user_id, type, key) 只保留一条 —— 新偏好覆盖旧偏好时走 upsert
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    #: 初始权重 w0
    weight0: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    #: 衰减常数 λ = ln2 / half_life_days。落库而不是每次算，是为了让"改过半衰期的老记忆"
    #: 也能保持它被写入时的口径
    decay_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0231)
    #: 最近一次被"用上"的时间。衰减的 Δt 以它为起点，而不是 created_at ——
    #: 经常用到的偏好不该因为"写得很早"就先归档
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: active / archived（归档 = 不进上下文，但数据仍在）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    #: 阶段 7 的零知识承诺，阶段 4 恒为 None
    chain_commitment: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<MemoryItem {self.type}/{self.key}={self.value[:16]}>"


class MemorySource(Base):
    """记忆的来源（多对一）。"""

    __tablename__ = "memory_source"

    id: Mapped[PrimaryKey]

    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("memory_item.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[CreatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<MemorySource {self.source_type}/{self.source_id}>"
