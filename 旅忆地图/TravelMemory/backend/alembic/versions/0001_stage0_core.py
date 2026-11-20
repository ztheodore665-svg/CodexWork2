"""阶段 0 初始 schema

范围严格遵守阶段 0：只建「数据底座 + 检索单元 + 可观测 + 评测」四组表。
Photo / Trip / Place / MemoryItem 等属于阶段 1 及以后，**此处不建**，
避免阶段之间互相污染（PROJECT.md §二、[7]）。

三类扩展：postgis（后续几何计算）/ vector（检索）/ pg_trgm（阶段 2 中文全文）。
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

#: 向量维度从环境读取。改维度 = 换模型，必须同时执行 `make reindex`。
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    # ---- 1. 扩展（W0-1 出口条件：两个扩展创建成功）----
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---- 2. 用户（外键锚点，单用户部署）----
    op.create_table(
        "user",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), nullable=False, server_default="我"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ---- 3. 检索单元 ----
    op.create_table(
        "document",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content", sa.Text, nullable=False),
        # 列名就叫 metadata（PG 里不是保留字），ORM 侧用 metadata_json 映射避免和 SQLAlchemy 保留名冲突
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("char_len", sa.Integer, nullable=False, server_default="0"),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source_type", "source_id", "chunk_index", name="uq_document_source_chunk"
        ),
    )
    # 阶段 0 不做 HNSW：pgvector 是「先遍历图、后过滤」，带 user_id 过滤时可能返回 0 条，
    # 先用精确扫描保证正确性；阶段 3+ 再建索引并配套「结果不足回退精确扫描」。
    op.create_index("ix_document_user_source", "document", ["user_id", "source_type"])

    # ---- 4. 可观测 ----
    op.create_table(
        "agent_run",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token_usage", JSONB, nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "agent_step",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            UUID,
            sa.ForeignKey("agent_run.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "tool_call_log",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("run_id", UUID, nullable=True, index=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ---- 5. 评测 ----
    op.create_table(
        "eval_case",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, nullable=False, index=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="rag"),
        sa.Column("expected_evidence", JSONB, nullable=True),
        sa.Column("expected_answer", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "eval_result",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "eval_case_id",
            UUID,
            sa.ForeignKey("eval_case.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("recall_at_k", sa.Float, nullable=True),
        sa.Column("mrr", sa.Float, nullable=True),
        sa.Column("citation_rate", sa.Float, nullable=True),
        sa.Column("refusal_correct", sa.Float, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """回滚：只删本阶段建的表。

    扩展不删：多个 schema 可能共用，删掉会连累其他本地库。
    """
    op.drop_table("eval_result")
    op.drop_table("eval_case")
    op.drop_table("tool_call_log")
    op.drop_table("agent_step")
    op.drop_table("agent_run")
    op.drop_index("ix_document_user_source", table_name="document")
    op.drop_table("document")
    op.drop_table("user")
