"""阶段 3 W3-1 / W3-2 的行为测试（全部离线：mock provider + 内存缓存）。

这些测试守的是**四类不可逆的错误**，每一类都对应一次真实的事故形态：

1. 工具选错 → 用户拿到与问题无关的地名
2. 工具证据没有 POI ID → 引用无目标 → 引用率红线破掉
3. 工具失败冒泡 → 一个旁路设施把整条问答拖挂
4. 工具证据被上下文预算截断 → 模型仍引用 `[n]` → 越界 → 整段回答作废
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.eval.dataset import load_tool_cases
from app.agent.eval.runner import TOOL_SELECT_TARGET, run_tool_selection_eval
from app.agent.nodes.pipeline.tool_call import ToolCallNode, rule_select
from app.agent.observability import Trace
from app.agent.state import PipelineState
from app.core.config import get_settings
from app.core.llm import build_llm
from app.services.map_service import COORD_BATCH_MAX, build_map_service
from app.tools.base import ToolContext
from app.tools.poi_tool import SearchPoiTool
from app.tools.registry import build_default_registry
from app.utils.coords import LngLat, gcj02_to_wgs84, wgs84_to_gcj02

USER = uuid.uuid4()


def _state(query: str) -> PipelineState:
    return PipelineState(user_id=USER, session_id="s", query=query)


def _trace(query: str) -> Trace:
    return Trace(session=None, user_id=USER, session_id="s", query=query)


# ---------------------------------------------------------------- 规则选择
def test_route_wins_over_poi_when_both_appear() -> None:
    """「怎么走到最近的停车场」是在问路线，不是在问停车场。"""
    name, reason = rule_select("从这里怎么走到最近的停车场？")
    assert name == "calculate_route"
    assert "路线词" in reason


@pytest.mark.parametrize(
    "question",
    [
        "我去年秋天去过哪些古镇？",
        "我在杭州最喜欢哪类地点？",
        "春节期间我拍的照片都在哪？",
        "总结一下我这趟西北环线的感受。",
    ],
)
def test_memory_questions_do_not_call_tools(question: str) -> None:
    """回忆类问题不该去查地图：既烧配额，又答非所问。"""
    assert rule_select(question)[0] is None


def test_tool_selection_accuracy_meets_exit_criterion() -> None:
    """W3-1 出口条件：≥ 0.90，且样本量足够（≥20 条、≥5 条负例）。

    直接跑真实评测集而不是另造一份：否则"测试绿了"和"出口条件达成"是两件事。
    """
    cases = load_tool_cases()
    assert len(cases) >= 20, f"用例数不足：{len(cases)}"
    assert sum(1 for c in cases if not c.expects_tool) >= 5, "负例不足 5 条"
    assert run_tool_selection_eval(cases) >= TOOL_SELECT_TARGET


# ---------------------------------------------------------------- POI
async def test_poi_result_always_carries_non_empty_id() -> None:
    """没有 id 的工具结果不能进上下文：它会被当成"查到了"却没有可引用目标。"""
    service = build_map_service(get_settings())
    tool = SearchPoiTool(service, ToolContext())
    result = await tool.run({"keyword": "乌镇", "city": "嘉兴", "limit": 3})

    assert result.ok
    assert result.data["count"] >= 1
    for item in result.data["items"]:
        assert item["id"], "POI ID 是引用目标，不能为空"
    assert result.data["text"], "给模型的必须是一句可读的事实描述"


async def test_invalid_payload_is_not_retried() -> None:
    """入参不合法时不重试：同样的脏参数重试只是多烧一次配额。"""
    service = build_map_service(get_settings())
    tool = SearchPoiTool(service, ToolContext(max_retries=2))
    result = await tool.run({"keyword": 123})  # 类型错误

    assert result.ok is False
    assert result.retryable is False


async def test_tool_failure_degrades_instead_of_raising() -> None:
    """工具异常绝不冒泡：它是旁路设施，挂了应该让问答退回只靠记忆。"""
    service = build_map_service(get_settings())

    class BoomTool(SearchPoiTool):
        async def _run(self, validated):  # type: ignore[override]
            raise RuntimeError("模拟外部 API 崩溃")

    tool = BoomTool(service, ToolContext(max_retries=0))
    result = await tool.run({"keyword": "乌镇"})

    assert result.ok is False
    assert result.degraded is True
    assert "RuntimeError" in result.error


async def test_same_payload_hits_cache_second_time() -> None:
    """同一批坐标第二次转换必须命中缓存（R3-2.4）。"""
    service = build_map_service(get_settings())
    points = [LngLat(120.0 + i * 0.001, 30.0 + i * 0.001) for i in range(3)]

    first = await service.convert_batch(points, to_gcj02=True)
    assert service.last_cache_hit is False
    assert service.last_batches == 1

    second = await service.convert_batch(points, to_gcj02=True)
    assert service.last_cache_hit is True
    assert second == first, "命中缓存不能改变结果"


async def test_coord_conversion_is_batched_by_40() -> None:
    """120 个点必须切成 3 批：高德单次上限 40，超了不是变慢而是直接报错。"""
    service = build_map_service(get_settings())
    points = [LngLat(120.0 + i * 0.0001, 30.0 + i * 0.0001) for i in range(120)]

    converted = await service.convert_batch(points, to_gcj02=True)

    assert service.last_batches == 3
    assert len(converted) == 120
    assert COORD_BATCH_MAX == 40


def test_wgs84_gcj02_roundtrip_is_stable() -> None:
    """坐标系往返必须收敛，否则 GCJ-02 会被反复加偏移（越转越远）。"""
    original = LngLat(116.397428, 39.90923)  # 天安门
    gcj = wgs84_to_gcj02(original.lng, original.lat)
    back = gcj02_to_wgs84(gcj.lng, gcj.lat)

    # 偏移量本身有几百米，所以先确认"确实偏了"，再确认"能转回来"
    assert abs(gcj.lng - original.lng) > 1e-4
    assert abs(back.lng - original.lng) < 1e-4
    assert abs(back.lat - original.lat) < 1e-4


# ---------------------------------------------------------------- 节点
async def test_node_is_noop_when_tools_disabled(monkeypatch) -> None:
    """TOOLS_ENABLED=false（默认）时节点必须是透明的 —— 这是 test-degradation 的核心。"""
    monkeypatch.setenv("TOOLS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        node = ToolCallNode(build_llm(settings), build_default_registry(), settings)
        state = _state("乌镇附近有什么景点？")
        await node(state, _trace(state.query))
        assert state.hits == []
        assert state.tool_decision is None
    finally:
        get_settings.cache_clear()


async def test_tool_evidence_is_citable_and_numbered(monkeypatch) -> None:
    """工具事实必须并进统一的 `[n]` 序列：记忆在前、工具在后，编号连续。"""
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        from app.schemas.document import DocumentRef, SearchHit

        settings = get_settings()
        node = ToolCallNode(build_llm(settings), build_default_registry(), settings)
        state = _state("乌镇附近有什么景点？")
        # 先塞两条"记忆证据"，模拟 retrieve 已经跑过
        state.hits = [
            SearchHit(
                ref=DocumentRef(source_type="day", source_id="d1"),
                content="记忆证据一",
                score=0.9,
            ),
            SearchHit(
                ref=DocumentRef(source_type="day", source_id="d2"),
                content="记忆证据二",
                score=0.5,
            ),
        ]
        await node(state, _trace(state.query))

        tool_hits = [h for h in state.hits if h.ref.source_type == "map_tool"]
        assert tool_hits, "工具证据必须进入 state.hits"
        assert all(h.ref.source_id for h in tool_hits)
        # 顺序：记忆在前，工具在后（PRD §5 的默认方案）
        assert state.hits[-len(tool_hits) :] == tool_hits
        # 分数与最好的记忆证据并列，避免"排序最低"
        assert tool_hits[0].score == 0.9
    finally:
        get_settings.cache_clear()


async def test_tool_evidence_survives_tiny_context_budget(monkeypatch) -> None:
    """预算极小也不能把工具证据丢掉：丢了模型还会引用 [n] → 越界 → 整段作废。"""
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        from app.agent.rag.context_builder import build_context
        from app.schemas.document import DocumentRef, SearchHit

        settings = get_settings()
        node = ToolCallNode(build_llm(settings), build_default_registry(), settings)
        state = _state("乌镇附近有什么景点？")
        state.hits = [
            SearchHit(
                ref=DocumentRef(source_type="day", source_id=f"d{i}"),
                content="记忆证据" * 200,  # 每条都很长，逼出预算竞争
                score=0.9 - i * 0.1,
            )
            for i in range(5)
        ]
        await node(state, _trace(state.query))

        built = build_context(state.hits, max_chars=settings.context_max_chars)
        tool_blocks = [b for b in built.blocks if b.ref.source_type == "map_tool"]
        assert tool_blocks, "预算再紧，工具证据也必须留下来"
        # 编号仍从 1 连续：模型引用 [n] 与这里的 index 一一对应
        assert [b.index for b in built.blocks] == list(range(1, len(built.blocks) + 1))
    finally:
        get_settings.cache_clear()


async def test_tool_failure_sets_unavailable_flag(monkeypatch) -> None:
    """工具失败要留下"本次不可用"的痕迹，让 generate 能如实声明，而不是假装查过。"""
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        registry = build_default_registry()
        node = ToolCallNode(build_llm(settings), registry, settings)

        class ExplodingTool:
            name = "search_poi"

            async def run(self, payload):  # noqa: ANN001
                from app.tools.base import ToolResult

                return ToolResult(tool="search_poi", ok=False, error="模拟故障", degraded=True)

        registry._tools["search_poi"] = ExplodingTool()
        state = _state("乌镇附近有什么景点？")
        await node(state, _trace(state.query))

        assert state.tool_unavailable is True
        assert state.hits == []
    finally:
        get_settings.cache_clear()
