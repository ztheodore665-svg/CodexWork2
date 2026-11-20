"""测试全局夹具。

两个原则：
  1. 单测**不依赖任何外部服务**：LLM 用 mock、embedding 用本地哈希、检索用内存实现
  2. 单测**不写真实 trace 目录**：TRACE_DIR 指向临时目录
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 探测真实数据库的 TCP 超时（秒）。
#:
#: 为什么必须先做一次裸 TCP 探测再去连数据库：asyncpg 的默认连接超时是 **60 秒**，
#: 而"端口被防火墙丢弃（不回 RST）"时连接不会立刻失败，会一直挂到超时。
#: 实测 9 个依赖真实库的用例**在被 skip 之前各干等 60 秒**，全量测试因此从 61 秒
#: 涨到 559 秒 —— 而这 540 秒全部花在"注定要跳过"的用例上。
#:
#: 先花 0.5 秒确认端口不通，就能在 skip 之前零成本地放弃。
_DB_PROBE_TIMEOUT_SEC = 0.5

#: PostgreSQL 握手超时（秒）。端口通但服务不回包时，`ping_database()` 会一直挂到
#: asyncpg 默认的 60 秒 —— 所以端口探测之后还要再封一次顶。
_DB_HANDSHAKE_TIMEOUT_SEC = 2.0


#: 探测结果缓存。**数据库可达性在一轮测试里不会变**，
#: 而每个 db 用例都探一次会把"注定跳过"的成本从 2.5 秒放大成 2.5×N 秒。
#: 只在进程内缓存（不落盘），避免"上次有库所以这次也当有库"的跨轮误判。
_DB_REACHABLE: bool | None = None


async def db_reachable() -> bool:
    """真实数据库是否可达（**快速失败 + 结果记忆化**版本）。

    分两步：先用裸 TCP 探端口（0.5 秒封顶），通了才交给 `ping_database()` 做一次
    `select 1`。只做前者会漏掉"端口开着但不是 PostgreSQL"的情况，
    只做后者会在端口被丢弃时白等 60 秒。
    """
    global _DB_REACHABLE  # noqa: PLW0603 - 见上方 `_DB_REACHABLE` 的注释
    if _DB_REACHABLE is not None:
        return _DB_REACHABLE
    _DB_REACHABLE = await _probe_database()
    return _DB_REACHABLE


async def _probe_database() -> bool:
    from sqlalchemy.engine import make_url

    from app.db.session import ping_database

    url = make_url(get_settings().database_url)
    host = url.host or "127.0.0.1"
    port = int(url.port or 5432)

    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_DB_PROBE_TIMEOUT_SEC
        )
        writer.close()
        await writer.wait_closed()
    except (TimeoutError, OSError):
        return False

    # 端口通 ≠ 数据库可用：可能是反代/容器网络接受了连接却迟迟不回握手包。
    # `ping_database()` 本身没有超时上限（asyncpg 默认 60s），所以这里必须再封一次顶。
    try:
        return await asyncio.wait_for(ping_database(), timeout=_DB_HANDSHAKE_TIMEOUT_SEC)
    except TimeoutError:
        logger.warning(
            "数据库端口 %s:%s 可达但握手超时（>%ss），本次按不可用处理",
            host,
            port,
            _DB_HANDSHAKE_TIMEOUT_SEC,
        )
        return False


@pytest.fixture(autouse=True)
def test_env(tmp_path, monkeypatch) -> None:
    """每个用例都在确定的环境里跑，避免本地 .env 或真实密钥影响结果。"""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hashing")
    # 注意：不要在这里改 EMBEDDING_DIM。依赖真实库的用例会往迁移建好的
    # vector(1024) 列里写向量，维度改小会直接 DataError（事务被判失败）。
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:nobody@127.0.0.1:5432/none")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def user_id() -> uuid.UUID:
    """一个固定的 user_id（单用户模型，测试里不需要真的入库）。"""
    return uuid.uuid4()
