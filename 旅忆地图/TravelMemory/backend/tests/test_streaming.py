"""阶段 2 W2-3：流式输出的自查。

出口条件有两条：

1. **首字可见**：不等生成完就往外推文本
2. **可中断**：客户端断开后能立刻收手，不继续烧 token

这两条在浏览器里点两下就能验证，但"能点两下"不代表有回归保护。
这里用 mock LLM（按字符切段 + 每段 sleep）把它们变成断言：
只要有人把流式改回"攒完再发"，第一条测试就会红。
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fakes import InMemoryVectorSearch, load_demo_corpus

from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.rag.time_parser import parse_time_range
from app.agent.state import ParsedQuery
from app.api.v1.agent import _sse
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.llm import build_llm


def _build_pipeline(store: InMemoryVectorSearch) -> AgentPipeline:
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(
        store,
        embedder,
        RetrieverConfig(top_k=5, min_score=0.05, enable_filter=True, enable_lexical=True),
        lexical=store,
    )
    return AgentPipeline(build_llm(settings), retriever, max_context_chars=2000, settings=settings)


@pytest.fixture
async def store(user_id) -> InMemoryVectorSearch:
    return await load_demo_corpus(user_id, build_embedder(get_settings()))


async def test_stream_events_are_in_order(store, user_id) -> None:
    """事件顺序必须是 parsed → evidence → delta* → done。"""
    pipeline = _build_pipeline(store)
    trace = Trace(session=None, user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")

    types: list[str] = []
    deltas: list[str] = []
    async for event in pipeline.run_stream(
        user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景", trace=trace
    ):
        types.append(event["type"])
        if event["type"] == "delta":
            deltas.append(event["text"])

    assert types[0] == "parsed"
    assert types[1] == "evidence"
    assert types[-1] == "done"
    assert "delta" in types
    # 首字可见：delta 必须出现在 done 之前
    assert types.index("delta") < types.index("done")
    assert "".join(deltas), "delta 拼起来必须是非空答案"


async def test_stream_is_chunked_not_batched(store, user_id) -> None:
    """首字可见的可观测证据：delta 不止一段。

    MockLLM 按 `mock_stream_chunk_chars` 切段并逐段 sleep，
    如果哪天有人改成"生成完一次性 yield"，这里会立刻变红。
    """
    pipeline = _build_pipeline(store)
    trace = Trace(session=None, user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")

    deltas = [
        event["text"]
        async for event in pipeline.run_stream(
            user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景", trace=trace
        )
        if event["type"] == "delta"
    ]

    assert len(deltas) >= 2, f"答案应当被切成多段推送，实际 {len(deltas)} 段"
    # 每段都不该是整篇答案（否则等于没流）
    assert max(len(d) for d in deltas) < sum(len(d) for d in deltas)


async def test_stream_can_be_interrupted(store, user_id) -> None:
    """可中断：拿到第一段就 break，生成器要能干净退出、不抛异常。

    这正是前端"停止生成"按钮按下后服务端发生的事：
    `async for` 提前退出 + `aclose()`。生成器若没写好，这里会挂住或抛错。
    """
    pipeline = _build_pipeline(store)
    trace = Trace(session=None, user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景")

    got: list[str] = []
    stream = pipeline.run_stream(
        user_id=user_id, session_id="s", query="乌镇 西塘 摇橹船 夜景", trace=trace
    )
    async for event in stream:
        if event["type"] == "delta":
            got.append(event["text"])
            break  # 模拟用户点"停止"

    assert got, "中断前至少应当收到一段文本"
    # aclose 必须能在有限时间内完成（挂住 = 生成协程没被正确取消）
    await asyncio.wait_for(stream.aclose(), timeout=5)


async def test_parsed_event_carries_rule_time_source(store, user_id) -> None:
    """中文时间表达在管道里真的生效了：parsed 事件带 rule 来源的时间窗。"""
    pipeline = _build_pipeline(store)
    query = "我去年秋天去过哪些古镇？"
    trace = Trace(session=None, user_id=user_id, session_id="s", query=query)

    parsed: dict = {}
    async for event in pipeline.run_stream(
        user_id=user_id, session_id="s", query=query, trace=trace
    ):
        if event["type"] == "parsed":
            parsed = event["payload"]

    expected = parse_time_range(query, today=date.today())
    assert expected is not None, "规则解析器应当识别出「去年秋天」"
    assert parsed.get("time_source") == "rule", f"时间应来自规则计算，实际：{parsed}"
    assert parsed["time_range"]["start"] == expected.start.isoformat()
    assert parsed["time_range"]["end"] == expected.end.isoformat()


def test_build_filter_degrades_without_hallucinating() -> None:
    """不确定就不过滤：没有条件返回 None；degraded 时丢弃 LLM 猜的地名/主题。"""
    # 1) 什么都没有 → None（走不过滤分支，绝不硬凑一个时间窗）
    assert Retriever.build_filter(ParsedQuery()) is None

    # 2) degraded 时 places/themes 不可信，必须丢掉
    flt = Retriever.build_filter(
        ParsedQuery(
            places=["乌镇"],
            themes=["古镇"],
            degraded=True,
            time_range={"start": "2025-09-01", "end": "2025-11-30"},
        )
    )
    assert flt is not None and flt.places == [] and flt.themes == []
    # 但规则算出的时间要保留（离线模式下时间过滤全靠它）
    assert flt.date_start == date(2025, 9, 1)

    # 3) 时间格式坏了（LLM 编的）→ 宁可不过滤
    broken = Retriever.build_filter(ParsedQuery(time_range={"start": "去年秋天", "end": "?"}))
    assert broken is None


def test_sse_frame_format() -> None:
    """SSE 帧格式：`event:` 行 + `data:` 行 + 空行。少一个空行前端就收不到。"""
    frame = _sse("delta", {"text": "你好"}).decode("utf-8")
    assert frame.startswith("event: delta\n")
    assert frame.endswith("\n\n")
    assert '"text": "你好"' in frame  # ensure_ascii=False：中文不能被转义成 \uXXXX


def test_sse_frame_keeps_newlines_inside_payload() -> None:
    """data 里出现换行会把一帧拆成两帧 —— 必须靠 JSON 转义消灭它。"""
    frame = _sse("delta", {"text": "第一行\n第二行"}).decode("utf-8")
    assert frame.count("\n\n") == 1, "帧内不允许出现空行"
