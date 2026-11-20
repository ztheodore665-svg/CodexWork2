"""中文时间表达解析（W2-1 的出口条件）。

用例放在 `app/data/time_cases.jsonl` 里，**每条自带 `today`** ——
这样无论哪天跑，期望值都是同一个。时间解析的结果依赖"今天"，
不写死日期的话，明年再跑一遍所有期望值都会变，用例就从回归测试退化成一次性快照。
"""

from __future__ import annotations

import pytest

from app.agent.eval.dataset import load_time_cases
from app.agent.rag.time_parser import TimeRange, parse_time_range

CASES = load_time_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.question)
def test_time_expression(case) -> None:
    got = parse_time_range(case.question, case.today)
    if case.expects_range:
        assert got is not None, f"应当解析出时间范围：{case.question}"
        assert (got.start, got.end) == (case.expected_start, case.expected_end)
    else:
        assert got is None, f"应当放弃过滤（不过滤）：{case.question}"


def test_parse_accuracy_is_perfect() -> None:
    """出口条件：中文时间表达用例 100% 通过。"""
    correct = 0
    for case in CASES:
        got = parse_time_range(case.question, case.today)
        if case.expects_range:
            ok = (
                got is not None
                and got.start == case.expected_start
                and got.end == case.expected_end
            )
        else:
            ok = got is None
        correct += int(ok)
    assert correct == len(CASES), f"解析准确率 {correct}/{len(CASES)}"


def test_winter_range_crosses_year_and_never_sits_in_future() -> None:
    """冬天是 12 月 ~ 次年 2 月，且必须是"已经发生的那一个冬天"。"""
    got = parse_time_range("我冬天去看过冰雕吗", __import__("datetime").date(2026, 10, 19))
    assert got is not None
    assert got.start.year == 2025 and got.start.month == 12
    assert got.end.year == 2026 and got.end.month == 2


def test_to_dict_and_contains() -> None:
    from datetime import date

    rng = TimeRange(date(2025, 9, 1), date(2025, 11, 30), "去年秋天")
    assert rng.to_dict() == {"start": "2025-09-01", "end": "2025-11-30"}
    assert rng.contains(date(2025, 10, 3))
    assert not rng.contains(date(2025, 12, 1))


def test_empty_input_returns_none() -> None:
    assert parse_time_range("") is None
    assert parse_time_range("   ") is None
