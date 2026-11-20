"""修正 Document 唯一键：加入 user_id。

## 为什么要改

原键 `unique(source_type, source_id, chunk_index)` 在**单用户**下没问题。
但 Document 表有 user_id、所有查询都按 user_id 过滤，一旦出现第二个用户
（或者像测试里那样建一个独立用户），两个用户的同一天日文档
`day/2025-10-03#0` 就会撞在同一行上：

- 后写的会 `ON CONFLICT DO UPDATE` 覆盖先写的（user_id 不在更新字段里）
- 结果是 A 用户的文档内容变成 B 用户的，而 B 用户查不到自己的文档

这是"唯一键没覆盖全部隔离维度"的典型后果，所以把 user_id 加进唯一键。
检索侧不受影响：`similar_search` 本来就显式带 user_id 过滤。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_document_source_chunk", "document", type_="unique")
    op.create_unique_constraint(
        "uq_document_user_source_chunk",
        "document",
        ["user_id", "source_type", "source_id", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_user_source_chunk", "document", type_="unique")
    op.create_unique_constraint(
        "uq_document_source_chunk",
        "document",
        ["source_type", "source_id", "chunk_index"],
    )
