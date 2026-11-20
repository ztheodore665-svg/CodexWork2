"""数据库连接与会话。

统一约定：
  - 全项目只用 async Session（asyncpg）
  - 业务代码不自己建 engine，一律从这里拿会话
  - `expire_on_commit=False`：提交后对象仍可读取，避免 AsyncIO 里常见的
    MissingGreenlet / DetachedInstanceError
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_settings: Settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,  # 断线自动重连，避免容器重启后第一条请求必失败
    pool_size=5,
    max_overflow=5,
)

SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """脚本 / CLI 用的事务上下文：出错自动回滚，正常自动提交。"""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的会话。"""
    async with SessionFactory() as session:
        yield session


async def ping_database() -> bool:
    """数据库可达性探测（用于健康检查与测试跳过判断）。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001 - 探测就是要吞掉所有异常
        return False


async def dispose_engine() -> None:
    """进程退出时释放连接池。"""
    await engine.dispose()
