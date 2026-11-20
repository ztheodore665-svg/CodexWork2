"""缓存抽象：Redis 四级用途的统一入口（设计文档 §3.2）。

四级用途与 TTL（§3.2 原文）：

| # | 用途 | TTL | key 前缀 |
| --- | --- | --- | --- |
| ① | 外部 API 结果 | POI 7d / 路线 1d / 逆地编 30d | `api` |
| ② | Embedding 结果 | 永久（靠 LRU 淘汰） | `emb` |
| ③ | 短期会话 + 流式状态 | 24h（List 滑动窗口） | `sess` |
| ④ | 幂等锁 + 预算计数 | 短期（`SETNX` / `INCR`） | `lock` |

## 三个实现，一份契约

- `InMemoryCache`：**默认**。项目是单用户单进程部署（Q2 已拍板），进程内字典够用，
  而且它能在没有 Redis 的机器上完整验证 TTL / 原子自增 / LRU 语义 —— 这是 E1 环境下唯一诚实的选择。
- `RedisCache`：真实 Redis。`import redis.asyncio` 写在函数里的 `try/except ImportError`，
  **不装 redis 包也能 import 本模块**，`redis` 只作为可选依赖存在（O-4）。
- `NullCache`：`test-degradation` 要的"缓存完全不参与"基线；也是排障时的对照开关。

## 为什么用 Protocol 而不是 ABC

三个实现之间没有一行可共享的代码，只有共享契约。Protocol（结构化子类型）让
`RedisCache` 在**没装 redis 的环境**里依然通过静态检查，也让测试可以注入任意替身。
ABC 会把"依赖 redis 包才能定义类"这件事变成硬性前提。

## 降级约定（A3）：连不上就不再重试

Redis 首次调用失败即在实例内标 `_dead=True`，之后一律"中性返回"
（`get → None`、`incr → 0`、`setnx → False`），**不再发起连接**。
否则每个请求都要干等一次连接超时，一个旁路设施能把整个问答拖到超时。

⚠️ `incr` 返回 0 **不等于"本次计数为 0"**，而是"没有计数依据"。预算类调用方必须先 `ping()`：
不可用则退回本地计数，切忌把 0 当成 0 成本直接放行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.exceptions import DegradedException
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------ 常量
#: 四级用途的 TTL。**唯一定义处**：其他模块一律 import 这里，不要各自写字面量，
#: 否则"7 天"会在三份代码里漂移成三个不同的数。
TTL_POI_SEC: int = 7 * 24 * 3600  # ① POI：地点信息稳定，扛得住长缓存
TTL_ROUTE_SEC: int = 24 * 3600  # ① 路线：路况会变，但要覆盖一次完整行程规划
TTL_GEOCODE_SEC: int = 30 * 24 * 3600  # ① 逆地编：坐标几乎不变，且配额最贵
TTL_SESSION_SEC: int = 24 * 3600  # ③ 短期会话 + 流式状态
#: ④ 日预算计数：必须活过"跨日"这个边界（48h 保证 23:59 累加的值到次日仍在），
#: 也不能太长（否则旧日期的 key 会一直占着内存）。
TTL_BUDGET_SEC: int = 48 * 3600

#: 允许的 key 前缀（R3-3.7：写进缓存的东西必须能一眼归类，且不得包含用户数据本体）
CACHE_PREFIXES: tuple[str, ...] = ("api", "emb", "sess", "lock")


@runtime_checkable
class CacheClient(Protocol):
    """缓存语义契约。`InMemoryCache` / `RedisCache` / `NullCache` 共用同一组单测。

    所有方法都是 async：今天的实现是内存字典（没有 I/O），明天换成 Redis 就有，
    接口提前异步化是为了不让调用方在切换实现时集体改签名。
    """

    async def get(self, key: str) -> str | None:
        """读；不存在或已过期一律返回 `None`（不存在与过期对调用方是同一件事）。"""
        ...

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        """写。`ttl_sec` 为 None / ≤0 表示不设过期（由 LRU 兜底淘汰）。"""
        ...

    async def delete(self, key: str) -> None: ...

    async def expire(self, key: str, ttl_sec: int) -> None: ...

    async def incr(self, key: str, by: int = 1, *, ttl_sec: int | None = None) -> int:
        """原子自增并返回新值。Redis 走 `INCR`；内存实现用 `asyncio.Lock` 保证原子。

        `ttl_sec` **只在 key 首次创建时生效**（与 Redis `INCR` 不清空已有 TTL 的行为一致，
        预算日计数属于这种语义：过了 24h 整体过期重来，不能被每一次自增续命）。
        """
        ...

    async def setnx(self, key: str, value: str, ttl_sec: int) -> bool:
        """幂等锁：只在 key 不存在时写入并设置过期，成功返回 True。"""
        ...

    async def lpush(self, key: str, value: str, *, max_len: int, ttl_sec: int) -> None:
        """从左侧压入并按 `max_len` 截断（③ 滑动窗口：最新在前，最旧自然被挤掉）。"""
        ...

    async def lrange(self, key: str) -> list[str]: ...

    async def hset(self, key: str, mapping: dict[str, str], *, ttl_sec: int) -> None: ...

    async def hgetall(self, key: str) -> dict[str, str]: ...

    async def ping(self) -> bool:
        """可用性探测。**预算计数这类"不可丢"的用途必须先问它**。"""
        ...

    async def close(self) -> None: ...


class InMemoryCache:
    """默认实现：进程内字典 + 惰性过期 + LRU。

    惰性过期（`get` 时判 TTL）而不是起一个定时任务清理：后者要么浪费一个协程，
    要么在清理瞬间抢 CPU；而缓存是旁路设施，绝不能引入后台生命周期。

    `clock` 可注入是为了让 TTL 测试**不必真睡一秒** ——
    套件已经偏慢（基线 ≈9 分钟），阶段 3 的新测试不许引入真实等待（§8.9）。
    """

    def __init__(
        self,
        *,
        max_entries: int = 5000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._clock: Callable[[], float] = clock or time.monotonic
        self._strings: OrderedDict[str, str] = OrderedDict()
        self._hashes: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._lists: OrderedDict[str, list[str]] = OrderedDict()
        #: key → 到期时刻（monotonic）；没有记录 = 永不过期（靠 LRU 淘汰）
        self._expiry: dict[str, float] = {}
        #: 统一的 LRU 顺序表：最近访问的在队尾
        self._order: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- 读
    async def get(self, key: str) -> str | None:
        if self._expired(key):
            return None
        value = self._strings.get(key)
        if value is None:
            return None
        self._touch(key)
        return value

    async def hgetall(self, key: str) -> dict[str, str]:
        if self._expired(key):
            return {}
        target = self._hashes.get(key)
        if target is None:
            return {}
        self._touch(key)
        return dict(target)

    async def lrange(self, key: str) -> list[str]:
        if self._expired(key):
            return []
        items = self._lists.get(key)
        if items is None:
            return []
        self._touch(key)
        return list(items)

    # ---------------------------------------------------------------- 写
    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        self._expired(key)
        self._strings[key] = value
        # 覆盖写必须重算 TTL：沿用旧 TTL 是分布式计数里最经典的"永不过期"事故来源
        self._reschedule(key, ttl_sec)
        self._touch(key)

    async def hset(self, key: str, mapping: dict[str, str], *, ttl_sec: int) -> None:
        self._expired(key)
        target = self._hashes.setdefault(key, {})
        target.update(mapping)
        self._reschedule(key, ttl_sec)
        self._touch(key)

    async def lpush(self, key: str, value: str, *, max_len: int, ttl_sec: int) -> None:
        self._expired(key)
        items = self._lists.setdefault(key, [])
        items.insert(0, value)
        del items[max(1, int(max_len)) :]
        self._reschedule(key, ttl_sec)
        self._touch(key)

    async def delete(self, key: str) -> None:
        self._drop(key)

    async def expire(self, key: str, ttl_sec: int) -> None:
        if self._expired(key) or not self._exists(key):
            return
        self._reschedule(key, ttl_sec if ttl_sec > 0 else None)

    async def incr(self, key: str, by: int = 1, *, ttl_sec: int | None = None) -> int:
        async with self._lock:
            self._expired(key)
            current = int(self._strings.get(key, "0"))
            value = current + int(by)
            self._strings[key] = str(value)
            # 首次创建才设 TTL；已有 TTL 的自增不清零倒计时（对齐 Redis INCR）
            if ttl_sec is not None and ttl_sec > 0 and key not in self._expiry:
                self._reschedule(key, ttl_sec)
            self._touch(key)
            return value

    async def setnx(self, key: str, value: str, ttl_sec: int) -> bool:
        if self._expired(key):
            self._drop(key)
        if self._exists(key):
            return False
        self._strings[key] = value
        self._reschedule(key, ttl_sec)
        self._touch(key)
        return True

    # ---------------------------------------------------------------- 运维
    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._strings.clear()
        self._hashes.clear()
        self._lists.clear()
        self._expiry.clear()
        self._order.clear()

    @property
    def size(self) -> int:
        """当前存活的 key 数（单测与排障用）。"""
        return len(self._order)

    # ---------------------------------------------------------------- 内部
    def _exists(self, key: str) -> bool:
        return key in self._strings or key in self._hashes or key in self._lists

    def _expired(self, key: str) -> bool:
        """惰性过期：命中则连带清理，返回是否已过期。"""
        deadline = self._expiry.get(key)
        if deadline is None:
            return False
        if self._clock() >= deadline:
            self._drop(key)
            return True
        return False

    def _drop(self, key: str) -> None:
        self._expiry.pop(key, None)
        self._strings.pop(key, None)
        self._hashes.pop(key, None)
        self._lists.pop(key, None)
        self._order.pop(key, None)

    def _reschedule(self, key: str, ttl_sec: int | None) -> None:
        if ttl_sec is None or ttl_sec <= 0:
            self._expiry.pop(key, None)
            return
        self._expiry[key] = self._clock() + ttl_sec

    def _touch(self, key: str) -> None:
        """记录一次访问并按容量做 LRU 淘汰。"""
        if key in self._order:
            self._order.move_to_end(key)
        else:
            self._order[key] = None
        while len(self._order) > self._max_entries:
            victim, _ = self._order.popitem(last=False)
            self._drop(victim)


class NullCache:
    """直通实现：什么都不存、读什么都 miss。

    它的价值是**给"没有缓存"一个可被测出来的形态**：
    `test-degradation` 用它证明关闭缓存后主流程完全不依赖缓存副作用；
    排查"是不是缓存的锅"时也能一行切过去。
    """

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def expire(self, key: str, ttl_sec: int) -> None:
        return None

    async def incr(self, key: str, by: int = 1, *, ttl_sec: int | None = None) -> int:
        # 返回 0 = "无计数依据"，调用方必须先 ping() 判定可用性
        return 0

    async def setnx(self, key: str, value: str, ttl_sec: int) -> bool:
        return False

    async def lpush(self, key: str, value: str, *, max_len: int, ttl_sec: int) -> None:
        return None

    async def lrange(self, key: str) -> list[str]:
        return []

    async def hset(self, key: str, mapping: dict[str, str], *, ttl_sec: int) -> None:
        return None

    async def hgetall(self, key: str) -> dict[str, str]:
        return {}

    async def ping(self) -> bool:
        return False

    async def close(self) -> None:
        return None


def _load_redis_module() -> Any | None:
    """惰性导入 `redis.asyncio`。**缺包返回 None，不抛异常** —— 交给工厂去降级。

    放在函数里而不是模块顶部，`redis` 就永远不会成为本项目的事实必装依赖（O-4 / E1）。
    """
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415

        return aioredis
    except ImportError:
        return None


class RedisCache:
    """真实 Redis。

    **构造不做网络 I/O**：工厂 `build_cache` 是同步的，没法在构造时 await 探测连通性。
    所以"连不上"的判定发生在第一次真实调用，失败后打 `_dead` 标记（A3）。
    """

    def __init__(self, url: str = "", *, socket_timeout_sec: float = 1.0) -> None:
        module = _load_redis_module()
        if module is None:
            raise DegradedException(
                "未安装 redis 包（可选依赖）：pip install redis>=5 后可启用 RedisCache，"
                "未安装时 build_cache() 会自动降级为 InMemoryCache"
            )
        if not url.strip():
            raise DegradedException("REDIS_URL 为空，无法构造 RedisCache")

        self._url = url.strip()
        self._timeout_sec = max(0.05, float(socket_timeout_sec))
        self._client: Any = module.Redis.from_url(
            self._url,
            socket_timeout=self._timeout_sec,
            socket_connect_timeout=self._timeout_sec,
            decode_responses=True,  # 统一 str 语义，与 InMemoryCache 对齐
        )
        self._dead: bool = False

    # ---------------------------------------------------------------- 读
    async def get(self, key: str) -> str | None:
        async with self._guarded() as client:
            if client is None:
                return None
            value: str | None = await client.get(key)
            return value

    async def hgetall(self, key: str) -> dict[str, str]:
        async with self._guarded() as client:
            if client is None:
                return {}
            raw: dict[str, str] = await client.hgetall(key)
            return dict(raw)

    async def lrange(self, key: str) -> list[str]:
        async with self._guarded() as client:
            if client is None:
                return []
            items: list[str] = await client.lrange(key, 0, -1)
            return list(items)

    # ---------------------------------------------------------------- 写
    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None:
        async with self._guarded() as client:
            if client is None:
                return
            ex = ttl_sec if ttl_sec is not None and ttl_sec > 0 else None
            await client.set(key, value, ex=ex)

    async def hset(self, key: str, mapping: dict[str, str], *, ttl_sec: int) -> None:
        async with self._guarded() as client:
            if client is None:
                return
            await client.hset(key, mapping=mapping)
            if ttl_sec > 0:
                await client.expire(key, ttl_sec)

    async def lpush(self, key: str, value: str, *, max_len: int, ttl_sec: int) -> None:
        async with self._guarded() as client:
            if client is None:
                return
            await client.lpush(key, value)
            await client.ltrim(key, 0, max(0, int(max_len) - 1))
            if ttl_sec > 0:
                await client.expire(key, ttl_sec)

    async def delete(self, key: str) -> None:
        async with self._guarded() as client:
            if client is None:
                return
            await client.delete(key)

    async def expire(self, key: str, ttl_sec: int) -> None:
        async with self._guarded() as client:
            if client is None:
                return
            await client.expire(key, ttl_sec)

    async def incr(self, key: str, by: int = 1, *, ttl_sec: int | None = None) -> int:
        async with self._guarded() as client:
            if client is None:
                return 0
            value = int(await client.incr(key, int(by)))
            # `EXPIRE nx` 依赖 Redis ≥7.0 的行为，这里改用「INCR → 查 TTL → 只有无过期才设」
            # 两步：多一次往返，换来的是对 Redis 5/6 都能用，且语义与 INCR 完全一致。
            if ttl_sec is not None and ttl_sec > 0 and int(await client.ttl(key)) < 0:
                await client.expire(key, ttl_sec)
            return value

    async def setnx(self, key: str, value: str, ttl_sec: int) -> bool:
        async with self._guarded() as client:
            if client is None:
                return False
            ok: bool | None = await client.set(key, value, nx=True, ex=max(1, int(ttl_sec)))
            return bool(ok)

    # ---------------------------------------------------------------- 运维
    async def ping(self) -> bool:
        if self._dead:
            return False
        try:
            await self._client.ping()
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路设施失败只降级
            self._mark_dead(exc)
            return False

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭 Redis 连接失败（忽略）: %s", exc)

    # ---------------------------------------------------------------- 内部
    @property
    def is_dead(self) -> bool:
        """是否已经"连不上"过 —— 排障与单测用。"""
        return self._dead

    def _mark_dead(self, exc: BaseException) -> None:
        if not self._dead:
            logger.warning(
                "Redis 不可用（%s），本进程后续缓存请求一律 miss / no-op，不再重试连接；"
                "修复后需重建 CacheClient 或重启进程",
                exc,
            )
        self._dead = True

    @asynccontextmanager
    async def _guarded(self) -> AsyncIterator[Any | None]:
        """把"Redis 不可用"统一降级成 client=None 的上下文（A3）。

        catch `Exception` 而不是 redis 的特定异常类型：惰性 import 意味着编译期拿不到
        `redis.exceptions`，而本文件的策略本来就是"任何 Redis 错误都当降级处理"。
        """
        if self._dead:
            yield None
            return
        try:
            yield self._client
        except Exception as exc:  # noqa: BLE001
            self._mark_dead(exc)


_MEMORY_FALLBACK_NOTE = "降级为进程内 InMemoryCache（功能与测试均不受影响）"


def build_cache(settings: Settings | None = None) -> CacheClient:
    """缓存工厂。**永不抛错**：任何异常都变成 warning + `InMemoryCache` 降级。

    决策顺序：
      - `CACHE_BACKEND=null`  → `NullCache`（显式关闭缓存）
      - `CACHE_BACKEND=memory`→ `InMemoryCache`
      - `CACHE_BACKEND=redis` → 试 `RedisCache`
      - `auto`（默认）：`CACHE_ENABLED=true` 且配了 `REDIS_URL` 才走 Redis，否则 `InMemoryCache`

    注意 `CACHE_ENABLED=false`（默认）返回的是 **InMemoryCache 而不是 NullCache**：
    单进程部署下进程内缓存仍然能省掉重复的外网调用、重复 embedding（§3.2 ②④的收益还在），
    真正需要"完全不缓存"的对照场景由 `CACHE_BACKEND=null` 显式表达。
    """
    settings = settings or get_settings()
    backend = (settings.cache_backend or "auto").strip().lower()
    max_entries = max(1, int(settings.cache_max_entries))

    if backend == "null":
        logger.info("CACHE_BACKEND=null：本次运行完全不缓存")
        return NullCache()

    if backend == "memory":
        return InMemoryCache(max_entries=max_entries)

    if backend == "redis":
        return _build_redis_or_memory(settings, max_entries)

    # ---- auto ----
    if not settings.redis_url.strip() or not is_enabled("cache", settings):
        return InMemoryCache(max_entries=max_entries)
    return _build_redis_or_memory(settings, max_entries)


def _build_redis_or_memory(settings: Settings, max_entries: int) -> CacheClient:
    try:
        return RedisCache(settings.redis_url, socket_timeout_sec=settings.redis_socket_timeout_sec)
    except Exception as exc:  # noqa: BLE001 - 缺包 / URL 非法一律降级
        logger.warning("RedisCache 不可用：%s；%s", exc, _MEMORY_FALLBACK_NOTE)
        return InMemoryCache(max_entries=max_entries)


# ------------------------------------------------------------------ key 构造
def api_cache_key(namespace: str, payload: dict[str, Any]) -> str:
    """`api:<namespace>:<sha1>` —— ① 外部 API 结果专用。

    为什么要归一化（`sort_keys` + 紧凑分隔符）：Python dict 的键顺序是稳定的，
    但 LLM 填出来的入参**不一定按同样顺序**（今天 `{city, keyword}`，明天 LLM 吐 `{keyword, city}`），
    不归一化就会产生两个等价缓存key —— 命中率莫名其妙掉一半。
    """
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    return f"api:{namespace}:{digest}"


def embedding_cache_key(content: str, model: str) -> str:
    """`emb:<sha256>` —— ② Embedding 结果专用。

    **原文必须参与哈希、但不出现在 key 里**：key 里塞原文会让 key 长度随文档线性增长，
    而缓存服务（含内存实现）的索引成本是按 key 长度算的。
    用 `\x00` 分隔 model 与 content，避免 "a"+"/"+"b" 与 "a/"+"b" 撞 key。
    """
    digest = hashlib.sha256(f"{model}\x00{content}".encode()).hexdigest()
    return f"emb:{digest}"


def lock_key(purpose: str, unit: str) -> str:
    """`lock:<purpose>:<unit>` —— ④ 幂等锁 / 计数，unit = 分钟戳 / 日期 / job_id。"""
    return f"lock:{purpose}:{unit}"


def session_key(session_id: str) -> str:
    """`sess:<session_id>` —— ③ 短期会话与流式状态。"""
    return f"sess:{session_id}"


# 阶段 16：`SessionStore` 已删除。
#
# 它从未被任何生产代码调用 —— 会话历史走 `agent.memory`（阶段 4 的 MemoryService），
# 而不是缓存里的一个 list。留着它的代价不是几十行代码，是**误导**：
# 读代码的人会以为"会话态在 Redis 里"，于是去改一个根本不在链路上的类。
# 缓存层的 `lpush` / `lrange` 仍然保留（它们是通用原语，且有独立的形状测试）。


def assert_allowed_key(key: str) -> str:
    """校验 key 落在四类前缀白名单内（R3-3.7），不合法时抛 ValueError。

    这是一条**防漂移**的护栏：缓存里不该出现用户数据本体，最先管住的就是 key 的形状。
    """
    prefix = key.split(":", 1)[0]
    if prefix not in CACHE_PREFIXES:
        raise ValueError(
            f"缓存 key 前缀非法: {prefix!r}（允许: {', '.join(CACHE_PREFIXES)}）; key={key}"
        )
    return key
