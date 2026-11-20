"""tool_call 节点：决定要不要用工具、用哪个、并把结果变成**可引用的证据**（阶段 3 W3-1/W3-2）。

## 三个设计决策

### 1. mock LLM 下**不发起 LLM 调用**，直接走规则

这不是偷懒，是必然：`MockLLM` 只会返回固定文本（它连 JSON 都不会真填），
如果让"工具选择"也走 LLM，20 条用例里 5 条负例会全部判错，准确率永远卡在 0.25，
出口条件（≥0.9）根本不可能达成 —— 而**离线模式是这个项目唯一的自查手段**。

所以规则选择不是"LLM 的降级方案"，它本身就是一条**必须存在的路径**：
真实 LLM 的输出要能被规则兜底（JSON 解析失败时），规则的结果也必须是可解释的。

### 2. 工具结果并入**统一的 `[n]` 编号序列**

工具查到的事实同样要满足引用率 100% 的红线。做法是把它包装成
`source_type="map_tool"` 的 `SearchHit` 追加到 `state.hits`，
让 `build_context` 照常编号（记忆 `[1][2]` + 工具 `[3][4]`）。

好处是 `utils/citation.py`、`self_check`、`context_builder` **三处零改动** ——
引用率的硬校验天然覆盖工具事实，不需要为工具单独造一套引用语法。

### 3. 上下文预算：工具证据必须活下来

`build_context` 按列表顺序截断，工具证据在末尾就会被先丢掉；
而模型如果仍然引用了 `[3]`，`self_check` 会判越界 → 拒答。
所以这里在追加后**主动为工具证据预留预算**：超预算时从记忆证据的**尾部**往回删，
而不是让工具证据被静默丢掉。顺序仍是"记忆在前"（PRD §5 的默认方案）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.observability import Trace
from app.agent.state import PipelineState
from app.core.config import Settings, get_settings
from app.core.feature_flags import is_enabled
from app.core.llm import BaseLLM, LLMMessage
from app.core.logging import get_logger
from app.schemas.document import DocumentRef, SearchHit
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

#: 路线类触发词。**顺序即优先级**：先判路线再判 POI，
#: 因为"从这里怎么走到最近的停车场"本质是在问路线，而不是在问停车场。
_ROUTE_PATTERN = re.compile(
    r"怎么走|怎么去|如何去|路线|导航|驾车|开车|自驾|公交|地铁|步行|骑行|"
    r"多远|多长|多少公里|多久|几分钟|几小时|到达|抵达|通勤"
)

#: POI 类触发词
_POI_PATTERN = re.compile(
    r"附近|周边|周围|旁边|有什么|哪里有|哪里可以|哪儿有|"
    r"景点|景区|停车场|加油站|充电桩|餐厅|美食|好吃|住宿|酒店|民宿|"
    r"门票|营业时间|几点关门|开放时间|地址在哪"
)


@dataclass
class ToolDecision:
    """一次工具调用决策。写进 trace，便于事后回答"为什么调了/没调这个工具"。"""

    tool: str | None = None
    source: str = "none"  # rule | llm | none
    reason: str = ""
    payload: dict = field(default_factory=dict)
    ok: bool = False
    error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    evidence_count: int = 0


def rule_select(query: str) -> tuple[str | None, str]:
    """规则版工具选择。返回 `(工具名 | None, 命中原因)`。

    **必须可解释**：返回命中原因而不是只返回名字 —— 排查"为什么调错了"时，
    "命中了'多远'"比"模型选了 route"有用得多。
    """
    text = (query or "").strip()
    if not text:
        return None, "空问题"

    route_hit = _ROUTE_PATTERN.search(text)
    if route_hit:
        return "calculate_route", f"命中路线词「{route_hit.group(0)}」"

    poi_hit = _POI_PATTERN.search(text)
    if poi_hit:
        return "search_poi", f"命中 POI 词「{poi_hit.group(0)}」"

    return None, "无地图类触发词（默认走记忆检索）"


class ToolCallNode:
    """工具调用节点。位置：`retrieve` 之后、`build_context` 之前。"""

    def __init__(
        self,
        llm: BaseLLM,
        registry: ToolRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- 入口
    async def __call__(self, state: PipelineState, trace: Trace) -> None:
        if not is_enabled("tools", self.settings):
            return

        registry = self._registry()
        if registry is None or len(registry) == 0:
            return

        async with trace.step("tool_call", {"query": state.search_query}) as step:
            decision = await self._decide(state, registry)
            if decision.tool is None:
                step.output = {"selected": None, "reason": decision.reason}
                state.tool_decision = decision
                return

            await self._execute(state, trace, registry, decision)
            state.tool_decision = decision
            step.output = {
                "selected": decision.tool,
                "source": decision.source,
                "reason": decision.reason,
                "ok": decision.ok,
                "cache_hit": decision.cache_hit,
                "evidence_count": decision.evidence_count,
            }

    # ---------------------------------------------------------------- 选择
    async def _decide(self, state: PipelineState, registry: ToolRegistry) -> ToolDecision:
        """选工具。真实 LLM 优先，失败/缺失一律落到规则。"""
        if getattr(self.llm, "provider", "") != "mock":
            llm_name = await self._llm_select(state, registry)
            if llm_name:
                return ToolDecision(tool=llm_name, source="llm", reason="LLM 选择")
            # 解析不出结果不报错：落规则，绝不因为"模型没答上来"就放弃工具

        name, reason = rule_select(state.search_query)
        return ToolDecision(tool=name, source="rule", reason=reason)

    async def _llm_select(self, state: PipelineState, registry: ToolRegistry) -> str | None:
        """让 LLM 选工具。JSON 解析失败返回 None（由调用方落规则）。"""
        from app.agent.prompts.loader import render

        try:
            prompt = render(
                "tool_select",
                question=state.search_query,
                tools=registry.describe(),
            )
            result = await self.llm.chat(
                [LLMMessage(role="user", content=prompt)],
                purpose="tool_select",
                json_mode=True,
            )
            payload = _parse_json(result.content)
        except Exception as exc:  # noqa: BLE001 - LLM 不可用时落规则，不能中断问答
            logger.info("LLM 工具选择失败，回落规则：%s", exc)
            return None

        if not isinstance(payload, dict):
            return None
        name = str(payload.get("tool") or "").strip()
        if name == "none" or not name:
            return None
        return name if registry.get(name) else None

    # ---------------------------------------------------------------- 执行
    async def _execute(
        self,
        state: PipelineState,
        trace: Trace,
        registry: ToolRegistry,
        decision: ToolDecision,
    ) -> None:
        tool = registry.get(decision.tool or "")
        if tool is None:  # pragma: no cover - 选择阶段已校验过
            decision.ok = False
            decision.error = f"未注册的工具：{decision.tool}"
            return

        decision.payload = await self._build_payload(state, decision.tool or "")
        result = await tool.run(decision.payload)

        # 每一行调用都必须落到 ToolCallLog：缓存命中率（≥60%）就靠这些行统计
        await trace.record_tool_call(
            tool.name,
            decision.payload,
            {"ok": result.ok, "error": result.error, "keys": sorted(result.data)},
            latency_ms=result.latency_ms,
            cache_hit=result.cache_hit,
        )
        decision.cache_hit = result.cache_hit
        decision.latency_ms = result.latency_ms

        if not result.ok:
            decision.ok = False
            decision.error = result.error
            # 降级：工具没帮上忙，但流程继续（记忆证据照常进上下文）
            state.tool_unavailable = True
            logger.info("工具 %s 不可用，降级走记忆：%s", tool.name, result.error)
            return

        hits = _hits_from_result(tool.name, result.data, state)
        if not hits:
            decision.ok = False
            decision.error = "工具未返回可用结果"
            state.tool_unavailable = True
            return

        decision.ok = True
        decision.evidence_count = len(hits)
        _append_with_budget(state, hits, max_chars=self.settings.context_max_chars)

    async def _build_payload(self, state: PipelineState, tool_name: str) -> dict:
        """从解析结果里拼工具入参。**不做语义猜测**：拿不到就给空值，让工具自己校验。"""
        parsed = state.parsed
        places = list(parsed.places) if parsed and not parsed.degraded else []
        keywords = list(parsed.keywords) if parsed else []

        if tool_name == "search_poi":
            keyword = (
                places[0] if places else (keywords[0] if keywords else _fallback_keyword(state))
            )
            return {"keyword": keyword, "city": places[1] if len(places) > 1 else "", "limit": 3}

        if tool_name == "calculate_route":
            coords = await self._resolve_coords(places[:2] or keywords[:2])
            if len(coords) < 2:
                # 凑不齐起终点就给一个能被 Pydantic 拒绝的载荷，
                # 走"入参非法"分支（不重试）—— 比瞎猜一组坐标诚实
                return {
                    "origin_lng_wgs84": 0,
                    "origin_lat_wgs84": 0,
                    "dest_lng_wgs84": 0,
                    "dest_lat_wgs84": 0,
                }
            return {
                "origin_lng_wgs84": coords[0][0],
                "origin_lat_wgs84": coords[0][1],
                "dest_lng_wgs84": coords[1][0],
                "dest_lat_wgs84": coords[1][1],
            }
        return {}

    async def _resolve_coords(self, names: list[str]) -> list[tuple[float, float]]:
        """地名 → 坐标。用 POI 搜索的结果当逆地编（mock 下同样成立）。"""
        from app.services.map_service import build_map_service

        if len(names) < 2:
            return []
        service = build_map_service(self.settings)
        coords: list[tuple[float, float]] = []
        for name in names:
            try:
                items = await service.search_poi(name, limit=1)
            except Exception as exc:  # noqa: BLE001 - 逆地编失败只是少一个端点
                logger.info("地名「%s」解析坐标失败：%s", name, exc)
                continue
            if items and items[0].location_wgs84:
                coords.append((items[0].location_wgs84.lng, items[0].location_wgs84.lat))
        return coords

    # ---------------------------------------------------------------- 内部
    def _registry(self) -> ToolRegistry | None:
        if self.registry is not None:
            return self.registry
        try:
            from app.tools.registry import build_default_registry

            self.registry = build_default_registry(
                ToolContext(
                    timeout_sec=self.settings.tool_timeout_sec,
                    max_retries=self.settings.tool_max_retries,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 工具装配失败不该拖垮问答
            logger.warning("工具注册表装配失败，本次不使用工具：%s", exc)
            return None
        return self.registry


def _describe_poi(item: dict) -> str:
    """没有现成描述时，用字段拼出一句可引用的事实句子。"""
    name = str(item.get("name") or "").strip()
    place = f"{item.get('city') or ''}{item.get('address') or ''}".strip()
    where = f"，位于{place}" if place else ""
    return f"{name}{where}；POI ID：{str(item.get('id') or '').strip()}。"


def _fallback_keyword(state: PipelineState) -> str:
    """兜底关键词：去掉疑问词后的整句。宁可宽一点，也不要空字符串。"""
    return re.sub(r"[？?。！!，,、\s]+", "", state.search_query)[:20] or "景点"


def _hits_from_result(tool_name: str, data: dict, state: PipelineState) -> list[SearchHit]:
    """工具结果 → SearchHit 列表。**每个 POI 一条证据**，这样引用粒度是"一个地点"。"""
    items = data.get("items") if isinstance(data, dict) else None
    top_score = max((hit.score for hit in state.hits), default=0.0)

    if items:
        hits: list[SearchHit] = []
        for item in items:
            poi_id = str(item.get("id") or "").strip()
            # 工具没给 text 时自己拼一句：宁可朴素，也不要因为少一个字段就把证据丢掉
            text = str(item.get("text") or _describe_poi(item))
            if not poi_id or not text:
                continue
            hits.append(
                SearchHit(
                    ref=DocumentRef(source_type="map_tool", source_id=poi_id),
                    content=text,
                    metadata={"source": "map_tool", "coord_system": "GCJ-02", "tool": tool_name},
                    # 与最好的记忆证据并列：见模块 docstring 第 3 点
                    score=round(top_score, 6),
                )
            )
        return hits

    # 路线类工具没有 items，用整段文字作为一条证据
    text = str((data or {}).get("text") or "")
    if not text:
        return []
    return [
        SearchHit(
            ref=DocumentRef(source_type="map_tool", source_id=f"{tool_name}:route"),
            content=text,
            metadata={"source": "map_tool", "coord_system": "GCJ-02", "tool": tool_name},
            score=round(top_score, 6),
        )
    ]


def _append_with_budget(
    state: PipelineState, tool_hits: list[SearchHit], *, max_chars: int
) -> None:
    """追加工具证据并为它预留预算。

    删谁、留谁是硬选择：**从记忆证据的尾部往回删**。
    尾部是分数最低的记忆证据，删它对答案的伤害最小；
    而工具证据一旦被删，模型仍会引用 `[n]` → 越界 → 拒答，代价是整个回答作废。
    """
    memory_hits = list(state.hits)
    tool_len = sum(len(hit.content) for hit in tool_hits)

    def total_len() -> int:
        return sum(len(hit.content) for hit in memory_hits) + tool_len

    while memory_hits and total_len() > max_chars:
        memory_hits.pop()

    state.hits = memory_hits + tool_hits


def _parse_json(text: str) -> object:
    """从 LLM 输出里抠出第一个 JSON 对象。失败返回 None（调用方落规则）。"""
    import json

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except Exception:  # noqa: BLE001
        return None
