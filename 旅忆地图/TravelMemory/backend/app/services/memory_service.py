"""记忆的**数据库实现**（`MemoryStorePort` 的 SQLAlchemy 版）。

放在 services 层而不是 agent 层，是为了守住依赖方向
`agent → nodes → tools → services → models`：契约在 `app/schemas/memory.py`（底层），
agent 依赖契约、services 实现契约，谁也不需要反向 import 谁。

## 两个必须注意的点

1. **upsert 走 `ON CONFLICT`**：记忆写入是"每轮都可能发生"的高频操作，
   先 `SELECT` 再 `INSERT/UPDATE` 既有竞态又多一次往返。唯一约束
   `uq_memory_item_user_type_key` 是它能成立的前提。
2. **来源行先删后插**：`MemorySource` 是关联表，覆盖旧偏好时旧的来源要换掉，
   否则会残留上一次的记忆来源 —— 而来源错了，引用率 100% 就是空话。
"""

from __future__ import annotations

import typing
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.memory import MemoryItem, MemorySource
from app.schemas.memory import (
    DEFAULT_HALF_LIFE_DAYS,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    MemoryDraft,
    MemoryRecord,
    MemorySourceRef,
)
from app.utils.decay import half_life_to_lambda, lambda_to_half_life

logger = get_logger(__name__)


def _rowcount(result: object) -> int:
    """取 DML 影响行数（与 `services/document_service.py` 同一套写法）。

    `session.execute(update(...))` 的静态类型是 `Result`，运行时才是带 `rowcount`
    的 `CursorResult`。用 `typing.cast` 表达"静态类型比运行时窄"这个事实。
    """
    return int(typing.cast(CursorResult[Any], result).rowcount or 0)


def _to_record(row: MemoryItem, sources: list[MemorySourceRef]) -> MemoryRecord:
    """ORM 行 → 层间契约对象（services 对外只出契约，不外泄 ORM 实例）。"""
    return MemoryRecord(
        id=row.id,
        user_id=row.user_id,
        layer=row.type,
        key=row.key,
        value=row.value,
        weight=row.weight0,
        confidence=row.confidence,
        half_life_days=_half_life_of(row),
        expires_at=row.expires_at,
        sources=sources,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        status=row.status,
    )


def _half_life_of(row: MemoryItem) -> float:
    """λ → 半衰期。老行没有 λ 时回落到该层的默认值。"""
    if row.decay_rate and row.decay_rate > 0:
        return lambda_to_half_life(row.decay_rate)
    return DEFAULT_HALF_LIFE_DAYS.get(row.type, 30.0)


class SqlMemoryStore:
    """`MemoryStorePort` 的真实实现。注入 AsyncSession，事务由调用方管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- 读
    async def list_memories(
        self,
        user_id: uuid.UUID,
        *,
        layers: tuple[str, ...] | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        stmt = select(MemoryItem).where(MemoryItem.user_id == user_id)
        if layers is not None:
            stmt = stmt.where(MemoryItem.type.in_(list(layers)))
        if not include_archived:
            stmt = stmt.where(MemoryItem.status == STATUS_ACTIVE)
        stmt = stmt.order_by(MemoryItem.last_seen_at.desc()).limit(limit)

        rows = list((await self.session.execute(stmt)).scalars().all())
        if not rows:
            return []
        return [_to_record(row, await self._sources_of(row.id)) for row in rows]

    async def find_by_key(self, user_id: uuid.UUID, layer: str, key: str) -> MemoryRecord | None:
        stmt = select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.type == layer,
            MemoryItem.key == key,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_record(row, await self._sources_of(row.id))

    # ---------------------------------------------------------------- 写
    async def upsert_memory(self, user_id: uuid.UUID, draft: MemoryDraft) -> MemoryRecord:
        half_life = draft.half_life_days or DEFAULT_HALF_LIFE_DAYS.get(draft.layer, 30.0)
        now = datetime.now(UTC)
        values = {
            "user_id": user_id,
            "type": draft.layer,
            "key": draft.key,
            "value": draft.value,
            "weight0": draft.weight,
            "confidence": draft.confidence,
            "decay_rate": half_life_to_lambda(half_life),
            "last_seen_at": now,
            "expires_at": draft.expires_at,
            # 重新说一次偏好 = 这条记忆重新生效，不该还停在归档态
            "status": STATUS_ACTIVE,
        }
        insert_stmt = pg_insert(MemoryItem).values(**values)
        stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_memory_item_user_type_key",
            set_={**{k: v for k, v in values.items() if k != "user_id"}, "updated_at": now},
        ).returning(MemoryItem)
        row = (await self.session.execute(stmt)).scalar_one()
        await self.session.flush()

        if draft.sources:
            # 来源整体替换：旧来源不清理会残留上一次的证据，溯源就失真了
            await self.session.execute(delete(MemorySource).where(MemorySource.memory_id == row.id))
            await self.session.execute(
                pg_insert(MemorySource),
                [
                    {
                        "memory_id": row.id,
                        "source_type": source.source_type,
                        "source_id": source.source_id,
                    }
                    for source in draft.sources
                ],
            )
            await self.session.flush()

        return _to_record(row, list(draft.sources))

    async def touch_memories(self, ids: list[uuid.UUID]) -> int:
        if not ids:
            return 0
        now = datetime.now(UTC)
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id.in_(ids))
            .values(last_seen_at=now, updated_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return _rowcount(result)

    async def archive_memories(self, ids: list[uuid.UUID]) -> int:
        if not ids:
            return 0
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id.in_(ids))
            .values(status=STATUS_ARCHIVED, updated_at=datetime.now(UTC))
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return _rowcount(result)

    # ---------------------------------------------------------------- 内部
    async def _sources_of(self, memory_id: uuid.UUID) -> list[MemorySourceRef]:
        stmt = select(MemorySource).where(MemorySource.memory_id == memory_id)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [
            MemorySourceRef(source_type=row.source_type, source_id=row.source_id) for row in rows
        ]


__all__ = ["SqlMemoryStore"]
