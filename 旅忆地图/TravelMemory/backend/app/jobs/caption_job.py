"""caption_job（W1-2）：给照片生成 caption + ai_tags 并写回 Photo。

**为什么必须先做这件事**：照片是二进制，检索系统只能检索文本。
不让模型把"这张照片拍了什么"写出来，阶段 0 建好的向量检索对照片就是无效的。

本文件只写业务（挑候选、调视觉模型、写回字段），
重试 / 幂等 / 成本闸 / 状态回写全部由 `jobs/base.py` 的 BaseJob 负责。

用法：
```bash
python -m app.jobs.caption_job --limit 200        # 幂等增量：只处理没做过/上次失败的
python -m app.jobs.caption_job --force            # 换模型后全量重跑
```
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.vision import BaseVision, VisionInput, build_vision
from app.jobs.base import BaseJob, run_job_cli
from app.models.jobs import JOB_CAPTION
from app.models.photo import AI_DONE, Photo


class CaptionJob(BaseJob):
    """把「没有文本的照片」变成「有 caption 与标签的照片」。"""

    job_type = JOB_CAPTION

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        vision: BaseVision | None = None,
        *,
        user_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(session, settings)
        self.vision = vision or build_vision(self.settings)
        # 模型版本参与幂等键：换模型 = 新的任务行 = 会重跑，旧结果仍可对比
        self.ai_model = self.vision.model_version
        #: 限定只跑某个用户的照片。`None` = 全库（生产默认，也兼容既有调用方）。
        #:
        #: 为什么需要它：候选选取原本是全库全用户的，于是"这一批处理了几张"
        #: 会随库里**别人**的 pending 照片数量浮动。测试因此没法写死计数
        #: （演示库一有 pending 就红），而按用户跑批本身也是合理需求。
        self.user_id = user_id

    async def candidate_photo_ids(self, *, limit: int, force: bool) -> list[uuid.UUID]:
        """挑候选：未删除、且（还没成功生成过 caption）的照片。

        force=True 时连 done 的也挑出来（换模型 / 想重生成时用）。
        构造时传了 `user_id` 就只挑那个用户的。
        """
        stmt = select(Photo.id).where(Photo.deleted_at.is_(None))
        if self.user_id is not None:
            stmt = stmt.where(Photo.user_id == self.user_id)
        if not force:
            # 只要不是 done 的都算候选：pending（没跑过）与 failed（上次失败，等重试）
            stmt = stmt.where(Photo.ai_status != AI_DONE)
        stmt = stmt.order_by(Photo.created_at).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def process_one(self, photo_id: uuid.UUID) -> float:
        """处理一张照片：调视觉模型 → 写回 Photo → 返回本次花费（分）。"""
        photo = await self.session.get(Photo, photo_id)
        if photo is None:
            raise ValueError(f"照片不存在：{photo_id}")

        exif = photo.exif_json or {}
        payload = VisionInput(
            thumb_path=photo.local_thumb_path,
            taken_at_text=_format_local(photo.taken_at_local),
            place_hint=str(exif.get("place") or ""),
            city=str(exif.get("city") or ""),
            exif=exif,
        )
        description = await self.vision.describe(payload)
        if not description.caption:
            raise ValueError("视觉模型没有返回 caption")

        # ---- 写回 Photo：ai_status 是下一环节（索引）挑候选的依据 ----
        photo.caption = description.caption
        photo.ai_tags = description.ai_tags
        photo.ai_status = AI_DONE
        photo.ai_model = self.ai_model
        photo.ai_updated_at = datetime.now(UTC)
        return float(description.cost_cents)


def _format_local(value: datetime | None) -> str:
    """把当地时间格式化成给模型看的线索字符串。"""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def main() -> None:
    run_job_cli(
        lambda session: CaptionJob(session),
        "给照片生成 caption + ai_tags（幂等，--limit 控制单次成本）",
    )


if __name__ == "__main__":
    main()
