"""index_job（W1-3）：把照片聚合出的日文档切成 chunk、向量化、写入 Document。

它是阶段 0 建好的索引链路（chunker → embedder → Document）的**上游驱动**：
阶段 0 用演示语料手工灌数据，阶段 1 之后由这个任务自动把照片变成可检索文本。

幂等与重建：
  - 普通跑批：只对"还没有入索引 / 上次失败"的照片所属的那一天重建
  - `--force`：`make reindex` 走的就是它，全量重建（换 embedding 模型后必须跑）

用法：
```bash
python -m app.jobs.index_job --limit 500
python -m app.jobs.index_job --force     # = make reindex，全量重建
```
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.rag.indexer import DocumentIndexer
from app.core.config import Settings
from app.core.embedding import BaseEmbedder, build_embedder
from app.jobs.base import BaseJob, run_job_cli
from app.models.jobs import JOB_INDEX
from app.models.photo import AI_DONE, Photo
from app.services.day_digest import build_day_digest


class IndexJob(BaseJob):
    """以「天」为单位重建可检索文档。"""

    job_type = JOB_INDEX

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        embedder: BaseEmbedder | None = None,
    ) -> None:
        super().__init__(session, settings)
        self.embedder = embedder or build_embedder(self.settings)
        # embedding 模型版本参与幂等键：换模型后必须 make reindex 才会重跑
        self.ai_model = self.embedder.model_version
        self._built_days: set[tuple[uuid.UUID, str]] = set()

    async def candidate_photo_ids(self, *, limit: int, force: bool) -> list[uuid.UUID]:
        """挑候选：**已经有 caption 的**照片才值得入索引。

        force=True 时忽略"已入索引"状态（全量重建）。
        """
        stmt = select(Photo.id).where(Photo.deleted_at.is_(None), Photo.ai_status == AI_DONE)
        if not force:
            # 还没做过 index 任务的照片：用 NOT EXISTS 子查询排除已完成的行
            from app.models.jobs import JOB_DONE, IngestJob

            done_ids = (
                select(IngestJob.photo_id)
                .where(
                    IngestJob.job_type == JOB_INDEX,
                    IngestJob.ai_model == self.ai_model,
                    IngestJob.status == JOB_DONE,
                )
                .scalar_subquery()
            )
            stmt = stmt.where(Photo.id.not_in(done_ids))
        stmt = stmt.order_by(Photo.taken_at_local, Photo.created_at).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def process_one(self, photo_id: uuid.UUID) -> float:
        """重建这张照片所在的那一天的日文档。

        同一天的多张照片只重建一次（用 _built_days 去重），
        否则这一天的 embedding 会被重复烧 N 次。
        """
        photo = await self.session.get(Photo, photo_id)
        if photo is None:
            raise ValueError(f"照片不存在：{photo_id}")

        day_key = _day_key(photo)
        cache_key = (photo.user_id, day_key)
        if cache_key in self._built_days:
            return 0.0  # 这一天本轮已经重建过

        # 取出这一天的全部照片（含这张）
        day_photos = await self._photos_of_day(photo.user_id, day_key)
        useable = [p for p in day_photos if p.caption]
        if not useable:
            raise ValueError(f"{day_key} 没有任何带 caption 的照片，跳过索引")

        digest = build_day_digest(day_key, useable)
        indexer = DocumentIndexer(self.session, self.embedder)
        result = await indexer.index_source(
            user_id=photo.user_id,
            source_type="day",
            source_id=day_key,
            text=digest.content,
            metadata=digest.metadata,
        )
        if result.chunks == 0:
            raise ValueError(f"{day_key} 生成的文档为空")

        self._built_days.add(cache_key)
        # embedding 成本极低且由外部服务计价，这里按 0 计；
        # 真正需要盯的是"跑了多少条"，由 IngestJob 行数体现。
        return 0.0

    async def _photos_of_day(self, user_id: uuid.UUID, day_key: str) -> list[Photo]:
        """取出某用户某一天的全部照片（unknown 组单独处理）。"""
        stmt = select(Photo).where(
            Photo.user_id == user_id,
            Photo.deleted_at.is_(None),
            Photo.ai_status == AI_DONE,
        )
        photos = list((await self.session.execute(stmt)).scalars().all())
        if day_key == "unknown":
            return [p for p in photos if p.taken_at_local is None]
        target = date.fromisoformat(day_key)
        return [
            p for p in photos if p.taken_at_local is not None and p.taken_at_local.date() == target
        ]


def _day_key(photo: Photo) -> str:
    """这张照片属于哪一天（没有当地时间时归 unknown）。"""
    if photo.taken_at_local is None:
        return "unknown"
    return photo.taken_at_local.date().isoformat()


def main() -> None:
    run_job_cli(
        lambda session: IndexJob(session),
        "按天聚合照片 → chunk → embedding → 写 Document（--force 即 make reindex）",
    )


if __name__ == "__main__":
    main()
