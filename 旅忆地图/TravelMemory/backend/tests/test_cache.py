"""缓存层单测（R3-3.1 / 3.2 / 3.7）。

三条硬要求驱动本文件的写法：

1. **不装 redis 包也要全绿**（E1 / O-4）：Redis 相关行为用替身模块注入
   （`monkeypatch` 掉 `_load_redis_module`），不依赖本机有没有装 redis。
2. **不许引入真实等待**（§8.9）：TTL 过期用注入的假时钟验证，
   没有一个用例 `asyncio.sleep` 超过 0 秒。
3. **三个实现共用同一组语义断言**：差异只在"返回什么"，不在"要不要存在"。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.cache import (
    CACHE_PREFIXES,
    TTL_GEOCODE_SEC,
    TTL_POI_SEC,
    TTL_ROUTE_SEC,
    TTL_SESSION_SEC,
    CacheClient,
    InMemoryCache,
    NullCache,
    RedisCache,
    _load_redis_module,
    api_cache_key,
    assert_allowed_key,
    build_cache,
    embedding_cache_key,
    lock_key,
    session_key,
)
from app.core.config import Settings, get_settings

#: Protocol 的全部方法：三个实现谁少一个都会在这条断言上红
REQUIRED_METHODS = (
    "get",
    "set",
    "delete",
    "expire",
    "incr",
    "setnx",
    "lpush",
    "lrange",
    "hset",
    "hgetall",
    "ping",
    "close",
)


class _Clock:
    """可推进的假时钟：让 TTL 测试不必真的等 7 天，也不必 sleep 1 秒。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------- 契约
def test_all_implementations_satisfy_protocol():
    """`InMemoryCache` / `NullCache` / `RedisCache` 必须同签名。

    只查方法名而不 `isinstance`：本机没装 redis，`RedisCache` 连构造都不该出现在测试里，
    但"它和另外两个是同一份契约"这件事必须被静态钉住。
    """
    for cls in (InMemoryCache, NullCache, RedisCache):
        missing = [name for name in REQUIRED_METHODS if not callable(getattr(cls, name, None))]
        assert missing == [], f"{cls.__name__} 缺少 CacheClient 方法: {missing}"


def test_instances_are_runtime_protocol_members():
    memory = InMemoryCache(max_entries=8)
    nothing = NullCache()
    assert isinstance(memory, CacheClient)
    assert isinstance(nothing, CacheClient)


def test_redis_module_is_optional():
    """缺 redis 包时 `_load_redis_module()` 返回 None 而不是抛 ImportError（O-4 的核心）。"""
    module = _load_redis_module()
    assert module is None or hasattr(module, "Redis")


# ---------------------------------------------------------------- InMemoryCache 语义
async def test_memory_get_set_delete():
    cache = InMemoryCache(max_entries=8)
    await cache.set("api:x", "value")

    assert await cache.get("api:x") == "value"
    assert await cache.get("api:missing") is None

    await cache.delete("api:x")
    assert await cache.get("api:x") is None


async def test_memory_ttl_really_expires():
    """过期必须**真的失效**：TTL 是缓存正确性的全部内容，假过期比不缓存更危险。"""
    clock = _Clock()
    cache = InMemoryCache(max_entries=8, clock=clock)

    await cache.set("api:poi", "data", TTL_POI_SEC)
    clock.advance(TTL_POI_SEC - 1)
    assert await cache.get("api:poi") == "data", "还没到期就该还在"

    clock.advance(1)
    assert await cache.get("api:poi") is None, "到期后必须失效"


async def test_memory_set_without_ttl_never_expires():
    cache = InMemoryCache(max_entries=8, clock=_Clock())
    await cache.set("emb:1", "vector")
    await cache.expire("emb:1", 0)  # 0 表示"取消过期时间"
    assert await cache.get("emb:1") == "vector"


async def test_memory_incr_counts_and_keeps_first_ttl():
    """首次创建才设 TTL：后续自增不清零倒计时（否则日预算计数会被每次调用无限续命）。"""
    clock = _Clock()
    cache = InMemoryCache(max_entries=8, clock=clock)

    assert await cache.incr("lock:budget:2026-01-01", 5, ttl_sec=60) == 5
    assert await cache.incr("lock:budget:2026-01-01", 5) == 10

    clock.advance(59)
    assert await cache.get("lock:budget:2026-01-01") == "10"
    clock.advance(1)
    assert await cache.get("lock:budget:2026-01-01") is None, "TTL 不该被自增续命"


async def test_memory_incr_is_atomic_under_concurrency():
    """10 个协程并发 INCR → 结果必须是 10（R3-3.5 的原子性，先在替身身上证明）。"""
    cache = InMemoryCache(max_entries=8)

    await asyncio.gather(*(cache.incr("lock:same") for _ in range(10)))

    assert await cache.get("lock:same") == "10"


