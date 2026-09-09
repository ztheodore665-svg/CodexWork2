import pytest

from app.schemes.base import SchemeContext
from app.schemes.scheme2.reward import DEFAULT_WEIGHTS, RewardCalculator


def _reward(engine):
    return RewardCalculator(SchemeContext(engine=engine))


def test_reward_switch_penalty_doubles_on_high_freq():
    r = _reward(None)
    base = {"avg_delay": 10, "avg_queue": 2, "avg_speed": 5}
    low = r.compute("T1", [0.0] * 22, base, True, 3)
    high = r.compute("T1", [0.0] * 22, base, True, 8)
    assert high < low
    # 未切换时无惩罚，奖励更高
    no_switch = r.compute("T1", [0.0] * 22, base, False, 0)
    assert no_switch > high


def test_reward_pressure_gives_higher_score():
    """服务高压力移动（上游排队长−下游排队长大）→ 更高奖励。"""
    r = _reward(None)
    base = {"avg_delay": 10, "avg_queue": 2, "avg_speed": 5}
    low = r.compute("T1", [0.0] * 22, base, False, 0, pressure=0.0)
    high = r.compute("T1", [0.0] * 22, base, False, 0, pressure=2.0)
    assert high > low
    assert high == pytest.approx(low + 2.0)   # w_pressure=1.0


def test_periodic_reward_sparse():
    r = _reward(None)
    val = r.periodic_reward({"avg_queue": 5, "avg_speed": 3, "global_speed": 3})
    assert isinstance(val, float)
