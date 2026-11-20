"""阶段 6 W6-2：trip / trip_item 两张表（行程聚类的物化结果）。

## 为什么它们只是「派生表」

`Photo` 是数据源（设计文档 §4），而 `Trip` / `TripItem` 里的每一行都能由
`Photo` 表重算出来（算法见 `services/trip_cluster.py`）。落表是为了让
`GET /api/v1/trips` 走一次带索引的扫描，而不是每次请求都对几千张照片重算聚类。

因此这两张表**可以被安全清空重建**：`services/trip_service.rebuild_user_trips()`
就是这么做的。反过来 `Photo` 绝不能被重建逻辑删除 —— 本迁移也**不动** `photo` 表，
`downgrade()` 同样只删这两张表。

## 索引选择

- `ix_trip_user_started` 覆盖读取路径
  `WHERE user_id = ? ORDER BY started_at ASC`（`GET /api/v1/trips` 每次都走它）
- `ix_trip_item_trip_id` 覆盖"取这趟的全部照片"（按 `trip_id IN (...)` + `ord` 排序）
- `ix_trip_item_photo_id` 覆盖"这张照片属于哪几趟"，也是将来按照片反查行程的入口

## 唯一约束 `uq_trip_item_photo`

同一张照片在同一趟行程里只许出现一次。没有它，重复导入会把同一张照片挂两遍，
行程照片数虚高 —— 而这种脏数据**不会报错**，只在地图上表现为"这趟怎么多了一张"。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "trip",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="cluster", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name="fk_trip_user_id_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trip"),
    )
    op.create_index("ix_trip_user_id", "trip", ["user_id"])
    op.create_index("ix_trip_user_started", "trip", ["user_id", "started_at"])

    op.create_table(
        "trip_item",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        # 列名 `ord`：`order` 是 SQL 保留字，用它当列名要处处加引号
        sa.Column("ord", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trip.id"], ondelete="CASCADE", name="fk_trip_item_trip_id_trip"
        ),
        sa.ForeignKeyConstraint(
            ["photo_id"], ["photo.id"], ondelete="CASCADE", name="fk_trip_item_photo_id_photo"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trip_item"),
        sa.UniqueConstraint("trip_id", "photo_id", name="uq_trip_item_photo"),
    )
    op.create_index("ix_trip_item_trip_id", "trip_item", ["trip_id"])
    op.create_index("ix_trip_item_photo_id", "trip_item", ["photo_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_item_photo_id", table_name="trip_item")
    op.drop_index("ix_trip_item_trip_id", table_name="trip_item")
    op.drop_table("trip_item")
    op.drop_index("ix_trip_user_started", table_name="trip")
    op.drop_index("ix_trip_user_id", table_name="trip")
    op.drop_table("trip")
