"""管道状态容器。

用可变 dataclass 而不是 Pydantic 模型：管道内部是原地传递与修改，
刻意保持轻量；对外（HTTP / CLI 输出）再转成 schemas 里的不可变结构。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.schemas.document import DocumentRef, SearchHit
from app.schemas.memory import MemoryContext
from app.utils.intent import INTENT_FACT


@dataclass
class ParsedQuery:
    """结构化解析结果（阶段 2 W2-1 起真正生效）。

    **两个来源，一套字段**：
      - `keywords / places / themes` 来自 LLM（它擅长从句子里"认出"实体）
      - `time_range` 优先来自规则解析器 `rag/time_parser.py`（它擅长"算准"日期）

    `time_source` 记录时间范围到底是规则算的还是 LLM 给的 —— 排查误过滤时，
    这是第一个要看的字段。
    """

    keywords: list[str] = field(default_factory=list)
    time_range: dict[str, str] | None = None
    places: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    #: 解析失败时置 True：检索阶段跳过 **LLM 抽出来的**过滤条件，宁可多召回不要漏召回
    degraded: bool = False
    #: 时间范围来源：rule（规则解析器）/ llm / none
    time_source: str = "none"
    #: 命中的时间表达原文（如"去年秋天"），写进 trace 便于回溯
    time_label: str = ""
    #: 查询意图（阶段 5.3）：fact（事实问答，默认）/ plan（行程规划）/ recommend（推荐）。
    #: plan/recommend 豁免 self_check 的逐句引用校验 —— 它们不是事实题，
    #: 拿"每句带 [n]"去量它们是错口径（阶段 5.2/5.3 连续两次真实事故）。
    #: 由 QueryParseNode 用规则分类器填写，定义见 `app/utils/intent.py`。
    intent: str = INTENT_FACT


@dataclass
class EvidenceBlock:
    """进入上下文的一条证据，`index` 即 prompt 里的 `[n]` 编号（从 1 开始）。"""

    index: int
    ref: DocumentRef
    content: str
    score: float
    meta: dict = field(default_factory=dict)


@dataclass
class PipelineState:
    """一次 Agent 问答的全部中间状态。"""

    user_id: uuid.UUID
    session_id: str
    query: str

    #: 最近若干轮对话（阶段 4 的短期记忆会替换这里的简易实现）
    history: list[dict[str, str]] = field(default_factory=list)
    #: 阶段 4 W4-1：本次要注入上下文的记忆（`[用户偏好]` / `[当前行程上下文]`）
    memory: MemoryContext | None = None

    # ---- 各节点的产物 ----
    rewritten_query: str = ""
    parsed: ParsedQuery | None = None
    #: 检索过程记录（用了什么过滤条件、两路各召回多少）——排查"为什么没召回"的第一现场
    retrieve_debug: object | None = None
    hits: list[SearchHit] = field(default_factory=list)
    #: 工具调用决策（阶段 3）。类型声明成 object：state.py 不能反向 import 节点层
    tool_decision: object | None = None
    #: 工具本次不可用（失败/无结果）：generate 会在 prompt 里声明，避免模型假装查过
    tool_unavailable: bool = False
    blocks: list[EvidenceBlock] = field(default_factory=list)
    #: 拼好后交给 LLM 的上下文原文（保留它在 trace 里便于复盘）
    context_text: str = ""
    answer: str = ""
    citations: list[DocumentRef] = field(default_factory=list)

    # ---- 结果与元信息 ----
    refused: bool = False
    refuse_reason: str = ""
    #: generate 重试次数：self_check 失败后只允许再生成一次
    attempts: int = 0
    latency_ms: int = 0
    token_usage: dict[str, float] = field(default_factory=dict)

    @property
    def search_query(self) -> str:
        """实际用于向量检索的文本：改写结果优先，失败回落原问题。"""
        return self.rewritten_query or self.query
