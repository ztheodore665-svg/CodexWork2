"""层间数据契约（与 ORM 模型严格分离）。

这些对象是 services 与 agent 之间的「接口」，将来 HTTP 层也复用同一套模型的 out 结构体，
避免同一份数据在不同层出现多份定义。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentRef(BaseModel):
    """文档稳定标识：`source_type + source_id + chunk_index`。

    为什么不用自增 id：评测集需要长期稳定的引用目标，而重建库会让 id 变化
    （设计文档 §4.5）。frozen=True 使其可哈希，能直接放进 set 做命中判断。
    """

    model_config = ConfigDict(frozen=True)

    source_type: str = Field(description="trip / place / photo / note")
    source_id: str
    chunk_index: int = 0

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.source_type, self.source_id, self.chunk_index)

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.source_type}/{self.source_id}#{self.chunk_index}"


class SearchHit(BaseModel):
    """一条检索命中结果。score 越大越相似（余弦相似度，取值 -1~1）。"""

    document_id: uuid.UUID | None = None
    ref: DocumentRef
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


class DocumentUpsert(BaseModel):
    """写入 / 覆盖一条 chunk。"""

    ref: DocumentRef
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_count: int = 0
    char_len: int = 0
