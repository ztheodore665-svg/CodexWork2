"""Document —— **检索单元**。

设计要点（设计文档 §4）：
  - Photo / Trip 是「数据源」，Document 是为检索而生的派生 unit，两者不要混用；
  - `(source_type, source_id, chunk_index)` 三元组唯一 —— 这既是幂等重写的依据，
    也是评测集里稳定标识的要求（自增 id 在重建库之后会变，不能用）；
  - `embedding_model` 落库，才能在启动时校验「库内主流值 == 当前配置」，
    防止换模型后新老向量混在一个索引里（脏向量）。
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base, CreatedAt, PrimaryKey, UpdatedAt

# 维度从配置读，不写死；但注意这里是**导入时**求值，改维要配合新迁移 + make reindex
_EMBEDDING_DIM = get_settings().embedding_dim


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        # 唯一键必须带上 user_id：否则两个用户同一天的日文档 day/2025-10-03#0
        # 会撞在同一行上互相覆盖（见迁移 0003 的说明）
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_id",
            "chunk_index",
            name="uq_document_user_source_chunk",
        ),
    )

    id: Mapped[PrimaryKey]

    #: 归属用户。单用户部署下始终同一个值，但过滤条件必须显式带上（为多用户留口）
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 数据源类型：trip / place / photo / note（阶段 0 的演示语料用 trip 与 place）
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 数据源稳定标识（如 trip 的 t1、place 的 p12）
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 同一数据源切分后的序号，从 0 开始
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 结构化过滤字段（时间窗/地点/主题等）放在这里，
    #: 阶段 2（W2-2）的时空过滤会直接读它，届时不必改表
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_len: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<Document {self.source_type}/{self.source_id}#{self.chunk_index}>"
