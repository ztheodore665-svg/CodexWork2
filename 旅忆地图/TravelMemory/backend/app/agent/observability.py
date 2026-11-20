"""统一可观测（PROJECT.md §二、[5]）：Run / Step / ToolCall 只有一个写入口。

节点里**只允许**这样用：

```python
async with trace.step("retrieve", {"query": q}) as step:
    hits = await retriever.retrieve(q)
    step.output = {"count": len(hits)}
```

这样 trace 的口径天然一致（谁都自己去 new AgentStep 就会出现字段缺失、延迟口径不齐）。

两条落盘路径：
  - 数据库 AgentRun / AgentStep / ToolCallLog —— 便于 SQL 聚合与归因
  - JSONL 文件（backend/data/traces/YYYY-MM-DD.jsonl）—— 便于离线分析、单机排障

**任何写入失败都只告警不影响主流程**：trace 是旁路设施，不能因为记日志把问答搞挂。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts.loader import PROMPT_VERSION
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.traces import AgentRun, AgentStep, ToolCallLog

logger = get_logger(__name__)


class StepRecorder:
    """`trace.step(...)` 上下文对象：给节点一个地方写输出。"""

    def __init__(
        self,
        step_type: str,
        input_payload: dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> None:
        self.step_type = step_type
        self.input = input_payload or {}
        self.tool_name = tool_name
        self.output: dict[str, Any] | None = None
        self.started_at = time.perf_counter()
        self.latency_ms = 0


class Trace:
    """一次 Run 的追踪器。

    session 可以为 None（例如只做 JSONL 的单测 / 离线评测），此时只写文件。
    """

    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        session_id: str,
        query: str,
        session: AsyncSession | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session = session
        self.user_id = user_id
        self.session_id = session_id
        self.query = query
        self.run_id: uuid.UUID = uuid.uuid4()
        self.prompt_version = PROMPT_VERSION
        self.started_at = time.perf_counter()
        self._run: AgentRun | None = None
        self.steps: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ 生命周期
    async def start_run(self) -> uuid.UUID:
        """写入 Run 行并落一行 JSONL。返回 run_id（前端/评测要引用它）。"""
        self._append_jsonl(
            {
                "type": "run_start",
                "run_id": str(self.run_id),
                "user_id": str(self.user_id),
                "session_id": self.session_id,
                "query": self.query,
                "prompt_version": self.prompt_version,
                "ts": _utc_now(),
            }
        )
        if self.session is None:
            return self.run_id

        run = AgentRun(
            id=self.run_id,
            user_id=self.user_id,
            session_id=self.session_id,
            query=self.query,
            status="running",
            prompt_version=self.prompt_version,
        )
        try:
            self.session.add(run)
            await self.session.flush()
            self._run = run
        except Exception as exc:  # noqa: BLE001 - 可观测失败不能影响主流程
            logger.warning("写入 AgentRun 失败（不影响问答）: %s", exc)
            self._run = None
        return self.run_id

    @asynccontextmanager
    async def step(
        self,
        step_type: str,
        input_payload: dict[str, Any] | None = None,
        *,
        tool_name: str | None = None,
    ) -> AsyncIterator[StepRecorder]:
        """记录一个环节。节点只调用它，不直接操作表。"""
        recorder = StepRecorder(step_type, input_payload, tool_name)
        try:
            yield recorder
        finally:
            recorder.latency_ms = int((time.perf_counter() - recorder.started_at) * 1000)
            entry = {
                "type": "step",
                "run_id": str(self.run_id),
                "step_type": recorder.step_type,
                "input": recorder.input,
                "output": recorder.output,
                "tool_name": recorder.tool_name,
                "latency_ms": recorder.latency_ms,
                "ts": _utc_now(),
            }
            self.steps.append(entry)
            self._append_jsonl(entry)
            await self._persist_step(recorder)

    async def record_tool_call(
        self,
        tool_name: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        *,
        latency_ms: int,
        cache_hit: bool = False,
    ) -> None:
        """记录工具调用。cache_hit 必须落到每一行，缓存命中率的验收依赖它。"""
        entry = {
            "type": "tool_call",
            "run_id": str(self.run_id),
            "tool_name": tool_name,
            "input": input_payload,
            "output": output_payload,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "ts": _utc_now(),
        }
        self._append_jsonl(entry)
        if self.session is None:
            return
        try:
            self.session.add(
                ToolCallLog(
                    run_id=self.run_id,
                    tool_name=tool_name,
                    input=input_payload,
                    output=output_payload,
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                )
            )
            await self.session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入 ToolCallLog 失败: %s", exc)

    async def finish_run(
        self,
        *,
        status: str = "success",
        token_usage: dict[str, Any] | None = None,
    ) -> int:
        """结束 Run，返回总耗时 ms。"""
        latency_ms = int((time.perf_counter() - self.started_at) * 1000)
        self._append_jsonl(
            {
                "type": "run_end",
                "run_id": str(self.run_id),
                "status": status,
                "latency_ms": latency_ms,
                "token_usage": token_usage or {},
                "ts": _utc_now(),
            }
        )
        if self.session is not None and self._run is not None:
            self._run.status = status
            self._run.latency_ms = latency_ms
            self._run.token_usage = token_usage or {}
            try:
                await self.session.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning("更新 AgentRun 失败: %s", exc)
        return latency_ms

    # ------------------------------------------------------------------ 内部
    async def _persist_step(self, recorder: StepRecorder) -> None:
        if self.session is None:
            return
        try:
            self.session.add(
                AgentStep(
                    run_id=self.run_id,
                    step_type=recorder.step_type,
                    input=recorder.input,
                    output=recorder.output,
                    tool_name=recorder.tool_name,
                    latency_ms=recorder.latency_ms,
                )
            )
            await self.session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入 AgentStep 失败: %s", exc)

    def _append_jsonl(self, payload: dict[str, Any]) -> None:
        """追加一行 JSONL。单文件按天切，方便 grep 与归档。"""
        try:
            path: Path = self.settings.resolved_trace_dir / f"{datetime.now().date()}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 - 磁盘写不动也不能让请求失败
            logger.warning("写 trace JSONL 失败: %s", exc)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
