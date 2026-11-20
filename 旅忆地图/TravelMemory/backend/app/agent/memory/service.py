"""MemoryService：三层记忆的**读（注入）与写（写回）**（阶段 4 W4-1）。

## 读：不是"把记忆全塞进 prompt"

顺序是「先算衰减权重 → 过滤归档 → 按权重排序 → 截断」。
跳过任何一步都会出问题：

- 不算权重：三年前说过一次"我不吃辣"会和昨天的"我不吃辣"一样重
- 不过滤归档：库越用越大，上下文预算被低权重记忆吃光（这正是 W4-3 压缩要防的）
- 不截断：记忆区块膨胀到比证据还长，模型会被"用户的自我描述"带偏

## 写：三条触发时机（设计文档 §7.5）

| 时机 | 方法 | 落哪一层 |
| --- | --- | --- |
| 用户明确表达偏好 | `write_memory(..., extract=True)` | semantic（半衰期 90 天） |
| 每 8 轮会话 | `summarize_session()` | episodic（30 天） |
| 当前会话上下文 | `write_memory(layer=short_term)` | short_term（1 天） |

`needs_confirm`：置信度 < 0.6 的记忆在注入时会**带上待确认标记**，
而不是直接当事实 —— 设计文档 §7.5 明确要求"置信度 < 0.6 时向用户确认"。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.agent.memory.extract import extract_preference
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.memory import (
    DEFAULT_HALF_LIFE_DAYS,
    LAYER_EPISODIC,
    LAYER_SEMANTIC,
    LAYER_SHORT_TERM,
    STATUS_ACTIVE,
    MemoryContext,
    MemoryDraft,
    MemoryRecord,
    MemorySourceRef,
    MemoryStorePort,
)
from app.utils.decay import ARCHIVE_THRESHOLD, current_weight, half_life_to_lambda, is_archived

logger = get_logger(__name__)

#: 置信度低于此值时，注入上下文需要带「待确认」标记
CONFIRM_CONFIDENCE = 0.6

#: 注入上下文的条数上限（每层）。超过就按权重截断 —— 记忆是调味，不是主菜
MAX_ITEMS_PER_SECTION = 5


class MemoryService:
    """三层记忆的门面。所有读写都经它，便于在 trace 里统一埋点。"""

    def __init__(
        self,
        store: MemoryStorePort,
        *,
        min_weight: float = ARCHIVE_THRESHOLD,
        max_items_per_section: int = MAX_ITEMS_PER_SECTION,
        half_life_days: dict[str, float] | None = None,
        summarize_every: int = 8,
    ) -> None:
        self.store = store
        self.min_weight = min_weight
        self.max_items_per_section = max_items_per_section
        #: 各层半衰期（天）。不传时取 `DEFAULT_HALF_LIFE_DAYS`
        self.half_life_days = dict(half_life_days or DEFAULT_HALF_LIFE_DAYS)
        self.summarize_every = summarize_every

    @classmethod
    def from_settings(cls, store: MemoryStorePort, settings: Settings | None = None):
        """按 `.env` 构造（唯一读取配置的入口，见 PROJECT.md §二、[5]）。"""
        settings = settings or get_settings()
        return cls(
            store,
            min_weight=settings.memory_archive_threshold,
            max_items_per_section=settings.memory_max_items_per_section,
            half_life_days={
                LAYER_SHORT_TERM: settings.memory_half_life_short_term,
                LAYER_EPISODIC: settings.memory_half_life_episodic,
                LAYER_SEMANTIC: settings.memory_half_life_semantic,
            },
            summarize_every=settings.memory_summarize_every,
        )

    # ---------------------------------------------------------------- 写
    async def write_memory(
        self,
        user_id: uuid.UUID,
        *,
        layer: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        weight: float = 1.0,
        half_life_days: float | None = None,
        expires_at: datetime | None = None,
        sources: list[MemorySourceRef] | None = None,
    ) -> MemoryRecord:
        """写回一条记忆（同 `user_id + layer + key` 覆盖）。

        `half_life_days` 不传时按层取默认（短期 1 天 / 情景 30 天 / 语义 90 天）。
        """
        if layer not in self.half_life_days:
            raise ValueError(f"未知的记忆层: {layer!r}（可选 {sorted(self.half_life_days)}）")
        resolved_half_life = (
            float(half_life_days) if half_life_days is not None else self.half_life_days[layer]
        )
        draft = MemoryDraft(
            layer=layer,
            key=key,
            value=value,
            weight=weight,
            confidence=confidence,
            half_life_days=resolved_half_life,
            expires_at=expires_at,
            sources=sources or [],
            needs_confirm=confidence < CONFIRM_CONFIDENCE,
        )
        record = await self.store.upsert_memory(user_id, draft)
        logger.info(
            "memory.write user=%s layer=%s key=%s conf=%.2f half_life=%.0fd",
            user_id,
            layer,
            key,
            confidence,
            resolved_half_life,
        )
        return record

    async def remember_from_text(
        self,
        user_id: uuid.UUID,
        text: str,
        *,
        sources: list[MemorySourceRef] | None = None,
    ) -> MemoryRecord | None:
        """从用户原话里抽偏好并写回（抽不到就什么都不做）。

        这是 `write_memory` 的**语义层便捷入口**，对应设计文档"用户明确表达偏好时抽取"。
        """
        extracted = extract_preference(text)
        if extracted is None:
            return None
        return await self.write_memory(
            user_id,
            layer=LAYER_SEMANTIC,
            key=extracted.key,
            value=extracted.value,
            confidence=0.9,
            sources=sources,
        )

    async def write_session_summary(
        self,
        user_id: uuid.UUID,
        *,
        session_id: str,
        turn_count: int,
        summary: str,
    ) -> MemoryRecord | None:
        """到点了才写会话摘要（每 8 轮一次），不到点返回 None。"""
        from app.agent.memory.extract import should_summarize_session

        if not should_summarize_session(turn_count, every=self.summarize_every):
            return None
        return await self.write_memory(
            user_id,
            layer=LAYER_EPISODIC,
            key=f"session.{session_id}.{turn_count}",
            value=summary,
            confidence=0.8,
            sources=[MemorySourceRef(source_type="session", source_id=session_id)],
        )

    # ---------------------------------------------------------------- 读
    async def build_context(
        self,
        user_id: uuid.UUID,
        *,
        now: datetime | None = None,
        include_short_term: bool = True,
    ) -> MemoryContext:
        """组装即将注入 prompt 的记忆区块（**顺序固定**：偏好在前，行程上下文在后）。"""
        layers = (
            tuple(DEFAULT_HALF_LIFE_DAYS)
            if include_short_term
            else (
                LAYER_EPISODIC,
                LAYER_SEMANTIC,
            )
        )
        records = await self.store.list_memories(user_id, layers=layers, limit=200)

        scored: list[tuple[float, MemoryRecord]] = []
        to_archive: list[uuid.UUID] = []
        for record in records:
            weight = current_weight(
                w0=record.weight,
                decay_rate=half_life_to_lambda(
                    record.half_life_days or self.half_life_days.get(record.layer, 30.0)
                ),
                last_seen_at=record.last_seen_at,
                now=now,
            )
            if is_archived(
                weight, expires_at=record.expires_at, now=now, threshold=self.min_weight
            ):
                # 到期即归档（不物理删除）。这次不进上下文，但数据仍在库里
                to_archive.append(record.id)
                continue
            scored.append((weight * record.confidence, record))

        if to_archive:
            await self.store.archive_memories(to_archive)

        scored.sort(key=lambda pair: pair[0], reverse=True)
        context = MemoryContext()
        for score, record in scored:
            line = self._render(record)
            if record.layer == LAYER_SEMANTIC:
                if len(context.preferences) < self.max_items_per_section:
                    context.preferences.append(line)
                    context.used_ids.append(record.id)
            elif len(context.trip_context) < self.max_items_per_section:
                context.trip_context.append(line)
                context.used_ids.append(record.id)
            _ = score  # 排序已用；保留变量是为了调试时能直接打断点看分数
        return context

    async def touch(self, memory_ids: list[uuid.UUID]) -> int:
        """记忆被真正用上了 → 刷新 `last_seen_at`（衰减重新计时）。"""
        if not memory_ids:
            return 0
        return await self.store.touch_memories(memory_ids)

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _render(record: MemoryRecord) -> str:
        """把一条记忆渲染成 prompt 里的一行。

        带「（待确认）」标记是刻意的：置信度低的偏好如果和事实句长得一模一样，
        模型会当成铁律去执行，用户却从没确认过 —— 宁可啰嗦，不可越权。
        """
        suffix = (
            "（待确认）" if record.needs_confirm or record.confidence < CONFIRM_CONFIDENCE else ""
        )
        return f"- {record.value}{suffix}"


#: 便于外部类型标注
__all__ = [
    "ARCHIVE_THRESHOLD",
    "CONFIRM_CONFIDENCE",
    "LAYER_EPISODIC",
    "LAYER_SEMANTIC",
    "LAYER_SHORT_TERM",
    "STATUS_ACTIVE",
    "MemoryService",
    "utc_now",
]


def utc_now() -> datetime:
    """统一的时间入口（测试要注入时间时只需替换本函数）。"""
    return datetime.now(UTC)
