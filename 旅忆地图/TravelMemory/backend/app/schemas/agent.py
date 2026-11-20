"""Agent 接口的入参 / 出参契约（与 ORM 严格分离）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """一次问答请求。

    `session_id` 不传时后端生成一个 —— 阶段 4 的短期记忆会靠它做会话串联，
    现在先留好位置，避免到时候改接口。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    """一条证据，`index` 与回答里的 `[n]` 一一对应。"""

    index: int
    ref: str
    score: float
    content: str


class ChatMeta(BaseModel):
    """服务元信息，前端用来展示"当前跑的是哪一版"。"""

    prompt_version: str
    embedding_model: str
    runtime: str
    session_id: str
