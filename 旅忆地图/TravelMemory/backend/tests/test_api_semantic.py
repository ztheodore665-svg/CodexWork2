"""`POST /api/v1/recommend/semantic`（+ `/contribute`）的接口层测试。

与 `tests/test_api_privacy.py` 同一套做法：`TestClient(app)` 直接发请求，
不连数据库、不 skip。

本文件盯的是**接口语义**，而不是算法（算法在 `test_semantic_trajectory.py` 里）：

1. 开关关闭 → 404（能力不存在）
2. 未达 k-匿名门禁 → **200 + `tag=None`**（没有结论，不是请求非法）
3. 达标 → 200 + 预期标签，且**响应里没有 `support`**
4. 空序列 / 全非法标签 / 越界评分 → 422
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.semantic import SemanticRecommendResponse
from app.services.semantic_trajectory import (
    K_ANONYMITY,
    MAX_SEQUENCE_STEPS,
    build_aggregator,
)

client = TestClient(app)

_URL = "/api/v1/recommend/semantic"
_CONTRIBUTE_URL = "/api/v1/recommend/semantic/contribute"

#: 设计文档 §9.1 的"洛阳"抽象：人文之后去看自然
_SEQUENCE = ["heritage", "nature"]

#: 推荐响应的字段集合。**`support` 不在里面** —— 这条断言是响应契约的守门人。
_RESPONSE_FIELDS = {"tag", "confidence", "avg_score", "message", "k", "version"}


@pytest.fixture(autouse=True)
def fresh_aggregator():
    """每个用例都从空的模式库开始。

    必须 autouse：聚合器是**模块级单例**，漏掉这一个用例就会让下一条
    "support=4 未达标"变成"support=9 已达标"的假绿 —— 而门禁是本阶段最要紧的红线。
    """
    build_aggregator().clear()
    yield
    build_aggregator().clear()


@pytest.fixture
def semantic_disabled(monkeypatch: pytest.MonkeyPatch):
    """开关显式关掉（同时屏蔽本机 `.env` 的影响）—— 断言考的是"默认关闭"这件事本身。"""
    monkeypatch.setenv("SEMANTIC_ENABLED", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def semantic_enabled(monkeypatch: pytest.MonkeyPatch):
    """开启 `semantic` 开关，用完清掉 `get_settings` 的 lru_cache。"""
    monkeypatch.setenv("SEMANTIC_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _contribute(sequence: list[str], *, score: float | None = None, user_key: str | None = None):
    """上报一条序列（每个贡献者用不同的 `user_key`，模拟真实的多人匿名集）。"""
    payload: dict[str, object] = {"sequence": sequence}
    if score is not None:
        payload["score"] = score
    if user_key is not None:
        payload["user_key"] = user_key
    return client.post(_CONTRIBUTE_URL, json=payload)


def _contribute_many(count: int) -> None:
    """`count` 个**不同**贡献者上报同一条序列。"""
    for index in range(count):
        resp = _contribute(_SEQUENCE, score=4.5, user_key=f"anon-{index}")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- 路由与开关


def test_routes_are_registered() -> None:
    """两条路由都要真的挂上：它们不会因为任何单测失败而自动显形。"""
    paths = app.openapi()["paths"]
    assert _URL in paths, f"推荐路由没挂上：{list(paths)}"
    assert _CONTRIBUTE_URL in paths, f"上报路由没挂上：{list(paths)}"
    assert "post" in paths[_URL]
    assert "post" in paths[_CONTRIBUTE_URL]


@pytest.mark.parametrize("url", [_URL, _CONTRIBUTE_URL])
def test_disabled_flag_returns_404(semantic_disabled, url: str) -> None:
    """未启用 → **404**（能力不存在），不是 403（存在但没权限）。"""
    assert client.post(url, json={"sequence": _SEQUENCE}).status_code == 404


def test_enabled_flag_accepts_requests(semantic_enabled) -> None:
    """开启后不再是 404（"能力存在"这件事本身要有用例盯着）。"""
    assert client.post(_URL, json={"sequence": _SEQUENCE}).status_code == 200


# ---------------------------------------------------------------- k-匿名门禁的接口语义


def test_below_threshold_returns_200_without_tag(semantic_enabled) -> None:
    """**核心语义**：未达门禁是"没有结论" → **200 + `tag=None`**，不是 404/422。"""
    _contribute_many(K_ANONYMITY - 1)

    resp = client.post(_URL, json={"sequence": ["heritage"]})
    assert resp.status_code == 200, "门禁未过不该报错：那是「没有结论」而不是「请求非法」"
    body = resp.json()
    assert body["tag"] is None
    assert "暂无足够匿名模式支撑" in body["message"]
    assert body["k"] == K_ANONYMITY  # 回显阈值，让前端能解释为什么没结果
    assert body["confidence"] == 0.0
    assert body["avg_score"] is None


def test_at_threshold_returns_the_expected_tag(semantic_enabled) -> None:
    """补上第 `k` 条后放行：推荐出 `nature`，`message` 也随之切换。"""
    _contribute_many(K_ANONYMITY - 1)
    assert client.post(_URL, json={"sequence": ["heritage"]}).json()["tag"] is None

    _contribute(_SEQUENCE, score=4.5, user_key=f"anon-{K_ANONYMITY - 1}")

    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert body["tag"] == "nature"
    assert body["confidence"] > 0.0
    assert body["avg_score"] == pytest.approx(4.5)
    assert "暂无足够匿名模式支撑" not in body["message"]


def test_gate_blocks_at_four_and_releases_at_five(semantic_enabled) -> None:
    """端到端复演门禁：**4 条 → 空，补到第 5 条 → 出标签**（本阶段最要紧的一条红线）。

    与上面两条用例的区别是"**一次连续的过程**"：从零开始一条条灌进去，
    在第 k-1 条与第 k 条之间反复查同一个接口，断言结论**恰好在第 5 条翻转**。

    为什么值得单独一条：前两条各测一个点，而"门禁恰好卡在 5"这件事
    只有把整条曲线走一遍才成立 —— 万一阈值被改成 4 或 6，只测单点可能都还是绿的
    （比如改成了 4，`support=4` 那条仍然返回 None，只有"第 4 条就该放行"才红）。
    """
    # 前 4 条：每一条之后都必须还是"没有结论"
    for index in range(K_ANONYMITY - 1):
        _contribute(_SEQUENCE, score=4.5, user_key=f"anon-{index}")
        body = client.post(_URL, json={"sequence": ["heritage"]}).json()
        assert body["tag"] is None, f"第 {index + 1} 条就放行了 —— 门禁阈值不对"
        assert "暂无足够匿名模式支撑" in body["message"]

    # 第 5 条：翻转
    _contribute(_SEQUENCE, score=4.5, user_key=f"anon-{K_ANONYMITY - 1}")
    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert body["tag"] == "nature"


def test_other_prefixes_stay_blocked_while_only_one_is_published(semantic_enabled) -> None:
    """**门禁是逐模式的**：一条模式达标不会顺带把别的模式放出来。

    把门禁实现成"全局样本量 >= k 就放行"是最容易犯的错：
    那样只要全站累计到 5 条上报，任何一条 support=1 的模式都会被当共识发出去 ——
    而 support=1 意味着"有一个人这么走过"，把它公开出去正是 §9 要防的。
    """
    for index in range(K_ANONYMITY):
        _contribute(_SEQUENCE, score=4.5, user_key=f"anon-{index}")
    # 只有 2 个人走过 heritage → food
    _contribute(["heritage", "food"], score=4.5, user_key="food-0")
    _contribute(["heritage", "food"], score=4.5, user_key="food-1")

    assert client.post(_URL, json={"sequence": ["heritage"]}).json()["tag"] == "nature"
    # 长前缀 support 只有 2，仍须拦住
    assert client.post(_URL, json={"sequence": ["watertown"]}).json()["tag"] is None


def test_response_never_leaks_exact_support(semantic_enabled) -> None:
    """**隐私红线**：响应体里没有 `support`（参与人数），只有相对占比 `confidence`。

    断言方式刻意做成"字段集合精确相等"：将来有人往 schema 里加一个
    `support` 或 `count` 字段，这里会立刻红，而不是安静地把人数发出去。
    """
    _contribute_many(K_ANONYMITY)

    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert set(body) == _RESPONSE_FIELDS
    assert "support" not in body
    assert "count" not in body
    # 达标的唯一候选 → 占比 1.0，但**它不等于 support=1**
    assert body["confidence"] == pytest.approx(1.0)
    assert body["confidence"] != K_ANONYMITY


def test_confidence_is_a_ratio_between_candidates(semantic_enabled) -> None:
    """有多条走向时 `confidence` 是相对占比，仍然不暴露任何人数。"""
    for index in range(K_ANONYMITY):
        _contribute(_SEQUENCE, score=4.5, user_key=f"nature-{index}")
    for index in range(K_ANONYMITY):
        _contribute(["heritage", "food"], score=4.5, user_key=f"food-{index}")

    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert body["tag"] == "nature"  # 平票 → 按 SEMANTIC_TAGS 顺序，nature 在 food 前
    assert body["confidence"] == pytest.approx(0.5)


def test_same_contributor_cannot_push_a_pattern_over_the_gate(semantic_enabled) -> None:
    """接口层复演 unique 去重：一个人刷 k 次也刷不出一条"群体模式"。"""
    for _ in range(K_ANONYMITY):
        assert _contribute(_SEQUENCE, user_key="single-user").status_code == 200

    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert body["tag"] is None
    assert "暂无足够匿名模式支撑" in body["message"]


# ---------------------------------------------------------------- 上报接口


def test_contribute_returns_accepted_count(semantic_enabled) -> None:
    """上报回执：`accepted` > 0，且 `patterns` / `published` 反映聚合器当前状态。"""
    resp = _contribute(_SEQUENCE, score=4.5, user_key="anon-0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] > 0
    assert body["patterns"] > 0
    assert body["published"] == 0  # 只有一条，离门禁还差 4 条


def test_contribute_publishes_after_k_distinct_users(semantic_enabled) -> None:
    """第 `k` 个不同贡献者之后，`published` 计数才变成 1（门禁与接口回执同源）。"""
    for index in range(K_ANONYMITY - 1):
        assert _contribute(_SEQUENCE, user_key=f"anon-{index}").json()["published"] == 0
    assert _contribute(_SEQUENCE, user_key="anon-last").json()["published"] == 1


def test_repeated_contribution_is_accepted_but_not_counted(semantic_enabled) -> None:
    """重复上报：HTTP 仍然 200（收到了），但 `accepted = 0`（没采纳）。"""
    assert _contribute(_SEQUENCE, user_key="alice").json()["accepted"] == 1
    assert _contribute(_SEQUENCE, user_key="alice").json()["accepted"] == 0


# ---------------------------------------------------------------- 422：请求非法


def test_empty_sequence_returns_422(semantic_enabled) -> None:
    """空序列既没法推荐也没法学 —— 请求非法（422），不是"数据不足"（200）。"""
    assert client.post(_URL, json={"sequence": []}).status_code == 422
    assert client.post(_CONTRIBUTE_URL, json={"sequence": []}).status_code == 422


def test_all_invalid_tags_returns_422(semantic_enabled) -> None:
    """非法标签是静默丢弃；**全部丢光**就等价于空序列 → 422。"""
    body = {"sequence": ["not-a-tag", "龙门石窟"]}
    assert client.post(_URL, json=body).status_code == 422
    assert client.post(_CONTRIBUTE_URL, json=body).status_code == 422


def test_invalid_tags_are_dropped_not_rejected(semantic_enabled) -> None:
    """混进一个脏标签不该让整条上报失败 —— 丢掉它，剩下的照常处理。"""
    resp = _contribute(["heritage", "not-a-tag", "nature"], user_key="anon-0")
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1


@pytest.mark.parametrize("score", [5.1, -0.1, 100.0])
def test_out_of_range_score_returns_422(semantic_enabled, score: float) -> None:
    """评分必须在 0~5：`Field(ge=0, le=5)` 统一转成 422。"""
    body = {"sequence": _SEQUENCE, "score": score}
    assert client.post(_URL, json=body).status_code == 422
    assert client.post(_CONTRIBUTE_URL, json=body).status_code == 422


@pytest.mark.parametrize("score", [0.0, 4.0, 5.0])
def test_valid_score_boundaries_are_accepted(semantic_enabled, score: float) -> None:
    """边界值 0 / MIN_AVG_SCORE / 5 都合法（区间是闭的）。"""
    assert client.post(_CONTRIBUTE_URL, json={"sequence": _SEQUENCE, "score": score}).status_code == 200


def test_too_long_sequence_returns_422(semantic_enabled) -> None:
    """超过 `MAX_SEQUENCE_STEPS` 步的序列由 schema 拦在 422：超长历史本身就是指纹。"""
    body = {"sequence": ["heritage"] * (MAX_SEQUENCE_STEPS + 1)}
    assert client.post(_URL, json=body).status_code == 422
    assert client.post(_CONTRIBUTE_URL, json=body).status_code == 422


def test_too_long_user_key_returns_422(semantic_enabled) -> None:
    body = {"sequence": _SEQUENCE, "user_key": "x" * 65}
    assert client.post(_CONTRIBUTE_URL, json=body).status_code == 422


def test_missing_sequence_returns_422(semantic_enabled) -> None:
    assert client.post(_URL, json={}).status_code == 422


def test_error_shape_has_the_fastapi_detail_field(semantic_enabled) -> None:
    """错误响应体用 FastAPI 标准的 `detail`，前端按一个地方解析即可。"""
    assert "detail" in client.post(_URL, json={"sequence": []}).json()


def test_response_model_fields_match_the_schema(semantic_enabled) -> None:
    """成功路径的字段集合与 pydantic 模型一致（防 `response_model` 被写漏）。"""
    _contribute_many(K_ANONYMITY)
    body = client.post(_URL, json={"sequence": ["heritage"]}).json()
    assert set(body) == set(SemanticRecommendResponse.model_fields)
