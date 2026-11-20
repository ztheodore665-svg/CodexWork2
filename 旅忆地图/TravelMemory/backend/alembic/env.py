"""Alembic 运行环境（异步版本）。

为什么用异步：项目运行期统一用 SQLAlchemy 2.0 async Session（asyncpg 驱动），
迁移如果走同步 psycopg 会出现两套连接方式与两种 URL，容易不一致。

离线模式（--sql）只生成 SQL 不连库，用于 code review 或手工执行。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 导入全部模型，确保 Base.metadata 里包含所有表（autogenerate 依赖）
import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings  # 配置唯一入口：PROJECT.md 第 3 部分
from app.models.base import Base

config = context.config

# 数据库 URL 来自 .env，不在 alembic.ini 里重复配置
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只输出 SQL，不连接数据库。"""
    from sqlalchemy import create_engine

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """给定连接执行迁移（由上层协程调用）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式：用 asyncpg 建立连接并执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
