"""Alembic 迁移脚本模板。

新建迁移：alembic revision -m "说明"
生成的文件头由本模板渲染，请勿手改头部字段。
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# 版本链
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