async def test_memory_setnx_only_wins_on_absent_key():
    clock = _Clock()
    cache = InMemoryCache(max_entries=8, clock=clock)

    assert await cache.setnx("lock:job:42", "node-a", 30) is True
    assert await cache.setnx("lock:job:42", "node-b", 30) is False, "幂等锁不能被抢"

    clock.advance(30)
    assert await cache.setnx("lock:job:42", "node-b", 30) is True, "过期后锁必须可重入"


async def test_memory_list_window_trims_to_max_len():
    """③ 滑动窗口：最新在前，多余的被挤掉。"""
    cache = InMemoryCache(max_entries=8)
    for i in range(5):
        await cache.lpush("sess:abc", f"msg-{i}", max_len=3, ttl_sec=TTL_SESSION_SEC)

    assert await cache.lrange("sess:abc") == ["msg-4", "msg-3", "msg-2"]


async def test_memory_hash_merges_fields():
    cache = InMemoryCache(max_entries=8)
    await cache.hset("sess:abc:state", {"a": "1"}, ttl_sec=TTL_SESSION_SEC)
    await cache.hset("sess:abc:state", {"b": "2"}, ttl_sec=TTL_SESSION_SEC)

    assert await cache.hgetall("sess:abc:state") == {"a": "1", "b": "2"}
    assert await cache.hgetall("sess:missing") == {}


async def test_memory_lru_evicts_least_recently_used():
    cache = InMemoryCache(max_entries=3)
    for i in range(3):
        await cache.set(f"api:k{i}", str(i))

    await cache.get("api:k0")  # 用一下 k0，让它变成"最近使用"
    await cache.set("api:k3", "3")

    assert await cache.get("api:k1") is None, "最久未用的 k1 应先被淘汰"
    assert await cache.get("api:k0") == "0", "刚被读过的不该先被淘汰"


async def test_memory_ping_is_true():
    assert await InMemoryCache().ping() is True


# ---------------------------------------------------------------- NullCache 语义
async def test_null_cache_is_transparent():
    cache = NullCache()
    await cache.set("api:x", "v", 60)
    await cache.hset("sess:x", {"a": "1"}, ttl_sec=60)
    await cache.lpush("sess:x", "m", max_len=3, ttl_sec=60)

    assert await cache.get("api:x") is None
    assert await cache.hgetall("sess:x") == {}
    assert await cache.lrange("sess:x") == []
    assert await cache.incr("lock:x") == 0, "0 = 无计数依据，调用方需先 ping()"
    assert await cache.setnx("lock:x", "v", 30) is False
    assert await cache.ping() is False


# ---------------------------------------------------------------- TTL 常量
def test_ttl_constants_match_spec():
    """§3.2 四级用途的 TTL 数值：POI 7d / 路线 1d / 逆地编 30d / 会话 24h。"""
    assert TTL_POI_SEC == 7 * 24 * 3600
    assert TTL_ROUTE_SEC == 24 * 3600
    assert TTL_GEOCODE_SEC == 30 * 24 * 3600
    assert TTL_SESSION_SEC == 24 * 3600


# ---------------------------------------------------------------- key 构造
def test_api_cache_key_is_order_insensitive():
    left = api_cache_key("poi", {"keyword": "古镇", "city": "嘉兴"})
    right = api_cache_key("poi", {"city": "嘉兴", "keyword": "古镇"})

    assert left == right, "入参顺序不该影响 key（LLM 吐参顺序是不稳定的）"
    assert left.startswith("api:poi:")
    assert assert_allowed_key(left).count(":") == 2


def test_api_cache_key_differs_by_payload():
    assert api_cache_key("poi", {"q": "a"}) != api_cache_key("poi", {"q": "b"})
    assert api_cache_key("poi", {"q": "a"}) != api_cache_key("route", {"q": "a"})


def test_embedding_cache_key_hides_raw_content():
    content = "这是一段可能很长的用户原文" * 50
    key = embedding_cache_key(content, "bge-m3@v1")

    assert key.startswith("emb:")
    assert content not in key, "用户原文不得出现在 key 里（R3-3.7）"
    assert len(key) < 100, "哈希型 key 的长度必须与原文长度无关"
    assert embedding_cache_key(content, "bge-m3@v1") == key
    assert embedding_cache_key(content, "other-model") != key, "换模型必须换 key"


def test_lock_and_session_keys():
    assert lock_key("budget", "2026-01-01") == "lock:budget:2026-01-01"
    assert session_key("abc") == "sess:abc"


def test_key_prefix_allowlist():
    """写进缓存的东西必须落在四类前缀内 —— 这是"不缓存用户数据本体"的第一道闸。"""
    for key in (
        api_cache_key("poi", {"q": "x"}),
        embedding_cache_key("c", "m"),
        session_key("s1"),
        lock_key("budget", "d"),
    ):
        assert key.split(":", 1)[0] in CACHE_PREFIXES
        assert_allowed_key(key)

    with pytest.raises(ValueError):
        assert_allowed_key("user:42:raw-content")


