"""阶段 4 W4-1：MemoryItem / MemorySource 两张表（设计文档 §4.1）。

## 为什么 `weight` 不落库

衰减是时间的函数。落库就变成"写入那一刻的快照"，第二天读出来还是老值，
归档判定会失真。所以只存 **`weight0`（w0）与 `decay_rate`（λ）**，
实时权重由 `app/utils/decay.py` 的 `current_weight()` 现算。

## 为什么 `status` 而不是物理删除

设计文档 §7.5：`weight < 0.05 或已过期 → 归档（不物理删除）`。
归档而不删除，用户某天重新提起旧偏好时 `touch` 一下就能复活，
而删掉就只能等他再说一遍。

## 唯一约束 `uq_memory_item_user_type_key`

它是 upsert 成立的前提（`ON CONFLICT ... DO UPDATE`），
也是"新偏好覆盖旧偏好"不会产生重复行的保证。

## 索引选择

`ix_memory_item_user_status_seen` 覆盖读取路径的
`WHERE user_id = ? AND status = 'active' ORDER BY last_seen_at DESC`，
即 `MemoryService.build_context()` 每次问答都要走的那条查询。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | None = None
depends_on: str | None = None

#: 与 Document 保持同一维度（设计文档 §4.3：两者必须一致）
EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        "memory_item",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("weight0", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("decay_rate", sa.Float(), server_default="0.0231", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column("chain_commitment", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE", name="fk_memory_item_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_item"),
        sa.UniqueConstraint("user_id", "type", "key", name="uq_memory_item_user_type_key"),
    )
    op.create_index("ix_memory_item_user_id", "memory_item", ["user_id"])
    op.create_index("ix_memory_item_last_seen_at", "memory_item", ["last_seen_at"])
    op.create_index(
        "ix_memory_item_user_status_seen",
        "memory_item",
        ["user_id", "status", "last_seen_at"],
    )

    op.create_table(
        "memory_source",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_id"], ["memory_item.id"], ondelete="CASCADE", name="fk_memory_source_memory_id_memory_item"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_source"),
    )
    op.create_index("ix_memory_source_memory_id", "memory_source", ["memory_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_source_memory_id", table_name="memory_source")
    op.drop_table("memory_source")
    op.drop_index("ix_memory_item_user_status_seen", table_name="memory_item")
    op.drop_index("ix_memory_item_last_seen_at", table_name="memory_item")
    op.drop_index("ix_memory_item_user_id", table_name="memory_item")
    op.drop_table("memory_item")
