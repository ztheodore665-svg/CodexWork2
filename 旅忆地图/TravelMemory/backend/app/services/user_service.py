"""用户服务（阶段 0 只需要「取到一个用户」）。

单用户部署，不做账号体系（设计文档 §1.5）。用 `get_or_create_demo_user`
保证 CLI、批处理、评测拿到的是同一个 user_id。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.user import User


async def get_or_create_demo_user(
    session: AsyncSession,
    *,
    name: str | None = None,
    settings: Settings | None = None,
) -> User:
    """按名称取用户，不存在则创建（单用户场景下的幂等入口）。"""
    settings = settings or get_settings()
    target_name = name or settings.demo_user_name

    existing = (
        await session.execute(select(User).where(User.name == target_name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(name=target_name)
    session.add(user)
    await session.flush()  # 拿到 id
    return user


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
