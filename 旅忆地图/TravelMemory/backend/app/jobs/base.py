"""离线批处理框架（W1-1）。

这个文件解决一个具体问题：**批处理重跑一次，不能把钱再烧一遍**。

## 为什么必须专门做框架

在线问答一次只调一次模型，失败重试影响不大。离线批处理一次要跑上千张照片，
如果中途挂掉、或者跑完发现效果不对想换模型再跑一次，天真实现会把 token 全部重烧。
所以批处理必须有三个机制：

```
1. 幂等键   unique(photo_id, job_type, ai_model) → 做过就是做过，重跑直接跳过
2. 可重试   status=failed 且 attempts < 上限 → 下次跑批会被重新挑出来（只重试失败的）
3. 成本闸   本次跑批累计 cost_cents > 上限 → 立刻停下，剩下的下次再说
```

## 一次跑批的状态流转

```
         ┌──────────── 还没有 IngestJob 行 ────────────┐
photo ──▶│ 建行：status=pending, attempts=0            │
         └────────────────────────────────────────────┘
                              │ 认领（claim）
                              ▼
                     status=running, attempts+1
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
            执行成功                    执行失败
         status=done                 status=failed
       cost_cents += 花费            last_error=原因
                                （attempts 未超上限 → 下次跑批会重试）

已 done 的行不会被再挑出来，除非传 --force（force 会把它重置回 pending）。
```

## 并发安全

claim 用 `SELECT ... FOR UPDATE SKIP LOCKED`：多个 worker 同时跑批也不会抢到同一行。
单进程时这个语法无害，多进程时它正是防止重复烧钱的关键。
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.jobs import JOB_DONE, JOB_FAILED, JOB_PENDING, JOB_RUNNING, IngestJob

logger = get_logger(__name__)


@dataclass
class JobRunReport:
    """一次跑批的结果摘要（打印给用户看，也是测试断言的对象）。"""

    job_type: str
    ai_model: str
    #: 本次真正执行的条数
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    #: 因"已经做过"或"重试次数用尽"而未执行的条数 —— 幂等生效的直接证据
    skipped: int = 0
    cost_cents: float = 0.0
    latency_ms: int = 0
    #: 是否因成本上限提前中止
    stopped_by_budget: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.job_type}({self.ai_model})：执行 {self.processed} 条，"
            f"成功 {self.succeeded}，失败 {self.failed}，跳过 {self.skipped}，"
            f"成本 {self.cost_cents:.3f} 分，用时 {self.latency_ms}ms"
        )


class BaseJob(ABC):
    """所有离线任务的基类。

    子类只需实现三件事：声明 `job_type` / `ai_model`、挑出候选照片、处理单条任务。
    编排（建行 / 认领 / 重试 / 成本闸 / 状态回写）全部由基类负责——
    这就是框架的价值：W1-2 与 W1-3 只写业务。
    """

    #: 任务类型标识（构成幂等键）
    job_type: str
    #: 模型版本（构成幂等键；换模型 = 新的一批任务）
    ai_model: str

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- 子类实现
    @abstractmethod
    async def candidate_photo_ids(self, *, limit: int, force: bool) -> list[uuid.UUID]:
        """挑出"需要处理"的照片 id。force=True 时忽略"已完成"状态。"""

    @abstractmethod
    async def process_one(self, photo_id: uuid.UUID) -> float:
        """处理一条任务，返回本次花费（分）。失败请抛异常，由框架记 failed。"""

    # ---------------------------------------------------------------- 编排
    async def run(self, *, limit: int = 200, force: bool = False) -> JobRunReport:
        """跑一批。幂等：重复执行不会重复烧钱。"""
        started = time.perf_counter()
        report = JobRunReport(job_type=self.job_type, ai_model=self.ai_model)

        photo_ids = await self.candidate_photo_ids(limit=limit, force=force)
        if not photo_ids:
            report.latency_ms = int((time.perf_counter() - started) * 1000)
            return report

        # 1) 补齐任务行：没有就建，已有则按 force 决定是否重置
        await self._ensure_job_rows(photo_ids, force=force)
        await self.session.flush()

        # 2) 认领一批（已 done 的不会被认领，除非 force 已把它们重置）
        claimed = await self._claim(limit)
        report.skipped = max(0, len(photo_ids) - len(claimed))

        # 3) 逐条执行：成功写 done，失败写 failed（等下次重试）
        budget = self.settings.job_max_cost_cents
        for job in claimed:
            if budget > 0 and report.cost_cents >= budget:
                report.stopped_by_budget = True
                report.errors.append(
                    f"达到单次成本上限 {budget} 分，提前结束（剩余任务下次跑批继续）"
                )
                break

            report.processed += 1
            try:
                cost = await self.process_one(job.photo_id)
            except Exception as exc:  # noqa: BLE001 - 单条失败不能中断整批
                self._mark_failed(job, str(exc))
                report.failed += 1
                report.errors.append(f"photo={job.photo_id} 失败：{exc}")
                logger.warning("[%s] photo=%s 失败：%s", self.job_type, job.photo_id, exc)
                continue

            self._mark_done(job, cost)
            report.succeeded += 1
            report.cost_cents += cost

        await self.session.flush()
        report.latency_ms = int((time.perf_counter() - started) * 1000)
        return report

    # ---------------------------------------------------------------- 内部
    async def _ensure_job_rows(self, photo_ids: list[uuid.UUID], *, force: bool) -> None:
        """为候选照片建任务行。

        - force=False：已存在就 DO NOTHING（done 的不会被重跑，这是不重复烧钱的关键）
        - force=True ：重置为 pending、attempts 归零（换模型 / 全量重建时用）
        """
        rows = [
            {
                "photo_id": pid,
                "job_type": self.job_type,
                "ai_model": self.ai_model,
                "status": JOB_PENDING,
                "attempts": 0,
                "cost_cents": 0.0,
            }
            for pid in photo_ids
        ]
        stmt = insert(IngestJob).values(rows)
        if force:
            stmt = stmt.on_conflict_do_update(
                index_elements=["photo_id", "job_type", "ai_model"],
                set_={"status": JOB_PENDING, "attempts": 0, "last_error": None},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["photo_id", "job_type", "ai_model"])
        await self.session.execute(stmt)

    async def _claim(self, limit: int) -> list[IngestJob]:
        """认领一批任务：pending / failed 且重试次数还没用完。"""
        max_attempts = self.settings.job_max_attempts
        stmt = (
            select(IngestJob)
            .where(
                IngestJob.job_type == self.job_type,
                IngestJob.ai_model == self.ai_model,
                IngestJob.status.in_([JOB_PENDING, JOB_FAILED]),
                IngestJob.attempts < max_attempts,
            )
            .order_by(IngestJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list((await self.session.execute(stmt)).scalars())
        for job in jobs:
            job.status = JOB_RUNNING
            job.attempts += 1
        return jobs

    def _mark_done(self, job: IngestJob, cost_cents: float) -> None:
        job.status = JOB_DONE
        job.last_error = None
        job.cost_cents = float(job.cost_cents or 0) + float(cost_cents)
        job.finished_at = datetime.now(UTC)

    def _mark_failed(self, job: IngestJob, error: str) -> None:
        job.status = JOB_FAILED
        job.last_error = error[:1000]
        job.finished_at = datetime.now(UTC)


def run_job_cli(job_factory, description: str, argv: list[str] | None = None) -> None:
    """所有 `python -m app.jobs.xxx` 统一的命令行入口。

    参数只有两个，因为它们就是批处理最重要的两个旋钮：
      --limit 控制"这次最多跑多少条"（单次成本）
      --force 控制"是否忽略已完成状态重跑"（换模型后全量重建）
    """
    import argparse
    import asyncio

    from app.db.session import SessionFactory

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--limit", type=int, default=200, help="本次最多处理多少条")
    parser.add_argument(
        "--force", action="store_true", help="忽略已完成状态，强制重跑（换模型后用）"
    )
    args = parser.parse_args(argv)

    async def _run() -> None:
        async with SessionFactory() as session:
            job = job_factory(session)
            report = await job.run(limit=args.limit, force=args.force)
            await session.commit()
        print(report.summary())
        for err in report.errors[:10]:
            print(f"  ! {err}")

    asyncio.run(_run())
