"""阶段 16：删除 `photo.thumb_url` 死列。

## 为什么它是死列

`thumb_url` 在 0002 建表时就存在，但全仓没有任何一处写入或读取它：
缩略图的真实载体是 `local_thumb_path`（本地生成的文件路径），
而 `thumb_url` 是「对象存储 URL」的占位 —— MinIO / S3 方案从未落地
（阶段 16 同时删掉了 `minio_*` 三个空配置）。

保留它的代价不是磁盘，是**误导**：读代码的人会以为系统支持"远程缩略图 URL"，
于是可能写出 `if photo.thumb_url:` 这种永远为假的分支。

## 为什么现在才删

阶段 1 建表时按"完整规格一次建好"的思路预留了它，之后阶段 6 的导入链路
选择了 `local_thumb_path` 而不是对象存储。预留列在方案落定后没有回收，
就变成了阶段 16 审计里的"死列"。这条迁移是回收动作，不是 schema 演进。

## downgrade 为什么能加回来

`thumb_url` 是 nullable Text，回滚只会补一列空值，不丢数据；
真正丢掉的只有"从没写进去过"这件事。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("photo", "thumb_url")


def downgrade() -> None:
    op.add_column("photo", sa.Column("thumb_url", sa.Text(), nullable=True))
