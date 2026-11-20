"""W4-1：三层记忆的测试。

三条出口条件对应三条用例族：
  1. 衰减公式正确（`test_decay*`）
  2. 写回能落库（`test_write_back*`）
  3. 二次问答的 Context 里能读到偏好（`test_context_injection*`）
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.agent.memory.extract import extract_preference, should_summarize_session
from app.agent.memory.service import MemoryService
from app.agent.memory.store import InMemoryMemoryStore
from app.agent.rag.context_builder import (
    PREFERENCE_HEADER,
    TRIP_CONTEXT_HEADER,
    build_context,
)
from app.schemas.document import DocumentRef, SearchHit
from app.schemas.memory import (
    LAYER_EPISODIC,
    LAYER_SEMANTIC,
    LAYER_SHORT_TERM,
    STATUS_ARCHIVED,
    MemorySourceRef,
)
from app.utils.decay import (
    ARCHIVE_THRESHOLD,
    current_weight,
    days_since,
    decay_weight,
    half_life_to_lambda,
    is_archived,
    lambda_to_half_life,
)

# ---------------------------------------------------------------- 衰减纯函数


def test_half_life_exactly_halves_weight():
    """半衰期的定义：过了 T 天权重正好剩一半。"""
    assert decay_weight(1.0, 30.0, half_life_days=30.0) == pytest.approx(0.5)
    assert decay_weight(1.0, 90.0, half_life_days=90.0) == pytest.approx(0.5)


def test_lambda_is_ln2_over_half_life():
    assert half_life_to_lambda(30.0) == pytest.approx(math.log(2) / 30)
    assert lambda_to_half_life(half_life_to_lambda(7.5)) == pytest.approx(7.5)


def test_half_life_must_be_positive():
    with pytest.raises(ValueError, match="必须为正数"):
        half_life_to_lambda(0)


def test_weight_decays_monotonically_and_never_negative():
    weights = [decay_weight(1.0, d) for d in (0, 10, 30, 60, 120, 365)]
    assert weights == sorted(weights, reverse=True)
    assert all(w > 0 for w in weights)
    assert weights[-1] < ARCHIVE_THRESHOLD, "365 天后的记忆应当早就低于归档线"


def test_zero_elapsed_keeps_full_weight():
    assert decay_weight(0.8, 0.0) == pytest.approx(0.8)


def test_future_timestamp_is_clamped_not_amplified():
    """时钟回拨不能让权重超过 w0——`exp(-λ·负数)` 会 >1，是个隐蔽的越权 bug。"""
    future = datetime.now(UTC) + timedelta(days=5)
    assert days_since(future) == 0.0
    assert (
        current_weight(w0=1.0, decay_rate=half_life_to_lambda(30), last_seen_at=future)
        <= 1.0 + 1e-9
    )


def test_days_since_allows_fractions():
    """一天内的记忆也要衰减，否则 Δt 全是 0，短期记忆永远归档不掉。"""
    moment = datetime.now(UTC) - timedelta(hours=12)
    assert days_since(moment) == pytest.approx(0.5, abs=1e-3)


def test_archived_when_weight_below_threshold():
    assert is_archived(0.049) is True
    assert is_archived(0.051) is False


def test_archived_when_expired():
    assert is_archived(1.0, expires_at=datetime.now(UTC) - timedelta(days=1)) is True
    assert is_archived(1.0, expires_at=datetime.now(UTC) + timedelta(days=1)) is False


def test_default_half_life_is_30_days():
    """设计文档 §7.5：默认 half_life_days = 30。"""
    assert decay_weight(1.0, 30.0) == pytest.approx(0.5)


# ---------------------------------------------------------------- 偏好抽取


@pytest.mark.parametrize(
    ("text", "expected_value"),
    [
        ("我不喜欢人多", "用户不喜欢人多"),
        ("我讨厌排队", "用户不喜欢排队"),
        ("我喜欢古镇", "用户喜欢古镇"),
        ("我偏好自驾", "用户喜欢自驾"),
    ],
)
def test_extract_preference(text: str, expected_value: str):
    got = extract_preference(text)
    assert got is not None, f"应当能抽出偏好：{text}"
    assert got.value == expected_value


@pytest.mark.parametrize("text", ["今天天气不错", "你喜欢人多吗？", "帮我查一下", ""])
def test_extract_preference_is_conservative(text: str):
    """宁可漏抽，不可错抽（noise 比 silence 贵得多）。"""
    assert extract_preference(text) is None


def test_extract_preference_key_is_stable():
    first = extract_preference("我不喜欢人多")
    second = extract_preference("我不喜欢人多啊")
    assert first is not None and second is not None
    assert first.key == second.key


def test_summarize_every_eight_turns():
    assert should_summarize_session(8) is True
    assert should_summarize_session(16) is True
    assert should_summarize_session(7) is False
    assert should_summarize_session(0) is False


# ---------------------------------------------------------------- 写回


@pytest.mark.asyncio
async def test_write_memory_creates_and_overwrites(user_id):
    service = MemoryService(InMemoryMemoryStore())
    first = await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.crowd", value="用户不喜欢人多"
    )
    assert first.value == "用户不喜欢人多"

    second = await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.crowd", value="用户不喜欢拥挤的景区"
    )
    assert second.id == first.id, "同 (user, layer, key) 必须覆盖而不是新增"
    assert second.value == "用户不喜欢拥挤的景区"


@pytest.mark.asyncio
async def test_write_memory_rejects_unknown_layer(user_id):
    service = MemoryService(InMemoryMemoryStore())
    with pytest.raises(ValueError, match="未知的记忆层"):
        await service.write_memory(user_id, layer="nope", key="k", value="v")


@pytest.mark.asyncio
async def test_semantic_layer_defaults_to_90_days(user_id):
    """设计文档 §7.5：偏好类半衰期 90 天。"""
    service = MemoryService(InMemoryMemoryStore())
    record = await service.write_memory(user_id, layer=LAYER_SEMANTIC, key="pref.x", value="v")
    assert record.half_life_days == 90.0


@pytest.mark.asyncio
async def test_low_confidence_memory_is_flagged(user_id):
    service = MemoryService(InMemoryMemoryStore())
    record = await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.y", value="用户喜欢露营", confidence=0.4
    )
    assert record.needs_confirm is True
    context = await service.build_context(user_id)
    assert "（待确认）" in context.preferences[0]


@pytest.mark.asyncio
async def test_remember_from_text_writes_preference(user_id):
    service = MemoryService(InMemoryMemoryStore())
    record = await service.remember_from_text(user_id, "我不喜欢人多")
    assert record is not None
    assert record.layer == LAYER_SEMANTIC

    assert await service.remember_from_text(user_id, "今天天气不错") is None


@pytest.mark.asyncio
async def test_session_summary_only_at_interval(user_id):
    service = MemoryService(InMemoryMemoryStore())
    assert (
        await service.write_session_summary(
            user_id, session_id="s1", turn_count=5, summary="聊了乌镇"
        )
        is None
    )
    record = await service.write_session_summary(
        user_id, session_id="s1", turn_count=8, summary="聊了乌镇与西塘"
    )
    assert record is not None
    assert record.layer == LAYER_EPISODIC


# ---------------------------------------------------------------- 衰减驱动的读取


@pytest.mark.asyncio
async def test_stale_memory_is_archived_and_excluded(user_id):
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    record = await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.old", value="用户喜欢爬山"
    )
    # 模拟 400 天后读取：以 90 天半衰期算，权重约 0.046 < 0.05
    future = datetime.now(UTC) + timedelta(days=400)
    context = await service.build_context(user_id, now=future)
    assert context.preferences == []

    updated = await store.find_by_key(user_id, LAYER_SEMANTIC, "pref.old")
    assert updated is not None
    assert updated.status == STATUS_ARCHIVED, "归档不删除，数据仍在库里"
    assert updated.id == record.id


@pytest.mark.asyncio
async def test_touch_resurrects_memory_by_refreshing_last_seen(user_id):
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    record = await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.crowd", value="用户不喜欢人多"
    )
    await service.touch([record.id])
    refreshed = await store.find_by_key(user_id, LAYER_SEMANTIC, "pref.crowd")
    assert refreshed is not None
    assert refreshed.last_seen_at >= record.last_seen_at


@pytest.mark.asyncio
async def test_semantic_goes_to_preferences_and_episodic_to_trip_context(user_id):
    service = MemoryService(InMemoryMemoryStore())
    await service.write_memory(
        user_id, layer=LAYER_SEMANTIC, key="pref.crowd", value="用户不喜欢人多"
    )
    await service.write_memory(
        user_id, layer=LAYER_EPISODIC, key="trip.t1", value="2025 秋·乌镇两日"
    )
    await service.write_memory(
        user_id, layer=LAYER_SHORT_TERM, key="ctx.now", value="正在问乌镇民宿"
    )

    context = await service.build_context(user_id)
    assert any("不喜欢人多" in line for line in context.preferences)
    assert any("乌镇" in line for line in context.trip_context)
    assert len(context.used_ids) == 3


@pytest.mark.asyncio
async def test_memory_sources_are_kept(user_id):
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    record = await service.write_memory(
        user_id,
        layer=LAYER_SEMANTIC,
        key="pref.crowd",
        value="用户不喜欢人多",
        sources=[MemorySourceRef(source_type="photo", source_id="p1")],
    )
    assert [str(s) for s in store.sources_of(record.id)] == ["photo/p1"]


# ---------------------------------------------------------------- 注入上下文（出口条件）


def _hit(source_id: str, content: str = "乌镇夜景") -> SearchHit:
    return SearchHit(
        ref=DocumentRef(source_type="trip", source_id=source_id),
        content=content,
        metadata={"city": "嘉兴"},
        score=0.9,
    )


def test_memory_section_precedes_evidence():
    """设计文档 §7.4：偏好必须在证据**之前**。"""
    from app.schemas.memory import MemoryContext

    memory = MemoryContext(preferences=["- 用户不喜欢人多"], trip_context=["- 2025 秋·乌镇"])
    built = build_context([_hit("t1")], memory=memory)

    assert PREFERENCE_HEADER in built.text
    assert TRIP_CONTEXT_HEADER in built.text
    assert built.text.index(PREFERENCE_HEADER) < built.text.index(TRIP_CONTEXT_HEADER)
    assert built.text.index(TRIP_CONTEXT_HEADER) < built.text.index("[1]")


def test_no_memory_means_no_section():
    built = build_context([_hit("t1")])
    assert PREFERENCE_HEADER not in built.text
    assert built.text.startswith("[1]")


def test_empty_evidence_drops_memory_section():
    """无证据时连记忆都不给：否则模型容易拿偏好去编一个有模有样的答案。"""
    from app.schemas.memory import MemoryContext

    built = build_context([], memory=MemoryContext(preferences=["- 用户不喜欢人多"]))
    assert built.text == ""
    assert built.blocks == []


@pytest.mark.asyncio
async def test_second_round_reads_the_preference(user_id):
    """W4-1 出口条件：说出偏好后，二次问答的 Context 里能读到它。"""
    service = MemoryService(InMemoryMemoryStore())

    # 第一轮：用户说了一句偏好
    await service.remember_from_text(user_id, "我不喜欢人多")

    # 第二轮：组装上下文
    memory = await service.build_context(user_id)
    built = build_context([_hit("t1")], memory=memory)

    assert "用户不喜欢人多" in built.text
    assert built.text.index(PREFERENCE_HEADER) < built.text.index("[1]")