# ---------------------------------------------------------------- RedisCache 降级
class _BrokenRedisModule:
    """替身 redis 模块：构造成功，但任何调用都炸 —— 模拟 Redis 挂掉。

    为什么要替身而不是连真的：`RedisCache` 的降级逻辑恰恰要在"连不上"时验证，
    而本机环境（E1）本来就没有 Redis，真连只会得到一个更慢的失败。
    """

    def __init__(self) -> None:
        self.connections = 0

    def _client(self, *_args: Any, **_kwargs: Any) -> Any:
        self.connections += 1
        return _AlwaysFailing()

    @property
    def Redis(self) -> Any:
        return SimpleNamespace(from_url=self._client)


class _AlwaysFailing:
    async def _fail(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("模拟 Redis 不可达")

    get = set = delete = expire = incr = ttl = lpush = ltrim = hset = hgetall = lrange = _fail
    ping = setnx = _fail

    async def aclose(self) -> None:
        return None


async def test_redis_cache_degrades_after_first_failure(monkeypatch):
    """A3：首次连接失败后标 dead，**后续直接 miss 不再重试**（否则每请求都卡一个超时）。"""
    broken = _BrokenRedisModule()
    monkeypatch.setattr("app.core.cache._load_redis_module", lambda: broken)

    cache = RedisCache("redis://127.0.0.1:6379/0", socket_timeout_sec=0.1)

    assert await cache.get("api:x") is None
    assert await cache.ping() is False
    assert cache.is_dead is True

    connections_after_first = broken.connections
    assert await cache.get("api:y") is None
    assert await cache.incr("lock:x") == 0
    assert broken.connections == connections_after_first, "dead 之后不该再发起连接"


async def test_redis_cache_write_paths_swallow_errors(monkeypatch):
    """写路径同样不能抛：缓存是旁路设施，写失败只影响性能不影响正确性。"""
    monkeypatch.setattr("app.core.cache._load_redis_module", lambda: _BrokenRedisModule())
    cache = RedisCache("redis://127.0.0.1:6379/0")

    await cache.set("api:x", "v", 60)
    await cache.hset("sess:x", {"a": "1"}, ttl_sec=60)
    await cache.lpush("sess:x", "m", max_len=3, ttl_sec=60)
    await cache.expire("api:x", 60)
    await cache.delete("api:x")

    assert cache.is_dead is True
    assert await cache.setnx("lock:x", "v", 30) is False
    assert await cache.hgetall("sess:x") == {}
    assert await cache.lrange("sess:x") == []


# ---------------------------------------------------------------- 工厂
def test_build_cache_defaults_to_in_memory(monkeypatch):
    """默认（cache_enabled=False）走进程内缓存：有 TTL 语义可测，且不用任何外部服务。"""
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.setenv("CACHE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        cache = build_cache()
    finally:
        get_settings.cache_clear()

    assert isinstance(cache, InMemoryCache)


def test_build_cache_backend_null(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "null")
    get_settings.cache_clear()
    try:
        assert isinstance(build_cache(), NullCache)
    finally:
        get_settings.cache_clear()


def test_build_cache_backend_memory(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    monkeypatch.setenv("CACHE_MAX_ENTRIES", "7")
    get_settings.cache_clear()
    try:
        cache = build_cache()
    finally:
        get_settings.cache_clear()

    assert isinstance(cache, InMemoryCache)
    assert cache._max_entries == 7


def test_build_cache_falls_back_when_redis_package_missing(monkeypatch):
    """**任何** Redis 侧异常都必须降级为 InMemoryCache 而不是抛错（PROJECT.md §二、[8]）。"""
    monkeypatch.setattr("app.core.cache._load_redis_module", lambda: None)
    monkeypatch.setenv("CACHE_ENABLED", "true")
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    get_settings.cache_clear()
    try:
        cache = build_cache()
    finally:
        get_settings.cache_clear()

    assert isinstance(cache, InMemoryCache), "缺 redis 包也不能让主流程炸"


def test_build_cache_without_redis_url_stays_in_memory(monkeypatch):
    """AUTO 模式下：开了开关但没配 REDIS_URL → 留在内存实现。"""
    settings = Settings(
        _env_file=None,
        cache_enabled=True,
        cache_backend="auto",
        redis_url="",
    )
    assert isinstance(build_cache(settings), InMemoryCache)


def test_build_cache_never_throws_on_garbage_url(monkeypatch):
    """连非法的 redis 模块都不能让工厂抛错：缓存失败至多降级，绝不能打断主流程。"""
    monkeypatch.setattr("app.core.cache._load_redis_module", lambda: SimpleNamespace())
    settings = Settings(_env_file=None, cache_enabled=True, redis_url="not-a-url")

    assert isinstance(build_cache(settings), InMemoryCache)
