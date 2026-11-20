"""批处理框架测试（W1-1 的出口条件：同一任务重复执行不重复烧 token）。

需要真实 PostgreSQL（有外键与事务语义），没有数据库时自动跳过。
测试用的照片 platform 带随机前缀，跑完删除，不会污染演示数据。
"""

from __future__ import annotations

import uuid

import pytest
from conftest import db_reachable
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.jobs.base import BaseJob
from app.models.jobs import JOB_CAPTION, JOB_DONE, JOB_FAILED, IngestJob
from app.models.photo import AI_PENDING, Photo

pytestmark = pytest.mark.db

_PLATFORM = f"pytest-job-{uuid.uuid4().hex[:8]}"


class _DummyJob(BaseJob):
    """一个可控的假任务：可以设定"每条花多少钱""是否抛错"。"""

    job_type = JOB_CAPTION

    def __init__(self, session, *, cost: float = 1.0, fail: bool = False, budget: int = 0):
        settings = get_settings().model_copy(update={"job_max_cost_cents": budget})
        super().__init__(session, settings)
        self.ai_model = "dummy-model@v1"
        self.cost = cost
        self.fail = fail
        self.calls: list[uuid.UUID] = []

    async def candidate_photo_ids(self, *, limit: int, force: bool) -> list[uuid.UUID]:
        stmt = select(Photo.id).where(Photo.platform == _PLATFORM).order_by(Photo.created_at)
        if not force:
            from app.models.photo import AI_DONE as DONE

            stmt = stmt.where(Photo.ai_status != DONE)
        return list((await self.session.execute(stmt.limit(limit))).scalars())

    async def process_one(self, photo_id: uuid.UUID) -> float:
        self.calls.append(photo_id)
        if self.fail:
            raise RuntimeError("模拟失败")
        return self.cost


@pytest.fixture
async def photos():
    """插入 3 张测试照片，用完删除（级联删除 IngestJob）。"""
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    async with SessionFactory() as session:
        from app.services.user_service import get_or_create_demo_user

        user = await get_or_create_demo_user(session)
        rows = [
            Photo(
                user_id=user.id,
                platform=_PLATFORM,
                asset_id=f"{_PLATFORM}-{i}",
                ai_status=AI_PENDING,
            )
            for i in range(3)
        ]
        session.add_all(rows)
        await session.commit()
        ids = [row.id for row in rows]

    yield ids

    async with SessionFactory() as session:
        await session.execute(delete(Photo).where(Photo.platform == _PLATFORM))
        await session.commit()
    await dispose_engine()


async def _job_statuses(session, photo_ids) -> dict[uuid.UUID, str]:
    rows = (
        (await session.execute(select(IngestJob).where(IngestJob.photo_id.in_(photo_ids))))
        .scalars()
        .all()
    )
    return {row.photo_id: row.status for row in rows}


async def test_second_run_does_not_repeat_work(photos):
    """★ W1-1 出口条件：重复执行不重复烧 token。"""
    async with SessionFactory() as session:
        first = _DummyJob(session, cost=1.0)
        report1 = await first.run(limit=10)
        await session.commit()
        assert report1.processed == 3
        assert report1.succeeded == 3

        second = _DummyJob(session, cost=1.0)
        report2 = await second.run(limit=10)
        await session.commit()

    # 关键断言：第二次一条都没执行、一分钱没花
    assert report2.processed == 0
    assert report2.cost_cents == 0.0
    assert len(second.calls) == 0


async def test_failed_task_is_retried_on_next_run(photos):
    """失败的任务下次跑批会被重新挑出来，成功的不受影响。"""
    async with SessionFactory() as session:
        job = _DummyJob(session, fail=True)
        await job.run(limit=10)
        await session.commit()
        statuses = await _job_statuses(session, photos)
        assert set(statuses.values()) == {JOB_FAILED}

        retry = _DummyJob(session, cost=0.5)
        report = await retry.run(limit=10)
        await session.commit()
        statuses = await _job_statuses(session, photos)

    assert report.processed == 3, "failed 的任务应在下次跑批被重试"
    assert set(statuses.values()) == {JOB_DONE}


async def test_cost_cap_stops_the_batch(photos):
    """成本上限触发后必须立刻停下，剩下的留给下次跑批。"""
    async with SessionFactory() as session:
        # 每条 10 分，上限 15 分 → 只能跑 2 条
        job = _DummyJob(session, cost=10.0, budget=15)
        report = await job.run(limit=10)
        await session.commit()

    assert report.succeeded == 2
    assert report.stopped_by_budget is True
    assert report.cost_cents == 20.0


async def test_force_reruns_completed_tasks(photos):
    """--force 用于换模型后全量重建。"""
    async with SessionFactory() as session:
        await _DummyJob(session, cost=1.0).run(limit=10)
        await session.commit()

        forced = _DummyJob(session, cost=1.0)
        report = await forced.run(limit=10, force=True)
        await session.commit()

    assert report.processed == 3, "force 应该把已完成的也重跑一遍"
