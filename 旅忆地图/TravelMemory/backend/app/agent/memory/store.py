"""记忆存储的**内存实现**（阶段 4 W4-1）。

它的存在意义与 `tests/fakes.py` 里的 `InMemoryVectorSearch` 完全一致：
让 agent 层的行为测试**不依赖数据库**就能跑。真实实现见
`app/services/memory_service.py`，两者实现同一个 `MemoryStorePort`。

放在 `app/agent/memory/` 而不是 `tests/` 下，是因为 CLI 的离线模式
（`LLM_PROVIDER=mock`）也需要它 —— 没有数据库时管道仍应能完整跑通。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.schemas.memory import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    MemoryDraft,
    MemoryRecord,
    MemorySourceRef,
)


class InMemoryMemoryStore:
    """进程内记忆库。

    唯一键口径与数据库一致：`(user_id, layer, key)`。同键写入走 upsert，
    新值覆盖旧值但**保留来源**（设计文档 §7.5：新偏好覆盖旧偏好时通过
    MemorySource 保留来源）。
    """

    def __init__(self) -> None:
        self.records: dict[uuid.UUID, MemoryRecord] = {}
        self.sources: dict[uuid.UUID, list[MemorySourceRef]] = {}

    # ---------------------------------------------------------------- 读
    async def list_memories(
        self,
        user_id: uuid.UUID,
        *,
        layers: tuple[str, ...] | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        rows = [
            record
            for record in self.records.values()
            if record.user_id == user_id
            and (layers is None or record.layer in layers)
            and (include_archived or record.status == STATUS_ACTIVE)
        ]
        rows.sort(key=lambda r: (r.layer, r.key))
        return rows[:limit]

    async def find_by_key(self, user_id: uuid.UUID, layer: str, key: str) -> MemoryRecord | None:
        for record in self.records.values():
            if record.user_id == user_id and record.layer == layer and record.key == key:
                return record
        return None

    # ---------------------------------------------------------------- 写
    async def upsert_memory(self, user_id: uuid.UUID, draft: MemoryDraft) -> MemoryRecord:
        now = datetime.now(UTC)
        existing = await self.find_by_key(user_id, draft.layer, draft.key)

        if existing is not None:
            updated = existing.model_copy(
                update={
                    "value": draft.value,
                    "weight": draft.weight,
                    "confidence": draft.confidence,
                    "half_life_days": draft.half_life_days,
                    "expires_at": draft.expires_at,
                    "needs_confirm": draft.needs_confirm,
                    "last_seen_at": now,
                    # 覆盖不等于复活失败：重新说一次偏好，这条记忆应当重新计权
                    "status": STATUS_ACTIVE,
                }
            )
            self.records[existing.id] = updated
            if draft.sources:
                self.sources[existing.id] = list(draft.sources)
            return updated

        record = MemoryRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            status=STATUS_ACTIVE,
            last_seen_at=now,
            created_at=now,
            **draft.model_dump(),
        )
        self.records[record.id] = record
        self.sources[record.id] = list(draft.sources)
        return record

    async def touch_memories(self, ids: list[uuid.UUID]) -> int:
        """刷新 `last_seen_at`（记忆被真正用上了）。返回实际刷新的条数。"""
        now = datetime.now(UTC)
        touched = 0
        for memory_id in ids:
            record = self.records.get(memory_id)
            if record is None:
                continue
            self.records[memory_id] = record.model_copy(update={"last_seen_at": now})
            touched += 1
        return touched

    async def archive_memories(self, ids: list[uuid.UUID]) -> int:
        archived = 0
        for memory_id in ids:
            record = self.records.get(memory_id)
            if record is None or record.status == STATUS_ARCHIVED:
                continue
            self.records[memory_id] = record.model_copy(update={"status": STATUS_ARCHIVED})
            archived += 1
        return archived

    # ---------------------------------------------------------------- 辅助
    def sources_of(self, memory_id: uuid.UUID) -> list[MemorySourceRef]:
        return list(self.sources.get(memory_id, []))
