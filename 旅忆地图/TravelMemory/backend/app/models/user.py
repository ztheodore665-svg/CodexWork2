"""用户表。

单用户多设备模型（设计文档 §1.5）：**不做账号体系**，这里只提供一个外键锚点，
让所有数据都能归属到某个人，未来要多用户时不必改表结构。
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, PrimaryKey


class User(Base):
    # 注意：user 在 PostgreSQL 里是保留字，SQLAlchemy 会自动加双引号
    __tablename__ = "user"

    id: Mapped[PrimaryKey]
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="我")
    created_at: Mapped[CreatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<User id={self.id} name={self.name!r}>"
