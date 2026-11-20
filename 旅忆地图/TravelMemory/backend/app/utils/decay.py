"""记忆衰减的**纯函数**集合（阶段 4 W4-1）。

## 为什么放在 utils 而不是 agent/memory 下

衰减既被 agent 层（`MemoryService` 组装上下文时算权重）使用，
也被 services 层（`SqlMemoryStore` 写入时把半衰期换算成 λ）使用。
放在 agent 下会让 services 反向依赖 agent，直接违反
PROJECT.md §二、[4] 的单向依赖铁律 —— 与 `utils/citation.py`
（agent 的 self_check 与 eval 的评测器共用）是完全同一个道理。

## 公式从哪来

设计文档 §7.5：

```
weight(t) = w0 · exp(-λ · Δt_days)，λ = ln2 / half_life_days
默认 half_life_days = 30（偏好类 90）
weight < 0.05 或已过期 → 归档（不物理删除）
```

## 为什么 λ 要由半衰期算，而不是直接配一个 λ

半衰期是**人类能直觉判断的量**："这条偏好 90 天后还剩一半权重"一听就懂。
λ = 0.0077 这种数字没人有感觉，调参时只能瞎试。
所以对外暴露 `half_life_days`，λ 只是它的换算结果。

## 为什么 Δt 以 last_seen_at 而不是 created_at 为起点

「不喜欢人多」这种偏好是**长期成立**的，如果按写入时间衰减，
用户天天在用、却因为"三个月前说的"就被归档，明显不合理。
每次这条记忆真的进了上下文就 `touch` 一下刷新 `last_seen_at`，
于是衰减衡量的是"多久没被用上"，这才是我们想要遗忘的东西。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

#: 低于该权重即归档（设计文档 §7.5）。不物理删除：数据还在，随时能复活
ARCHIVE_THRESHOLD = 0.05

#: 默认半衰期（天）
DEFAULT_HALF_LIFE_DAYS = 30.0
#: 偏好（语义）类的默认半衰期（天）—— 偏好变化慢，衰减也要慢
PREFERENCE_HALF_LIFE_DAYS = 90.0


def half_life_to_lambda(half_life_days: float) -> float:
    """半衰期 → 衰减常数 λ。

    `λ = ln2 / half_life_days` 的来路：要求 `exp(-λ · T) = 0.5`，
    两边取对数即得 `λ = ln2 / T`。
    """
    if half_life_days <= 0:
        # 半衰期非正没有物理意义。抛错而不是静默取默认值 ——
        # 配错成 0 会让所有记忆瞬间归零，属"静默失效"，必须当场炸出来
        raise ValueError(f"half_life_days 必须为正数，收到 {half_life_days}")
    return math.log(2) / half_life_days


def lambda_to_half_life(decay_rate: float) -> float:
    """λ → 半衰期（`half_life_to_lambda` 的逆运算，读库回填配置时用）。"""
    if decay_rate <= 0:
        raise ValueError(f"decay_rate 必须为正数，收到 {decay_rate}")
    return math.log(2) / decay_rate


def days_since(moment: datetime, now: datetime | None = None) -> float:
    """距现在过去了多少天（**允许小数**，否则一天内的记忆 Δt 全是 0，衰减完全失效）。

    未来时间（时钟回拨 / 测试数据）截断为 0，避免 `exp(正数)` 算出大于 1 的权重。
    """
    reference = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return max(0.0, (reference - moment).total_seconds() / 86400.0)


def decay_weight(
    w0: float,
    delta_days: float,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """衰减后的权重：`w0 · exp(-λ · Δt)`。纯函数，单测直接钉死。"""
    return w0 * math.exp(-half_life_to_lambda(half_life_days) * max(0.0, delta_days))


def weight_from_rate(w0: float, delta_days: float, decay_rate: float) -> float:
    """按**已落库的 λ** 算权重（读取路径用这个，口径与写入时一致）。"""
    return w0 * math.exp(-decay_rate * max(0.0, delta_days))


def current_weight(
    *,
    w0: float,
    decay_rate: float,
    last_seen_at: datetime,
    now: datetime | None = None,
) -> float:
    """一条记忆此刻的权重：把「取时间差」与「按 λ 衰减」合成一步。

    读库时统一走它，避免每个调用点各写一遍 `datetime.now()`（那会让单测无法注入时间）。
    """
    return weight_from_rate(w0, days_since(last_seen_at, now), decay_rate)


def is_archived(
    weight: float,
    *,
    expires_at: datetime | None = None,
    now: datetime | None = None,
    threshold: float = ARCHIVE_THRESHOLD,
) -> bool:
    """是否应当归档：**权重过低**或**已过期**，二者满足其一即可。"""
    if weight < threshold:
        return True
    if expires_at is not None:
        reference = now or datetime.now(UTC)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        if reference >= expires_at:
            return True
    return False
