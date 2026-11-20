"""可观测三张表：AgentRun / AgentStep / ToolCallLog。

为什么必须三张而不是一张：Owner 口径要能回答三类问题
  1. 这次问答整体多快、花了多少钱？（Run）
  2. 卡在哪个环节、retrieve 返回了几条？（Step）
  3. 哪个工具被选中、有没有命中缓存？（ToolCallLog，阶段 3 起）

写入入口**只有** app/agent/observability.py（PROJECT.md §二、[5]），节点不得自己写表。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, PrimaryKey


class AgentRun(Base):
    """一次完整的 Agent 问答。"""

    __tablename__ = "agent_run"

    id: Mapped[PrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    #: 阶段 0 由 CLI 传入一个固定值；阶段 4 引入 ChatSession 后换成真实会话 id
    session_id: Mapped[str | None] = mapped_column(String(64))

    query: Mapped[str] = mapped_column(Text, nullable=False)
    #: success | refused | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: {"prompt_tokens":..,"completion_tokens":..,"cost_cents":..}
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: 归因关键：改 prompt 而不改版本号，评测就无法解释指标变化
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[CreatedAt]


class AgentStep(Base):
    """Agent 内部的一个环节（rewrite / retrieve / generate ...）。"""

    __tablename__ = "agent_step"

    id: Mapped[PrimaryKey]
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: rewrite | query_parse | retrieve | build_context | generate | self_check
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[CreatedAt]


class ToolCallLog(Base):
    """工具调用日志。

    `cache_hit` 是必填字段：缓存命中率 ≥ 60% 是验收指标（设计文档 §10.1），
    没有它就无法验证 W3-3 的效果。阶段 0 尚无工具，表先建好。
    """

    __tablename__ = "tool_call_log"

    id: Mapped[PrimaryKey]
    #: message_id 依赖 Message 表（阶段 4），先留空
    message_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[CreatedAt]
