"""ORM 基础设施：声明基类与通用列类型。

统一的不具名约束命名约定很重要：没有它，PostgreSQL 会给主键/唯一约束随机起名，
后续写迁移做 drop constraint 时会非常痛苦。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有模型的声明基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: UUID 主键。默认值交给数据库生成（PostgreSQL 13+ 内置 gen_random_uuid()，无需 pgcrypto）
PrimaryKey = Annotated[
    uuid.UUID,
    mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
]

#: 创建时间
CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False),
]

#: 更新时间（写入时自动刷新）
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
]
