"""LangGraph 的 `AgentState`（阶段 4 W4-2）。

## 为什么是 TypedDict 而不是 dataclass

LangGraph 的每个节点返回**部分状态更新**，框架负责把它 merge 回总状态。
TypedDict 天然表达"这是一份键值映射、可以只给其中几个键"，
而 dataclass 的更新语义（None 是"不变"还是"置空"）会一直扯皮。

## 滑动窗口为什么是 3 轮

`messages` 只保留最近 3 轮（6 条：user/assistant 各 3）：

- **够用**：指代消解（"那里的民宿"）需要的上下文几乎从不超过上一轮
- **省钱**：messages 每一轮都要进 prompt，窗口线性增长 = token 线性烧钱
- **抗污染**：早期跑偏的对话不该一直影响后面的判断

窗口由 `SLIDING_WINDOW_TURNS` 常量控制，改一处即可。

## 字段与 `PipelineState` 的关系

`PipelineState` 是**纯 Python 管道**的状态（阶段 0~3），本文件是**图运行时**的状态。
两者刻意并存：`AGENT_RUNTIME` 切换的是编排方式，不是数据契约。
图跑完之后会由 `graph.py` 把 `AgentState` 折叠回 `PipelineState`，
于是**上层（API / 评测）看到的永远是同一种结构**，两种运行时才能逐条对比。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, TypedDict

from langgraph.graph import MessagesState  # noqa: F401 - 供类型检查器与文档引用

#: 滑动窗口保留的**轮数**（1 轮 = 1 条 user + 1 条 assistant）
SLIDING_WINDOW_TURNS = 3


def _keep_last_turns(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """messages 的 reducer：**拼接后只保留最近 N 轮**。

    LangGraph 默认的 messages reducer 是"只增不减"，那会让状态无限膨胀 ——
    一次会话聊 50 轮，第 50 轮的 prompt 里就带着前 49 轮，成本与延迟都失控。
    这里显式截断，窗口大小成为**状态定义的一部分**，而不是散落在各节点里的约定。
    """
    merged = list(left or []) + list(right or [])
    if len(merged) <= SLIDING_WINDOW_TURNS * 2:
        return merged
    # 从尾部按"轮"切：保证窗口边界落在 user 消息上，不会把一轮拆成半轮
    return merged[-SLIDING_WINDOW_TURNS * 2 :]


class AgentState(TypedDict, total=False):
    """一次问答在图里流动的全部状态。

    标记 `total=False` 是有意的：节点只声明自己关心的键，
    新增字段不必改动所有节点签名。
    """

    # ---- 身份与输入 ----
    user_id: uuid.UUID
    session_id: str
    query: str

    #: 最近 3 轮对话。`Annotated` 的 reducer 负责自动截断（见 `_keep_last_turns`）
    messages: Annotated[list[dict[str, Any]], _keep_last_turns]

    # ---- 各环节产物 ----
    rewritten_query: str
    #: 结构化解析结果（dict 形态，对应 dataclass `ParsedQuery`）
    parsed_query: dict[str, Any] | None
    #: 检索命中（dict 形态，对应 `SearchHit`）
    evidences: list[dict[str, Any]]
    #: 带编号的上下文块（dict 形态，对应 `EvidenceBlock`）
    blocks: list[dict[str, Any]]
    context_text: str
    answer: str
    #: 引用到的文档引用（字符串形态）
    citations: list[str]
    #: self_check 的违规描述（空列表 = 通过）
    violations: list[str]

    # ---- 记忆（W4-1）----
    memory_preferences: list[str]
    memory_trip_context: list[str]
    memory_used_ids: list[str]

    # ---- 控制流 ----
    refused: bool
    refuse_reason: str
    #: 已生成次数。self_check 失败后只允许 +1，第二次再失败就拒答
    attempts: int
    #: 本次生成是否被 self_check 打回过（供 SSE 发 restart 事件）
    restarted: bool

    # ---- 元信息 ----
    latency_ms: int
    token_usage: dict[str, float]


def initial_state(
    *,
    user_id: uuid.UUID,
    session_id: str,
    query: str,
    history: list[dict[str, str]] | None = None,
) -> AgentState:
    """构造初始状态（把 history 折叠进 `messages` 滑动窗口）。"""
    return AgentState(
        user_id=user_id,
        session_id=session_id,
        query=query,
        messages=_keep_last_turns([], list(history or [])),
        rewritten_query="",
        parsed_query=None,
        evidences=[],
        blocks=[],
        context_text="",
        answer="",
        citations=[],
        violations=[],
        memory_preferences=[],
        memory_trip_context=[],
        memory_used_ids=[],
        refused=False,
        refuse_reason="",
        attempts=0,
        restarted=False,
        latency_ms=0,
        token_usage={},
    )
