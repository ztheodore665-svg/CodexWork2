"""阶段 1 迁移：Photo + IngestJob

范围：只建阶段 1 需要的两张表（数据源 + 批处理幂等键）。
Trip / TripItem / Place 属于阶段 6 的规则聚类产物，**此处不建**。
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str = "0001"
branch_labels: str | None = None
depends_on: str | None = None

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "photo",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("platform", sa.String(32), nullable=False, server_default="demo"),
        sa.Column("asset_id", sa.String(255), nullable=False),
        sa.Column("hash", sa.String(64), nullable=True, index=True),
        sa.Column("local_thumb_path", sa.Text, nullable=True),
        sa.Column("thumb_url", sa.Text, nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        # 存储侧恒为 WGS84（PROJECT.md §二、[2]）
        sa.Column("coord_system", sa.String(8), nullable=False, server_default="WGS84"),
        sa.Column("location_source", sa.String(16), nullable=True),
        sa.Column("location_confidence", sa.Float, nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("taken_at_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("tz_name", sa.String(64), nullable=True),
        sa.Column("tz_source", sa.String(16), nullable=True),
        sa.Column("exif_json", JSONB, nullable=True),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("ai_tags", JSONB, nullable=True),
        sa.Column("ai_status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("ai_model", sa.String(64), nullable=True),
        sa.Column("ai_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # 去重键：同一平台同一 asset 只保留一条
        sa.UniqueConstraint("platform", "asset_id", name="uq_photo_platform_asset"),
    )
    op.create_index("ix_photo_user_taken", "photo", ["user_id", "taken_at_local"])

    op.create_table(
        "ingest_job",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "photo_id",
            UUID,
            sa.ForeignKey("photo.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("ai_model", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("cost_cents", sa.Float, nullable=False, server_default="0"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # ★ 幂等键：同一照片 + 同一任务 + 同一模型版本 只应该有一行
        sa.UniqueConstraint("photo_id", "job_type", "ai_model", name="uq_ingest_job_identity"),
    )


def downgrade() -> None:
    op.drop_table("ingest_job")
    op.drop_index("ix_photo_user_taken", table_name="photo")
    op.drop_table("photo")
