"""阶段 3 W3-3 用途 ③/④：会话态与预算计数原子性。

④ 的出口条件是"预算计数原子生效"。真正要防的不是"算错"，
而是**并发下的丢失更新**：10 个协程同时加，最后只加了 7 次 —— 这种 bug 在单测里
不并发就永远看不见，上线后表现为"预算明明设了却超支"。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.cache import TTL_SESSION_SEC, InMemoryCache, NullCache
from app.core.exceptions import LLMBudgetExceeded
from app.core.llm import BudgetGuard


async def test_concurrent_incr_is_not_lost() -> None:
    """10 个协程各加 1，结果必须正好是 10（并发下不能丢失更新）。"""
    cache = InMemoryCache()

    async def bump() -> None:
        await cache.incr("lock:budget:2026-01-01", 1)

    await asyncio.gather(*[bump() for _ in range(10)])

    assert await cache.get("lock:budget:2026-01-01") == "10"


async def test_budget_guard_accumulates_and_blocks() -> None:
    """累加到上限后必须拦住：这是成本兜底，宁可误杀也不能放行。"""
    cache = InMemoryCache()
    guard = BudgetGuard(limit_cents=10, cache=cache)

    await guard.add(6.0)
    await guard.add(3.0)
    assert await guard.spent_cents() == pytest.approx(9.0)

    await guard.check()  # 还没到上限，应当放行
    await guard.add(1.5)
    with pytest.raises(LLMBudgetExceeded):
        await guard.check()


async def test_budget_guard_is_atomic_under_concurrency() -> None:
    """并发 10 次各加 1 分，最终计数必须是 10 分而不是某个更小的数。"""
    cache = InMemoryCache()
    guard = BudgetGuard(limit_cents=1000, cache=cache)

    await asyncio.gather(*[guard.add(1.0) for _ in range(10)])

    assert await guard.spent_cents() == pytest.approx(10.0)


async def test_budget_falls_back_to_local_counting_when_cache_dead() -> None:
    """缓存不可用时退回进程内计数 —— 关键是不能把"没计数依据"当成"花了 0 分"。

    `NullCache.ping()` 返回 False，所以走本地分支；计数仍然生效，预算不会失效。
    """
    guard = BudgetGuard(limit_cents=5, cache=NullCache())

    await guard.add(3.0)
    assert await guard.spent_cents() == pytest.approx(3.0)
    await guard.add(3.0)
    with pytest.raises(LLMBudgetExceeded):
        await guard.check()


def test_budget_key_is_per_day() -> None:
    """按天分 key：昨天的花费不能算进今天的预算，也不能把今天的计数一直留着。"""
    guard = BudgetGuard(limit_cents=1, cache=InMemoryCache())
    key = guard._day_key
    today = __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m-%d")
    assert today in key
    assert TTL_SESSION_SEC <= 48 * 3600  # 会话 TTL 不该长过预算 key 的生命周期
