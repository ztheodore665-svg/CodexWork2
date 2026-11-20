"""Memory 的层间契约（阶段 4 W4-1）。

## 为什么契约放在 schemas 而不是 agent/memory 下

依赖方向铁律是 `agent → nodes → tools → services → models`（PROJECT.md §二、[4]）。
记忆既要被 agent 层（注入上下文）使用，也要被 services 层（持久化）实现，
如果契约定义在 `app/agent/memory/` 下，services 就必须反向 import agent，直接违规。

放在 `app/schemas/` 之后两边都向下依赖，方向就顺了 —— 这和
`app/schemas/document.py` 承担的角色完全一致。

## 三层记忆的口径

| 层 | 常量 | 内容 | 默认半衰期 |
| --- | --- | --- | --- |
| 短期 | `short_term` | 当前会话 / 当前行程上下文（滑动窗口） | 1 天 |
| 情景 | `episodic` | 某次旅行的事件摘要 | 30 天 |
| 语义 | `semantic` | 用户偏好、长期兴趣 | 90 天（设计文档 §7.5） |
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

#: 短期：当前会话与当前行程上下文
LAYER_SHORT_TERM = "short_term"
#: 情景：某次旅行 / 某个 Trip 的事件摘要
LAYER_EPISODIC = "episodic"
#: 语义：用户偏好与长期兴趣
LAYER_SEMANTIC = "semantic"

MEMORY_LAYERS: tuple[str, ...] = (LAYER_SHORT_TERM, LAYER_EPISODIC, LAYER_SEMANTIC)

#: 各层默认半衰期（天）。语义层用 90 天，见设计文档 §7.5「默认 half_life_days = 30（偏好类 90）」
DEFAULT_HALF_LIFE_DAYS: dict[str, float] = {
    LAYER_SHORT_TERM: 1.0,
    LAYER_EPISODIC: 30.0,
    LAYER_SEMANTIC: 90.0,
}

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


class MemorySourceRef(BaseModel):
    """一条记忆的来源（`MemorySource` 表的层间形态）。

    设计文档 §4.1 特意把它做成**关联表而不是数组**：数组无法建外键，
    也就无法反查"这条记忆是哪张照片支撑的"，而拒答红线要求每条结论都能追到源。
    """

    model_config = ConfigDict(frozen=True)

    source_type: str = Field(description="trip / place / photo / note / session")
    source_id: str

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.source_type}/{self.source_id}"


class MemoryDraft(BaseModel):
    """写入一条记忆的请求体（还没有 id 与时间戳）。"""

    layer: str = Field(description="short_term / episodic / semantic")
    key: str = Field(description="记忆的稳定键，同键覆盖（如 pref.crowd）")
    value: str = Field(description="记忆内容")
    #: 初始权重 w0。冲突高、来源可靠的记忆可以调高
    weight: float = 1.0
    #: 置信度。< 0.6 时按设计文档 §7.5 应当"向用户确认"而不是直接当事实使用
    confidence: float = 1.0
    #: 半衰期（天）。None 表示按层取默认
    half_life_days: float | None = None
    expires_at: datetime | None = None
    sources: list[MemorySourceRef] = Field(default_factory=list)
    #: 是否需要人工确认（置信度低时自动置 True）
    needs_confirm: bool = False


class MemoryRecord(MemoryDraft):
    """库里的一条记忆（含 id 与时间戳，以及**实时算出的** weight）。

    `weight` 为什么每次读都重算而不是落库：
    衰减是时间的函数，落库就变成"写入那一刻的快照"，第二天读出来还是老值。
    让它在读取时按 `decay_weight()` 现算，归档判定才不会失真。
    """

    id: uuid.UUID
    user_id: uuid.UUID
    status: str = STATUS_ACTIVE
    last_seen_at: datetime
    created_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE


@dataclass
class MemoryContext:
    """即将注入 prompt 的记忆（设计文档 §7.4 的前两个区块）。

    拆成两块而不是一整坨，是因为**衰减策略与展示位置都不同**：
    偏好（语义层）半衰期 90 天、放在最前面让模型优先遵守；
    行程上下文（短期 + 情景层）半衰期 1~30 天、紧跟其后做场景限定。
    """

    #: `[用户偏好]`：语义层（长期偏好）
    preferences: list[str] = field(default_factory=list)
    #: `[当前行程上下文]`：短期 + 情景层
    trip_context: list[str] = field(default_factory=list)
    #: 本次实际用上的记忆 id（供调用方 touch，刷新 last_seen_at）
    used_ids: list[uuid.UUID] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.preferences and not self.trip_context


@runtime_checkable
class MemoryStorePort(Protocol):
    """记忆持久化端口。

    agent 层只依赖这个 Protocol，services 层提供实现 —— 于是单测可以用内存实现，
    真实运行用 SQLAlchemy 实现，两边的**行为差异**由 `tests/test_memory_service.py`
    与 `tests/test_document_service_sql.py` 一类的用例对齐。
    """

    async def list_memories(
        self,
        user_id: uuid.UUID,
        *,
        layers: tuple[str, ...] | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[MemoryRecord]: ...

    async def upsert_memory(self, user_id: uuid.UUID, draft: MemoryDraft) -> MemoryRecord: ...

    async def find_by_key(
        self, user_id: uuid.UUID, layer: str, key: str
    ) -> MemoryRecord | None: ...

    async def touch_memories(self, ids: list[uuid.UUID]) -> int: ...

    async def archive_memories(self, ids: list[uuid.UUID]) -> int: ...
