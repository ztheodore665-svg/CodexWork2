"""IngestJob —— 离线批处理的**幂等键表**。

它存在的唯一理由：让"重跑一次批处理"不再重复烧钱。

```
unique(photo_id, job_type, ai_model)
```

这个三元组决定了：同一张照片、同一个任务类型、同一个模型版本，只会有一行任务记录。
第二次跑批时看到 `status=done` 就直接跳过——这就是 W1-1 出口条件
「同一任务重复执行不重复烧 token」的实现方式。

换个模型版本（ai_model 变了）会产生**新的一行**，于是旧结果不会被新模型覆盖，
可以直接对比两个模型的效果，也符合设计文档"三个版本化"的要求。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, PrimaryKey, UpdatedAt

#: 任务状态流转：pending → running → done
#:                        ↘ failed（attempts 未超上限时，下次跑批会再次被挑出来）
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_SKIPPED = "skipped"

#: 阶段 1 的两类任务
JOB_CAPTION = "caption"
JOB_INDEX = "index"


class IngestJob(Base):
    __tablename__ = "ingest_job"
    __table_args__ = (
        UniqueConstraint("photo_id", "job_type", "ai_model", name="uq_ingest_job_identity"),
    )

    id: Mapped[PrimaryKey]
    photo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("photo.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: caption / index（后续阶段可扩展 thumb 等）
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 模型版本，构成幂等键的一部分：换模型 = 新任务
    ai_model: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JOB_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    #: 累计成本（分）。配合批次成本上限，防止一次跑批烧穿预算
    cost_cents: Mapped[float] = mapped_column(default=0.0, nullable=False)

    #: 上次成功执行的时间（失败重试时用它判断"是不是卡住了"）
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<IngestJob {self.job_type}/{self.ai_model} {self.status} x{self.attempts}>"
