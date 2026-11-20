"""caption_job 与 index_job 的端到端测试（W1-2 / W1-3）。

需要真实数据库；用独立 platform 前缀，跑完删除，不污染演示数据。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from conftest import db_reachable
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.vision import MockVision
from app.db.session import SessionFactory, dispose_engine
from app.jobs.caption_job import CaptionJob
from app.jobs.index_job import IndexJob
from app.models.document import Document
from app.models.photo import AI_DONE, AI_PENDING, Photo

pytestmark = pytest.mark.db

_PLATFORM = f"pytest-pipeline-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def photos():
    """插 6 张照片：3 张同一天（嘉兴），3 张次日，带地点线索。

    用**独立用户**而不是演示用户：日文档是按 user_id + 日期聚合的，
    共用用户会把演示照片一起算进来，断言就没法写死。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    async with SessionFactory() as session:
        from app.models.user import User

        user = User(name=f"pytest-user-{uuid.uuid4().hex[:8]}")
        session.add(user)
        await session.flush()
        rows = []
        for i in range(6):
            day = 3 if i < 3 else 4
            rows.append(
                Photo(
                    user_id=user.id,
                    platform=_PLATFORM,
                    asset_id=f"{_PLATFORM}-{i}",
                    taken_at_local=datetime(2025, 10, day, 9 + i % 3),
                    lat=30.7443,
                    lng=120.4879,
                    exif_json={"city": "嘉兴", "place": "乌镇"},
                    ai_status=AI_PENDING,
                )
            )
        session.add_all(rows)
        await session.commit()
        ids = [row.id for row in rows]
        user_id = user.id

    yield user_id, ids

    async with SessionFactory() as session:
        await session.execute(delete(Document).where(Document.user_id == user_id))
        await session.execute(delete(Photo).where(Photo.platform == _PLATFORM))
        await session.commit()
    await dispose_engine()


async def test_caption_job_writes_back_and_is_idempotent(photos):
    user_id, photo_ids = photos

    async with SessionFactory() as session:
        job = CaptionJob(session, vision=MockVision(get_settings()), user_id=user_id)
        report = await job.run(limit=10)
        await session.commit()

        loaded = (
            (await session.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()
        )

    assert report.succeeded == 6
    assert all(p.ai_status == AI_DONE for p in loaded)
    assert all(p.caption for p in loaded), "每张照片都必须有 caption"
    assert all(p.ai_tags for p in loaded), "每张照片都必须有 ai_tags"

    async with SessionFactory() as session:
        again = CaptionJob(
            session,
            vision=MockVision(get_settings()),
            user_id=user_id,
        )
        report2 = await again.run(limit=10)
        await session.commit()

    assert report2.processed == 0, "第二次跑批不该重复生成 caption（幂等）"


async def test_index_job_builds_day_documents(photos):
    user_id, photo_ids = photos

    async with SessionFactory() as session:
        await CaptionJob(
            session,
            vision=MockVision(get_settings()),
            user_id=user_id,
        ).run(limit=10)
        await session.commit()

    async with SessionFactory() as session:
        report = await IndexJob(session).run(limit=10)
        await session.commit()

        docs = (
            (
                await session.execute(
                    select(Document).where(
                        Document.user_id == user_id, Document.source_type == "day"
                    )
                )
            )
            .scalars()
            .all()
        )

    assert report.succeeded > 0
    # 6 张照片分布在 10-03 / 10-04 两天 → 应聚成两个日文档
    assert {d.source_id for d in docs} == {"2025-10-03", "2025-10-04"}
    for doc in docs:
        assert doc.metadata_json["photo_count"] == 3
        assert doc.metadata_json["places"] == ["乌镇"]
        assert doc.embedding is not None, "日文档必须带有向量"


async def test_reindex_force_rebuilds(photos):
    """`make reindex` 走的是 --force，必须能全量重建且不产生重复文档。"""
    user_id, photo_ids = photos

    async with SessionFactory() as session:
        await CaptionJob(
            session,
            vision=MockVision(get_settings()),
            user_id=user_id,
        ).run(limit=10)
        await session.commit()

    async def _day_doc_count(session) -> int:
        rows = (
            (
                await session.execute(
                    select(Document).where(
                        Document.user_id == user_id, Document.source_type == "day"
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)

    async with SessionFactory() as session:
        await IndexJob(session).run(limit=10)
        await session.commit()
        before = await _day_doc_count(session)

        forced = await IndexJob(session).run(limit=10, force=True)
        await session.commit()
        after = await _day_doc_count(session)

    assert forced.processed > 0, "--force 必须真的重跑"
    assert after == before == 2, "重建不能产生重复文档（同三元组覆盖，而不是新增行）"
