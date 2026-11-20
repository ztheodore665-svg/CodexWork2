"""W4-2：LangGraph 图运行时的测试。

两条出口条件：
  1. 两种运行时的**现有测试**全部通过（由 `test_pipeline_mock.py` 的参数化版本覆盖）
  2. 多轮对话能记住上文指代（第一句问乌镇，第二句问"那里的民宿" → 改写要补全乌镇）
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakes import InMemoryVectorSearch, load_demo_corpus

from app.agent.memory.service import MemoryService
from app.agent.memory.store import InMemoryMemoryStore
from app.agent.nodes.graph.graph import (
    MAX_GENERATE_RETRIES,
    LangGraphAgent,
    route_after_self_check,
)
from app.agent.nodes.graph.state import (
    SLIDING_WINDOW_TURNS,
    initial_state,
)
from app.agent.observability import Trace
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.runtime import (
    RUNTIME_GRAPH,
    RUNTIME_PIPELINE,
    build_agent,
    is_graph_runtime,
    resolve_runtime,
)
from app.core.config import Settings, get_settings
from app.core.embedding import build_embedder
from app.core.llm import BaseLLM, LLMMessage, LLMResult, build_llm
from app.utils.citation import citation_rate

# ---------------------------------------------------------------- 滑动窗口


def test_messages_window_keeps_last_three_turns():
    history: list[dict[str, str]] = []
    for i in range(1, 6):
        history.append({"role": "user", "content": f"第{i}轮问题"})
        history.append({"role": "assistant", "content": f"第{i}轮回答"})

    state = initial_state(user_id=uuid.uuid4(), session_id="s", query="第6轮问题", history=history)
    joined = " ".join(m["content"] for m in state["messages"])
    assert len(state["messages"]) == SLIDING_WINDOW_TURNS * 2
    # 窗口 = 最近 3 轮：第 3/4/5 轮留下，第 1/2 轮被挤掉
    assert "第5轮回答" in joined
    assert "第3轮问题" in joined
    assert "第1轮" not in joined
    assert "第2轮" not in joined
    # 当前提问不在 history 里（它由 `query` 字段单独承载，与管道口径一致）
    assert "第6轮问题" not in joined


def test_short_history_is_not_padded():
    history = [{"role": "user", "content": "只有一轮"}]
    state = initial_state(user_id=uuid.uuid4(), session_id="s", query="q", history=history)
    assert len(state["messages"]) == 1


# ---------------------------------------------------------------- 条件边（纯函数）


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"violations": [], "attempts": 0}, "write_memory"),
        ({"violations": ["缺少引用"], "attempts": 1}, "generate"),
        ({"violations": ["缺少引用"], "attempts": 2}, "refuse"),
        ({"violations": ["缺少引用"], "attempts": 0, "refused": False}, "generate"),
    ],
)
def test_route_after_self_check(state: dict, expected: str):
    assert route_after_self_check(state, MAX_GENERATE_RETRIES) == expected


def test_refused_short_circuits_to_write_memory():
    """无证据拒答不是"违规"，不该被 refuse 节点覆盖话术，也不该重试。"""
    assert (
        route_after_self_check({"violations": [], "attempts": 0, "refused": True}) == "write_memory"
    )


# ---------------------------------------------------------------- 运行时开关


def test_runtime_defaults_to_pipeline(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "pipeline")
    get_settings.cache_clear()
    try:
        assert resolve_runtime() == RUNTIME_PIPELINE
        assert is_graph_runtime() is False
    finally:
        get_settings.cache_clear()


def test_runtime_graph_is_recognized(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "graph")
    get_settings.cache_clear()
    try:
        assert resolve_runtime() == RUNTIME_GRAPH
        assert is_graph_runtime() is True
    finally:
        get_settings.cache_clear()


def test_unknown_runtime_falls_back_to_pipeline(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "langchain!!")
    get_settings.cache_clear()
    try:
        assert resolve_runtime() == RUNTIME_PIPELINE
    finally:
        get_settings.cache_clear()


def test_build_agent_respects_switch(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "graph")
    get_settings.cache_clear()
    try:
        agent = build_agent(InMemoryVectorSearch())
        assert isinstance(agent, LangGraphAgent)
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("AGENT_RUNTIME", "pipeline")
    get_settings.cache_clear()
    try:
        from app.agent.pipeline import AgentPipeline

        agent2 = build_agent(InMemoryVectorSearch())
        assert isinstance(agent2, AgentPipeline)
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- 端到端（两种运行时同一套断言）


class ScriptedLLM(BaseLLM):
    """确定性替身：按 **purpose 分桶**返回预设内容。

    为什么按 purpose 分桶而不是"按调用次序出队"：管道里一个 purpose 可能被调用
    多次（generate 会因为 self_check 失败而重试），用队列的话第 N 次取到什么
    完全取决于前面跑了几步，测试会变得极难维护，也掩盖真实意图。
    """

    def __init__(self, settings: Settings, answers: dict[str, list[str]] | None = None) -> None:
        super().__init__(settings)
        self.answers: dict[str, list[str]] = {k: list(v) for k, v in (answers or {}).items()}
        self.calls: list[dict[str, Any]] = []

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return "scripted-llm"

    async def _completion(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        return self._next(messages, purpose)

    async def _astream_completion(self, messages, *, purpose, temperature, max_tokens, usage):
        result = self._next(list(messages), purpose)
        usage.prompt_tokens = 1
        usage.completion_tokens = max(1, len(result.content) // 2)
        for start in range(0, len(result.content), 8):
            yield result.content[start : start + 8]

    def _next(self, messages: list[LLMMessage], purpose: str) -> LLMResult:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        self.calls.append({"purpose": purpose, "user": last_user})
        bucket = self.answers.get(purpose)
        if bucket:
            content = bucket.pop(0)
        elif purpose == "query_parse":
            content = '{"keywords": [], "places": [], "themes": [], "time_range": null}'
        else:
            # 兜底：回一段必定带引用的话，避免"没预设答案"意外变成拒答
            content = "依据记录回答[1]。"
        return LLMResult(content=content, model=self.model, provider=self.provider)


@pytest.fixture
async def store(user_id) -> InMemoryVectorSearch:
    return await load_demo_corpus(user_id, build_embedder(get_settings()))


def _build(
    monkeypatch,
    store,
    runtime: str,
    *,
    answers: dict[str, list[str]] | None = None,
    min_score: float = 0.05,
    memory: MemoryService | None = None,
):
    monkeypatch.setenv("AGENT_RUNTIME", runtime)
    get_settings.cache_clear()
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=min_score))
    llm = ScriptedLLM(settings, answers)
    agent = LangGraphAgent(llm, retriever, max_context_chars=2000, settings=settings, memory=memory)
    return agent, llm, settings


@pytest.mark.asyncio
async def test_graph_answers_with_citations(monkeypatch, store, user_id):
    agent, _llm, _settings = _build(
        monkeypatch, store, "graph", answers={"generate": ["你去过乌镇[1]。"]}
    )
    trace = Trace(session=None, user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")
    state = await agent.run(
        user_id=user_id,
        session_id="s",
        query="乌镇 西塘 摇橹船 夜景",
        trace=trace,
    )
    assert state.refused is False, state.answer
    assert state.blocks, "必须检索到证据"
    assert state.citations, "答案必须带引用"
    assert citation_rate(state.answer) == 1.0


@pytest.mark.asyncio
async def test_graph_refuses_without_evidence(monkeypatch, store, user_id):
    """拒答红线在 graph 路径上同样成立（无证据 → 拒答，且不调 LLM 编答案）。"""
    monkeypatch.setenv("AGENT_RUNTIME", "graph")
    get_settings.cache_clear()
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=0.99))
    llm = ScriptedLLM(settings, {"generate": ["我去过珠峰[1]。"]})
    agent = LangGraphAgent(llm, retriever, settings=settings)

    state = await agent.run(
        user_id=user_id,
        session_id="s",
        query="我攀登珠峰用了几天？",
    )
    assert state.refused is True
    assert state.refuse_reason == "no_evidence"
    assert state.citations == []
    assert citation_rate(state.answer) == 1.0


@pytest.mark.asyncio
async def test_graph_streams_deltas_and_done(monkeypatch, store, user_id):
    agent, _llm, _settings = _build(
        monkeypatch, store, "graph", answers={"generate": ["你去过乌镇[1]。"]}
    )
    events: list[dict] = []
    async for event in agent.run_stream(
        user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景"
    ):
        events.append(event)

    kinds = [e["type"] for e in events]
    assert "parsed" in kinds
    assert "evidence" in kinds
    assert kinds.count("delta") >= 1
    assert kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "delta")


@pytest.mark.asyncio
async def test_graph_retries_once_then_refuses(monkeypatch, store, user_id):
    """self_check 违规 → 回 generate 重试一次 → 仍违规 → refuse。"""
    agent, llm, _settings = _build(
        monkeypatch,
        store,
        "graph",
        answers={"generate": ["我编一句话，不带引用。", "还是不带引用。"]},
    )
    state = await agent.run(user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")

    assert state.refused is True
    assert state.refuse_reason == "self_check_failed"
    # 拒答不允许挂引用：话说"不知道"却给出处是自相矛盾
    assert state.citations == []
    # 次数用尽：self_check 判违规两次，只允许重生成一次
    assert state.attempts == MAX_GENERATE_RETRIES + 1
    # rewrite + query_parse 各一次，generate 两次（首次 + 重试）
    assert len([c for c in llm.calls if c["purpose"] == "generate"]) == 2


@pytest.mark.asyncio
async def test_graph_retry_then_success(monkeypatch, store, user_id):
    agent, _llm, _settings = _build(
        monkeypatch, store, "graph", answers={"generate": ["第一次不带引用。", "去过乌镇[1]。"]}
    )
    state = await agent.run(user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")
    assert state.refused is False
    assert citation_rate(state.answer) == 1.0


@pytest.mark.asyncio
async def test_multi_turn_anaphora_is_resolved(monkeypatch, store, user_id):
    """W4-2 出口条件：第二句问"那里的民宿"，改写结果必须补全成"乌镇"。"""
    agent, llm, _settings = _build(
        monkeypatch,
        store,
        "graph",
        answers={"rewrite": ["去过乌镇吗"], "generate": ["乌镇的民宿[1]。"]},
    )
    history = [
        {"role": "user", "content": "我去过乌镇吗？"},
        {"role": "assistant", "content": "去过乌镇[1]。"},
    ]
    await agent.run(
        user_id=user_id,
        session_id="s",
        query="那里的民宿怎么样？",
        history=history,
    )

    rewrite_calls = [c for c in llm.calls if c["purpose"] == "rewrite"]
    assert rewrite_calls, "必须调用过 rewrite"
    prompt = rewrite_calls[-1]["user"]

    # ① 历史进了改写 prompt（否则模型无从消解指代）
    assert "我去过乌镇吗？" in prompt
    # ② 当前问题也进了 prompt
    assert "那里的民宿怎么样？" in prompt

    rewritten = llm.calls[-1]["user"]
    assert rewritten is not None


@pytest.mark.asyncio
async def test_multi_turn_rewrite_completes_the_referent(monkeypatch, store, user_id):
    """用替身 LLM 真实产出"乌镇 的民宿"，断言它进了检索串。"""
    agent, _llm, _settings = _build(
        monkeypatch,
        store,
        "graph",
        answers={"rewrite": ["乌镇 的民宿"], "generate": ["乌镇 民宿[1]。"]},
    )

    state = await agent.run(
        user_id=user_id,
        session_id="s",
        query="那里的民宿怎么样？",
        history=[
            {"role": "user", "content": "我去过乌镇吗？"},
            {"role": "assistant", "content": "去过乌镇[1]。"},
        ],
    )
    assert state.rewritten_query == "乌镇 的民宿", (
        f"指代没被补全，实际改写结果：{state.rewritten_query!r}"
    )
    assert state.refused is False


@pytest.mark.asyncio
async def test_graph_injects_memory(monkeypatch, store, user_id):
    """W4-1 + W4-2 串起来：图路径上记忆也要能进上下文。"""
    monkeypatch.setenv("AGENT_RUNTIME", "graph")
    get_settings.cache_clear()
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=0.05))
    memory = MemoryService(InMemoryMemoryStore())
    await memory.remember_from_text(user_id, "我不喜欢人多")
    agent = LangGraphAgent(build_llm(settings), retriever, settings=settings, memory=memory)

    sink: dict = {}
    async for _e in agent.run_stream(
        user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景", sink=sink
    ):
        pass

    final = sink["final"]
    assert any("不喜欢人多" in line for line in final.get("memory_preferences", []))
    assert "[用户偏好]" in _to_pipeline_context(final)


def _to_pipeline_context(state: dict) -> str:
    from app.agent.nodes.graph.graph import _to_pipeline

    return _to_pipeline(state).context_text


@pytest.mark.asyncio
async def test_both_runtimes_agree_on_answer_shape(monkeypatch, store, user_id):
    """两种运行时跑同一个问题，输出的**结构**必须一致（内容可以不同）。"""
    from app.agent.pipeline import AgentPipeline

    query = "乌镇 西塘 摇橹船 夜景"
    results = {}
    for runtime in (RUNTIME_PIPELINE, RUNTIME_GRAPH):
        monkeypatch.setenv("AGENT_RUNTIME", runtime)
        get_settings.cache_clear()
        settings = get_settings()
        embedder = build_embedder(settings)
        retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=0.05))
        llm = build_llm(settings)
        if runtime == RUNTIME_GRAPH:
            runner: Any = LangGraphAgent(llm, retriever, settings=settings)
        else:
            runner = AgentPipeline(llm, retriever, settings=settings)
        state = await runner.run(user_id=user_id, session_id="s", query=query)
        results[runtime] = state

    for name, state in results.items():
        assert state.blocks, f"{name} 应当有证据"
        assert citation_rate(state.answer) == 1.0, f"{name} 引用率必须 100%"
        assert state.refused is False, f"{name} 不该拒答：{state.answer}"

    assert len(results[RUNTIME_PIPELINE].blocks) == len(results[RUNTIME_GRAPH].blocks)


def _build_runtime(monkeypatch, store, runtime: str, *, answers=None):
    """构造指定运行时（阶段 16 的契约测试要成对跑同一道题）。"""
    from app.agent.pipeline import AgentPipeline

    monkeypatch.setenv("AGENT_RUNTIME", runtime)
    get_settings.cache_clear()
    settings = get_settings()
    retriever = Retriever(
        store, build_embedder(settings), RetrieverConfig(top_k=5, min_score=0.05)
    )
    llm = ScriptedLLM(settings, answers)
    runner: Any = (
        LangGraphAgent(llm, retriever, max_context_chars=2000, settings=settings)
        if runtime == RUNTIME_GRAPH
        else AgentPipeline(llm, retriever, max_context_chars=2000, settings=settings)
    )
    return runner, llm, settings


@pytest.mark.asyncio
async def test_event_sequence_matches_pipeline(monkeypatch, store, user_id):
    """**完整**事件类型序列一致，而不是只比 parsed/evidence/done。

    阶段 16 之前这里用 `{parsed, evidence, done}` 过滤过 —— 那个断言在
    两套运行时的 delta / tool / restart 位置不同时依然是绿的，等于没测。
    前端是逐条解析 SSE 的，顺序不同就是 bug。
    """
    query = "乌镇 西塘 摇橹船 夜景"
    kinds: dict[str, list[str]] = {}
    for runtime in (RUNTIME_PIPELINE, RUNTIME_GRAPH):
        runner, _llm, _settings = _build_runtime(monkeypatch, store, runtime)
        kinds[runtime] = [
            event["type"]
            async for event in runner.run_stream(user_id=user_id, session_id="s", query=query)
        ]

    assert kinds[RUNTIME_PIPELINE] == kinds[RUNTIME_GRAPH]
    assert kinds[RUNTIME_GRAPH][0] == "parsed"
    assert kinds[RUNTIME_GRAPH][-1] == "done"
    assert "evidence" in kinds[RUNTIME_GRAPH]


@pytest.mark.asyncio
async def test_retry_budget_matches_between_runtimes(monkeypatch, store, user_id):
    """self_check 失败后的重试次数必须逐次一致（配置里只有一个数字）。

    第一版回答故意不带引用 → 触发 restart → 第二版带引用 → 通过。
    两套运行时都应该恰好重试 **1** 次，且最终答案相同。
    """
    answers = {"generate": ["这段回答没有任何引用。", "依据记录回答[1]。"]}
    observed: dict[str, dict[str, Any]] = {}

    for runtime in (RUNTIME_PIPELINE, RUNTIME_GRAPH):
        runner, llm, _settings = _build_runtime(
            monkeypatch, store, runtime, answers={"generate": list(answers["generate"])}
        )
        state = await runner.run(user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")
        observed[runtime] = {
            "generate_calls": sum(1 for call in llm.calls if call["purpose"] == "generate"),
            "answer": state.answer,
            "refused": state.refused,
        }

    # 第一次生成无引用 → 重试一次 → 第二次通过：两次调用，不是一次也不是三次
    assert observed[RUNTIME_PIPELINE]["generate_calls"] == 2
    assert observed[RUNTIME_GRAPH] == observed[RUNTIME_PIPELINE]
