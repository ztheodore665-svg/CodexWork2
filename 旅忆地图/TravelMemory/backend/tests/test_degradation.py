"""降级守护（O-1 / PROJECT.md 第 8 条 / 约束 E3）。

**这是扩展代码合入 main 的前提**（PROJECT.md §三）：
所有 feature flag 关闭时，主流程行为必须与**上一阶段**逐条一致。

本文件断言的几件事，正是后面各个批次各自会撬动的地方：

| 断言 | 守护谁 | 被谁可能打破 |
| --- | --- | --- |
| 证据顺序 = 阶段 2 快照 | 检索层 | W3-4 的 reranker / HNSW（关闭时必须 no-op、必须不换序列化 != 换排序） |
| Recall@5 / MRR 不退化 | 检索质量 | 同上 + 任何"顺手优化"融合权重的行为 |
| 引用率 1.00 + 拒答路径 | 生成与自检 | W3-2 的工具证据并入 `[n]`（编号只能续在后，不能打乱口径） |
| 关闭缓存后输出完全不变 | 缓存层 | W3-3 的四级缓存接入（读路径不得改变答案） |
| 阶段 6 四开关全关的前提下：Recall@5 / MRR / 证据顺序 / 引用率 / 拒答路径逐条不变 | 阶段 1–5 的全部主流程 | W6-1 照片导入、W6-2 行程聚类、W6-3 端能力抽象、W6-4 地理模糊 —— 四条旁路合进来后，"没开"必须等价于"没装" |
| import 阶段 6 模块前后主流程输出**逐字一致** | 全局状态 | 新模块在 import 期做副作用（改 settings 单例、注册钩子、全局 patch）会让"全关"这个前提悄悄失真 |
| `/api/v1/privacy/obfuscate` 关闭时 404 | 阶段 6 旁路的对外可见性 | 路由无条件下挂（关着的旁路不该出现在 OpenAPI 的行为里），或把 404 写成 403 —— 403 等于先把攻击面画在地图上 |
| 阶段 6 四开关的**出厂默认**是 False | 上述所有前提的根 | `core/config.py` 里默认值被误改成 True（`tests/test_feature_flags.py` 之外的第二道目击者） |

## "指标不退化"为什么不需要再写一份快照

动态生成快照会把"当次运行的结果"当成期望值，任何回归都会被自己 bless 掉
（这是下面 `STAGE2_CASES` 硬编码的原因）。但阶段 6 要守护的不是某个具体数字，
而是**同一个进程里"装了扩展"与"没装扩展"两次运行之间的关系**：

`test_stage6_import_has_no_side_effect_on_main_flow` 因此先跑一遍主流程、**当场**
采下指纹，import 三个新模块之后在同一前提下再跑一遍，要求两次结果逐字相等；
指纹的正确性则由它自身与 `STAGE2_CASES` 的对账（Recall@5 / MRR / 证据前缀）钉住 ——
这样就既不会 bless 回归，也不需要维护第二份快照。

快照为什么硬编码在这里而不动态生成：动态快照会把"当次运行的结果"当成期望值，
任何回归都会被自己 bless 掉。宁可在语料变更时手工更新一次 —— 那次更新本身就是一次评审。

**本文件禁止新增 skip**（O-5）：全部用例走 mock LLM + hashing embedding + 内存检索，离线可跑。
阶段 6 的用例同样如此：`/privacy/obfuscate` 用 `TestClient` 直连 app 实例，不连库、不发真实请求。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import pytest
from fakes import InMemoryVectorSearch, load_demo_corpus
from fastapi.testclient import TestClient

from app.agent.eval.metrics import mrr, recall_at_k
from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.core.cache import InMemoryCache, NullCache, build_cache
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.feature_flags import (
    STAGE3_FLAG_NAMES,
    STAGE6_FLAG_NAMES,
    all_flags,
    clean_settings,
    is_enabled,
    stage3_flags,
    stage6_all_disabled,
    stage6_flags,
)
from app.core.llm import build_llm
from app.main import app
from app.utils.citation import citation_rate, out_of_range_indices

#: 记忆类证据的来源类型（工具证据一旦混进来，source_type 会是 `map_tool`）
MEMORY_SOURCE_TYPES = frozenset({"trip", "place", "photo", "note"})

#: 阶段 6 四个旁路的实现入口。"it's not installed unless imported" ——
#: 退化守护要证明的是**它们被 import 进来之后**主流程仍然不变。
STAGE6_MODULES = (
    "app.services.photo_import",
    "app.services.trip_cluster",
    "app.privacy.obfuscate",
)

#: `/api/v1/privacy/obfuscate` 的路径与一组合法入参（字段口径见 `app/schemas/privacy.py`）
PRIVACY_OBFUSCATE_URL = "/api/v1/privacy/obfuscate"
_PRIVACY_BODY: dict[str, Any] = {"lat": 30.2422, "lng": 120.1494, "mode": "approx", "epsilon": 1.0}

#: 无证据的问题：任何阶段都必须走拒答而不是硬凑答案
REFUSAL_QUERY = "我攀登珠峰用了几天？"


@dataclass(frozen=True)
class Stage2Case:
    """一条阶段 2 快照用例：`expected_prefix` 管顺序，`expected_keys` 管召回。"""

    query: str
    #: 前若干条证据的 `[n]` 编号顺序（含 source_type，工具证据不得插队）
    expected_prefix: tuple[str, ...]
    #: Recall@5 期望命中的 key：`(source_type, source_id, chunk_index)`
    expected_keys: tuple[tuple[str, str, int], ...]


STAGE2_CASES = (
    Stage2Case(
        query="乌镇 西塘 摇橹船 夜景",
        expected_prefix=(
            "place/p10#0",
            "trip/t1#0",
            "place/p20#0",
            "trip/t5#0",
            "place/p11#0",
        ),
        expected_keys=(
            ("place", "p10", 0),
            ("trip", "t1", 0),
            ("place", "p20", 0),
        ),
    ),
    Stage2Case(
        query="我去过哪些爬山看云海的地方？",
        expected_prefix=(
            "trip/t3#0",
            "place/p14#0",
            "trip/t2#0",
            "trip/t6#0",
            "trip/t4#0",
        ),
        expected_keys=(
            ("trip", "t3", 0),
            ("place", "p14", 0),
            ("trip", "t2", 0),
        ),
    ),
)


@pytest.fixture
async def store(user_id) -> InMemoryVectorSearch:
    """演示语料 + 本地哈希向量 + 内存检索（与 `test_pipeline_mock.py` 同一套替身）。"""
    return await load_demo_corpus(user_id, build_embedder(get_settings()))


@pytest.fixture
def stage3_off(monkeypatch) -> dict[str, bool]:
    """把阶段 3 四个开关**显式**关成 false，并把缓存切到 NullCache。

    为什么显式而不是依赖默认值：本守护回答的是"开关关着的时候行为等不等同阶段 2"；
    **默认值是多少**是另一个问题，由 `tests/test_feature_flags.py` 断言。
    两个问题分开，出红时才知道是"默认值被改"还是"关闭路径被写坏"。
    """
    for name in STAGE3_FLAG_NAMES:
        monkeypatch.setenv(f"{name.upper()}_ENABLED", "false")
    monkeypatch.setenv("CACHE_BACKEND", "null")
    get_settings.cache_clear()
    yield stage3_flags()
    get_settings.cache_clear()


@pytest.fixture
def stage6_off(monkeypatch) -> dict[str, bool]:
    """把阶段 6 已接线的开关**显式**关成 false，并清空 `AMAP_API_KEY`。

    显式关 false 的理由同 `stage3_off`：本守护回答的是"开关关着时行为等不等同阶段 5"，
    **默认值是多少**由 `test_stage6_flags_default_to_disabled` 单独断言。

    额外清 `AMAP_API_KEY` 是因为 W6-2 的地图相册与 W3-1 的地图工具都以"有没有配高德 key"
    作为走真实 API 还是 mock 的依据：留着本机 `.env` 里的 key，等于让"开关全关"的
    用例悄悄依赖了外部服务 —— 而这恰恰是 `禁止调用真实高德 API` 这条铁律要挡的事。
    """
    for name in STAGE6_FLAG_NAMES:
        monkeypatch.setenv(f"{name.upper()}_ENABLED", "false")
    monkeypatch.setenv("AMAP_API_KEY", "")
    get_settings.cache_clear()
    yield stage6_flags()
    get_settings.cache_clear()


def _build_pipeline(store: InMemoryVectorSearch, *, min_score: float) -> AgentPipeline:
    settings = get_settings()
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, RetrieverConfig(top_k=5, min_score=min_score))
    return AgentPipeline(build_llm(settings), retriever, max_context_chars=2000, settings=settings)


async def _run(pipeline: AgentPipeline, user_id, query: str):
    trace = Trace(session=None, user_id=user_id, session_id="degradation", query=query)
    return await pipeline.run(user_id=user_id, session_id="degradation", query=query, trace=trace)


def _fingerprint(state, expected_keys: list[tuple], expected_prefix: tuple[str, ...]) -> dict:
    """把一次运行压成一个可**逐字**比较的字典（从检索到生成的所有可观测量）。

    只比 recall / mrr 两个标量是不够的：它们对第 2~5 条之间的内部换序完全不敏感。
    所以指纹里同时塞进 `hits`（检索侧完整顺序）、`blocks`（进上下文的顺序）、
    `answer` 与 `citations`（生成侧逐字输出），任何一个字节变了都会不相等。
    """
    keys = [hit.ref.key for hit in state.hits]
    return {
        "hits": tuple(keys),
        "blocks": tuple(str(block.ref) for block in state.blocks),
        "answer": state.answer,
        "citations": tuple(str(ref) for ref in state.citations),
        "refused": state.refused,
        "refuse_reason": state.refuse_reason,
        "recall_at_5": recall_at_k(keys, expected_keys, 5),
        "mrr": mrr(keys, expected_keys),
        "prefix": tuple(str(block.ref) for block in state.blocks)[: len(expected_prefix)],
    }


async def _measure_main_flow(store: InMemoryVectorSearch, user_id) -> dict[str, dict]:
    """在**当前**全局状态下把阶段 1–5 的主流程完整跑一遍，返回指纹字典。

    覆盖两路：两条快照问答（检索 + 生成 + 引用）与一条无证据拒答（安全路径）。
    """
    pipeline = _build_pipeline(store, min_score=0.05)
    measured: dict[str, dict] = {}
    for case in STAGE2_CASES:
        state = await _run(pipeline, user_id, case.query)
        measured[case.query] = _fingerprint(state, list(case.expected_keys), case.expected_prefix)

    refusal = await _run(_build_pipeline(store, min_score=0.99), user_id, REFUSAL_QUERY)
    measured[REFUSAL_QUERY] = _fingerprint(refusal, [], ())
    return measured


# ---------------------------------------------------------------- 前提
def test_degradation_premise_is_all_flags_off(stage3_off):
    """守护的前提本身要先被断言：四个开关全关 + 缓存不参与。"""
    assert stage3_off == {"tools": False, "cache": False, "rerank": False, "hnsw": False}
    assert isinstance(build_cache(), NullCache), "降级模式下缓存必须是直通实现"


# ---------------------------------------------------------------- 检索指标
@pytest.mark.parametrize("case", STAGE2_CASES, ids=lambda case: case.query)
async def test_retrieval_metrics_match_stage2_snapshot(store, user_id, case, stage3_off):
    """Recall@5 / MRR / 证据顺序 = 阶段 2 快照。

    三条断言各有分工，缺一条就可能被"看起来还是对的"骗过：
      - Recall@5 == 1.0：预期证据都没丢（顺序乱了也能过）
      - MRR == 1.0：第一条就命中（顺序乱了会掉下来）
      - 前缀相等：**逐条**顺序完全一致（前两条是它的交集，看不出中间插入）
    """
    pipeline = _build_pipeline(store, min_score=0.05)

    state = await _run(pipeline, user_id, case.query)

    keys = [hit.ref.key for hit in state.hits]
    assert recall_at_k(keys, list(case.expected_keys), 5) == 1.0, (
        f"Recall@5 退化：期望 {case.expected_keys}，实际 {keys}"
    )
    assert mrr(keys, list(case.expected_keys)) == 1.0, f"MRR 退化：实际顺序 {keys}"

    block_keys = [str(block.ref) for block in state.blocks]
    width = len(case.expected_prefix)
    assert tuple(block_keys[:width]) == case.expected_prefix, (
        f"证据顺序相比阶段 2 发生变化：\n  期望 {case.expected_prefix}\n  实际 {tuple(block_keys[:width])}"
    )


# ---------------------------------------------------------------- 生成与自检
async def test_answer_keeps_full_citation_rate(store, user_id, stage3_off):
    """引用率 100% 是硬指标（PROJECT.md §二、[3]）：开关全关时也必须稳在 1.00。"""
    pipeline = _build_pipeline(store, min_score=0.05)

    state = await _run(pipeline, user_id, "乌镇 西塘 摇橹船 夜景")

    assert state.refused is False, f"不该拒答：{state.answer}"
    assert state.citations, "答案必须带引用"
    assert citation_rate(state.answer) == 1.0, f"引用率退化：{state.answer}"
    # 引用不得越界：编号只能来自真实存在的证据块（工具证据将来并入时这条同样生效）
    assert out_of_range_indices(state.answer, len(state.blocks)) == []


async def test_refusal_path_still_triggers(store, user_id, stage3_off):
    """无证据必须拒答：这条路径一旦被工具证据"补上"而失效，引用率红线就守不住了。"""
    pipeline = _build_pipeline(store, min_score=0.99)

    state = await _run(pipeline, user_id, REFUSAL_QUERY)

    assert state.refused is True
    assert state.refuse_reason == "no_evidence"
    assert state.citations == []
    assert citation_rate(state.answer) == 1.0, "拒答话术里不该出现事实句"


async def test_no_map_tool_evidence_when_tools_disabled(store, user_id, stage3_off):
    """tools 关 → 证据里不允许出现 `map_tool`，且编号必须从 1 连续递增。"""
    pipeline = _build_pipeline(store, min_score=0.05)

    state = await _run(pipeline, user_id, "乌镇 西塘 摇橹船 夜景")

    sources = {block.ref.source_type for block in state.blocks}
    assert sources <= MEMORY_SOURCE_TYPES, f"出现了非记忆类证据: {sources - MEMORY_SOURCE_TYPES}"
    assert [block.index for block in state.blocks] == list(range(1, len(state.blocks) + 1))


# ---------------------------------------------------------------- 缓存
async def test_disabled_cache_does_not_change_output(store, user_id, stage3_off, stage6_off):
    """缓存层合进来之后：开关关着时，有无缓存的答案必须**逐字相同**。

    这条是 W3-3 的"旁路设施不得改变主行为"的契约 —— 缓存只能改延迟，不能改答案。
    """
    pipeline = _build_pipeline(store, min_score=0.05)
    query = "乌镇 西塘 摇橹船 夜景"

    first = await _run(pipeline, user_id, query)
    second = await _run(pipeline, user_id, query)

    assert first.answer == second.answer
    assert [str(ref) for ref in first.citations] == [str(ref) for ref in second.citations]

    # 直通实现的世界里：写进去的东西一律读不回来（这正是"没缓存"的可观测形态）
    nothing = build_cache()
    await nothing.set("api:poi:whatever", "cached", 60)
    assert await nothing.get("api:poi:whatever") is None
    assert not isinstance(nothing, InMemoryCache)


# ================================================================ 阶段 6：四个新开关
def test_stage6_premise_is_all_flags_off(stage6_off):
    """阶段 6 守护的前提也必须先被断言：四开关全关 + 没有高德 key 可依赖。"""
    assert stage6_off == dict.fromkeys(STAGE6_FLAG_NAMES, False)
    assert stage6_all_disabled() is True, "阶段 6 已接线的开关必须全部关闭"
    assert get_settings().amap_api_key == "", "没配高德 key 时地图能力必须走 mock"


def test_stage6_flags_default_to_disabled():
    """出厂默认值：四个阶段 6 开关一律 False。

    用 `clean_settings()` 而不是 `get_settings()`：本机 `.env` 里写了
    `PRIVACY_ENABLED=true` 也能让它"看起来是关的" —— 那台机器绿了，
    产品默认值已经被改坏却没人知道（假绿是最贵的那种失败）。
    """
    settings = clean_settings()

    for name in STAGE6_FLAG_NAMES:
        assert is_enabled(name, settings) is False, f"{name} 的出厂默认必须是 False"
    assert stage6_flags(settings) == dict.fromkeys(STAGE6_FLAG_NAMES, False)
    assert stage6_all_disabled(settings) is True
    assert settings.amap_api_key == "", "高德 key 不该有默认值"


def test_privacy_endpoint_is_absent_when_flag_off(stage6_off):
    """`privacy` 开关关着时这个接口必须表现为**不存在**（404），而不是"没权限"（403）。

    三条断言是一条完整的因果链，少一条这条用例就会假绿：
      - 路由确实挂着（否则 404 来自"压根没实现"，而不是"开关关着"）
      - 状态码 404
      - `detail` 不是 FastAPI 的通用 "Not Found"（把业务闸门与路由缺失区分开）

    403 与 404 的取舍写在 `app/api/v1/privacy.py` 的模块 docstring 里：
    403 会告诉外部"这里确有此功能"，等于先把攻击面画在地图上。
    """
    assert PRIVACY_OBFUSCATE_URL in app.openapi()["paths"], (
        f"路由没挂上，404 将来自缺路由而不是开关：{list(app.openapi()['paths'])}"
    )

    response = TestClient(app).post(PRIVACY_OBFUSCATE_URL, json=dict(_PRIVACY_BODY))

    assert response.status_code == 404, f"应当 404（能力不存在），实际 {response.status_code}"
    detail = response.json().get("detail")
    assert detail not in (None, "Not Found"), (
        f"这个 404 来自路由缺失而不是 `privacy` 开关闸门：{response.text}"
    )


# ---------------------------------------------------------------- 阶段 6：核心指标不退化
@pytest.mark.parametrize("case", STAGE2_CASES, ids=lambda case: case.query)
async def test_stage6_retrieval_metrics_match_stage2_snapshot(
    store, user_id, case, stage3_off, stage6_off
):
    """阶段 6 四开关全关 → Recall@5 / MRR / 证据顺序仍然等于阶段 2 快照。

    为什么有了上面那条同名阶段 3 用例还要再写一遍：两条用例的前提是**不同的开关集**。
    上面那条只保证"阶段 3 四个开关全关"，它完全可以在阶段 6 开关开着的情况下绿着。
    W6-1~W6-4 合进来之后要盯的正是**新增的这四个**有没有撬动检索，
    所以这里把 `stage3_off` 与 `stage6_off` 一起挂上，出红时归因范围锁定在这两个批次。
    """
    pipeline = _build_pipeline(store, min_score=0.05)

    state = await _run(pipeline, user_id, case.query)

    keys = [hit.ref.key for hit in state.hits]
    assert recall_at_k(keys, list(case.expected_keys), 5) == 1.0, (
        f"阶段 6 开关全关时 Recall@5 退化：期望 {case.expected_keys}，实际 {keys}"
    )
    assert mrr(keys, list(case.expected_keys)) == 1.0, f"阶段 6 开关全关时 MRR 退化：实际 {keys}"

    block_keys = tuple(str(block.ref) for block in state.blocks)
    width = len(case.expected_prefix)
    assert block_keys[:width] == case.expected_prefix, (
        f"阶段 6 开关全关时证据顺序发生变化：\n"
        f"  期望 {case.expected_prefix}\n  实际 {block_keys[:width]}"
    )
    # 阶段 6 没有自己的证据类型：照片 / 行程聚类一旦混进主检索会出现新 source_type
    sources = {block.ref.source_type for block in state.blocks}
    assert sources <= MEMORY_SOURCE_TYPES, f"出现了非记忆类证据: {sources - MEMORY_SOURCE_TYPES}"


async def test_stage6_answer_keeps_full_citation_rate(store, user_id, stage3_off, stage6_off):
    """引用率 1.00 是红线：阶段 6 四个开关全关时也必须稳在满值，且引用不越界。"""
    pipeline = _build_pipeline(store, min_score=0.05)

    state = await _run(pipeline, user_id, "乌镇 西塘 摇橹船 夜景")

    assert state.refused is False, f"不该拒答：{state.answer}"
    assert state.citations, "答案必须带引用"
    assert citation_rate(state.answer) == 1.0, f"阶段 6 开关全关时引用率退化：{state.answer}"
    assert out_of_range_indices(state.answer, len(state.blocks)) == []


async def test_stage6_refusal_path_still_triggers(store, user_id, stage3_off, stage6_off):
    """无证据仍必须拒答（`refuse_reason == "no_evidence"`）。

    这条盯的是"旁路把空白补上"那条退化路：照片导入 / 行程聚类一旦凭着 EXIF 坐标
    把本来找不到证据的问题"补"出了答案，拒答路径就静默失效了，
    而引用率 1.00 这条红线正是靠拒答兜底才守得住的。
    """
    pipeline = _build_pipeline(store, min_score=0.99)

    state = await _run(pipeline, user_id, REFUSAL_QUERY)

    assert state.refused is True
    assert state.refuse_reason == "no_evidence"
    assert state.citations == []
    assert citation_rate(state.answer) == 1.0, "拒答话术里不该出现事实句"


async def test_stage6_import_has_no_side_effect_on_main_flow(store, user_id, stage3_off, stage6_off):
    """阶段 6 的模块被 import 进来之后，主流程输出必须**逐字不变**。

    这是全文件最硬的一条：前面的用例只证明"开关关着时代码没走到新分支"，
    而这条证明的是**把新代码装进内存本身**也不许改变主行为 ——
    守的是 import 期的副作用：

      - 模块顶层调了 `reload_settings()` / `get_settings.cache_clear()`（配置被重读）
      - 模块顶层注册了全局钩子、补丁、也一样改变了分数线

    证明手法是**自比较**：先在当前状态下跑一遍主流程采指纹，import 三个模块，
    再在同一前提下跑第二遍，两次指纹必须完全相等。
    不需要新增硬编码快照 —— 指纹的正确性由它与 `STAGE2_CASES` 的对账钉住：
    若两遍"一致地错"，下面第一行这组断言会先红。
    """
    before = await _measure_main_flow(store, user_id)
    flags_before = all_flags()
    settings_before = get_settings()

    # 先把基线本身核对一遍：不让"前后一致"变成"前后一致地错"
    for case in STAGE2_CASES:
        baseline = before[case.query]
        assert baseline["recall_at_5"] == 1.0, f"基线 Recall@5 就不是 1.0：{baseline['hits']}"
        assert baseline["mrr"] == 1.0, f"基线 MRR 就不是 1.0：{baseline['hits']}"
        assert baseline["prefix"] == case.expected_prefix, (
            f"基线证据顺序已偏离阶段 2 快照：{baseline['prefix']}"
        )
    assert before[REFUSAL_QUERY]["refuse_reason"] == "no_evidence"

    for module in STAGE6_MODULES:
        importlib.import_module(module)

    after = await _measure_main_flow(store, user_id)

    assert after == before, (
        "阶段 6 模块被 import 之后主流程输出发生了变化：\n"
        f"  之前 {before}\n  之后 {after}"
    )
    assert all_flags() == flags_before, f"import 阶段 6 模块改动了开关状态：{all_flags()}"
    assert get_settings() is settings_before, "import 阶段 6 模块重置了 settings 单例"
    assert get_settings().amap_api_key == "", "import 阶段 6 模块带回了高德 key"

