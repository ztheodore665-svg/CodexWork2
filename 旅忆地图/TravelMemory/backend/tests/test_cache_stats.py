"""`make cache-stats` 的单测（R3-3.6）：口径必须算对，否则出口条件就是自欺。

验证三件事：
  1. 只统计 POI / 路线类工具（其他工具的调用**不计入分母** —— Q6）
  2. `命中率 = cache_hit / 总调用`
  3. trace JSONL 的读取走的是真实落盘格式（用 `trace.record_tool_call()` 生成，
     而不是手搓一行 JSON —— 一旦 trace 的 schema 改了，这里会先红）
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent.observability import Trace
from app.core.config import get_settings
from app.scripts.cache_stats import (
    STAT_TOOLS,
    TARGET_HIT_RATE,
    CacheReport,
    ToolCallRecord,
    ToolStats,
    async_main,
    build_report,
    load_tool_calls,
    parse_args,
    print_report,
    synthetic_report,
    trace_paths,
)


@pytest.fixture
def trace_dir(tmp_path, monkeypatch):
    """把 trace 目录指到 tmp（conftest 已经这么做，这里显式拿回路径便于断言）。"""
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / "traces"))
    get_settings.cache_clear()
    yield tmp_path / "traces"
    get_settings.cache_clear()


async def _record(user_id, *, tool_name: str, cache_hit: bool) -> None:
    trace = Trace(session=None, user_id=user_id, session_id="stats", query="q")
    await trace.record_tool_call(
        tool_name,
        {"q": "x"},
        {"ok": True},
        latency_ms=1,
        cache_hit=cache_hit,
    )


def test_build_report_only_counts_map_tools():
    records = [
        ToolCallRecord("search_poi", cache_hit=True),
        ToolCallRecord("search_poi", cache_hit=True),
        ToolCallRecord("search_poi", cache_hit=False),
        ToolCallRecord("calculate_route", cache_hit=True),
        ToolCallRecord("other_tool", cache_hit=False),  # 不计入分母
    ]

    report = build_report(records)

    assert report.calls == 4
    assert report.hits == 3
    assert report.hit_rate == 0.75
    assert report.meets_target is True
    assert set(report.per_tool) >= {
        ToolStats("search_poi", 3, 2),
        ToolStats("calculate_route", 1, 1),
    }


def test_empty_report_does_not_divide_by_zero():
    report = build_report([])
    assert report.calls == 0
    assert report.hit_rate == 0.0
    assert report.meets_target is False


async def test_synthetic_sequence_hits_target():
    """10 次查询里 8 次重复 → 命中率必须 ≥ 0.6（§10.1）。"""
    report = await synthetic_report(repeats=8, uniques=2)

    # 每个工具：repeats(8) + uniques(2) = 10 次调用
    assert report.calls == len(STAT_TOOLS) * 10
    assert report.hits / report.calls >= TARGET_HIT_RATE
    # 每个工具内部：same-0 / same-1 两个 key 首轮各 miss 一次，之后 6 次全命中；
    # 再加 2 次全新查询各 miss → 每个工具 10 调用 / 6 命中 = 0.60，刚好过线
    assert report.hits == 2 * 6
    assert report.meets_target is True


async def test_reads_real_trace_jsonl(trace_dir, user_id, capsys):
    """走真实落盘格式：10 条 search_poi，8 条 cache_hit → 0.8。"""
    for i in range(10):
        await _record(user_id, tool_name="search_poi", cache_hit=i < 8)
    await _record(user_id, tool_name="unknown_tool", cache_hit=True)

    records = load_tool_calls(trace_paths(trace_dir))
    assert len(records) == 10, "非 POI/路线工具不该被统计"

    report = build_report(records)
    print_report(report, source=str(trace_dir))

    assert report.hit_rate == 0.8
    assert "✓ 达标" in capsys.readouterr().out


async def test_pretty_prints_pending_notice_when_no_data(tmp_path, capsys):
    """还没有工具调用时给的是"归属批次"提示，不是堆栈也不是空表。"""
    settings = get_settings()

    exit_code = await async_main(parse_args(["--dir", str(tmp_path)]), settings)

    out = capsys.readouterr().out
    assert exit_code == 0, "没数据不等于失败，别把'还没做'报成'做坏了'"
    assert "本阶段后续批次实现" in out
    assert "✓ 达标" not in out


async def test_synthetic_mode_exit_code_reflects_target(capsys):
    settings = get_settings()

    exit_code = await async_main(parse_args(["--synthetic"]), settings)

    assert exit_code == 0
    assert "合成序列" in capsys.readouterr().out


def test_cache_report_dataclass_is_immutable():
    """frozen dataclass：报表一旦生成不许中途被改 —— 否则"谁动了我的命中率"又是一次悬案。"""
    report = CacheReport(per_tool=(ToolStats("search_poi", 1, 1),), calls=1, hits=1)
    # 用具体的 FrozenInstanceError 而不是裸 Exception：
    # 裸 Exception 会连带放过"属性名拼错 → AttributeError"这类假阳性。
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.calls = 99  # type: ignore[misc]
