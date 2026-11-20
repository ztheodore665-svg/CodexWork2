"""Photo —— 数据源表（不是检索单元，检索单元是 Document）。

设计要点（设计文档 §4）：
  - 原图永不上云：库里只存 `platform + asset_id`，展示时通过系统 API 重新取；
    落盘的只有**本地生成的缩略图**（`local_thumb_path`）
  - `unique(platform, asset_id)` 做去重：同一张照片重复导入不会产生两条记录
  - `ai_status` / `ai_model` 记录 caption 与 ai_tags 的生成状态，
    离线批处理（阶段 1 W1-2）靠它挑"还有哪些照片没处理过"
  - `coord_system` 必须显式写：WGS84 存储、GCJ-02 展示（PROJECT.md §二、[2]）

阶段 1 不碰 EXIF 解析（那是 W6-1 导入链路的工作），但表按完整规格建好，
避免阶段 6 再来一次迁移。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base, CreatedAt, PrimaryKey, UpdatedAt

_EMBEDDING_DIM = get_settings().embedding_dim

#: ai_status 取值：待处理 / 处理中 / 成功 / 失败
AI_PENDING = "pending"
AI_RUNNING = "running"
AI_DONE = "done"
AI_FAILED = "failed"


class Photo(Base):
    __tablename__ = "photo"

    id: Mapped[PrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---- 来源与去重 ----
    #: 设备/平台：web / android / ios / demo
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="demo")
    #: 平台侧稳定标识（如 MediaStore 的 asset id、Web 端的文件指纹）
    asset_id: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 内容指纹，用于跨平台的重复照片识别
    hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # ---- 缩略图（原图不入库、不上云）----
    #: 本地生成的缩略图路径。`thumb_url` 已在阶段 16 删除（从未写入过的对象存储占位）。
    local_thumb_path: Mapped[str | None] = mapped_column(Text)

    # ---- 位置 ----
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    #: WGS84 / GCJ-02，存储侧恒为 WGS84
    coord_system: Mapped[str] = mapped_column(String(8), nullable=False, default="WGS84")
    #: exif / inferred / user / none
    location_source: Mapped[str | None] = mapped_column(String(16))
    location_confidence: Mapped[float | None] = mapped_column(Float)

    # ---- 时间与时区 ----
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 拍摄地当地时间（无时区），用于"按天聚合"——同一趟行程的白天边界以当地为准
    taken_at_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    tz_name: Mapped[str | None] = mapped_column(String(64))
    tz_source: Mapped[str | None] = mapped_column(String(16))

    # ---- 元数据与 AI 产物 ----
    exif_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    caption: Mapped[str | None] = mapped_column(Text)
    ai_tags: Mapped[list[str] | None] = mapped_column(JSONB)

    ai_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AI_PENDING, index=True
    )
    ai_model: Mapped[str | None] = mapped_column(String(64))
    ai_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- 向量（阶段 1 不写，留位给后续图文向量）----
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Photo {self.platform}/{self.asset_id} ai={self.ai_status}>"
