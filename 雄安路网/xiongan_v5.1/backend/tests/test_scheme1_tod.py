import pytest

from app.schemes.scheme1.tod import TODSchedule


def test_tod_default_periods():
    t = TODSchedule()
    assert t.get_period(8 * 3600) == "morning_peak"    # 8:00
    assert t.get_period(12 * 3600) == "off_peak"       # 12:00
    assert t.get_period(23 * 3600) == "night"          # 23:00
    assert t.get_period(2 * 3600) == "night"           # 跨午夜 2:00


def test_tod_force_release():
    t = TODSchedule()
    t.force("evening_peak")
    assert t.get_period(8 * 3600) == "evening_peak"
    assert t.is_forced()
    t.release_force()
    assert t.get_period(8 * 3600) == "morning_peak"
    assert not t.is_forced()


def test_tod_force_unknown_raises():
    t = TODSchedule()
    with pytest.raises(ValueError):
        t.force("nope")


def test_tod_transition_detection():
    t = TODSchedule()
    assert t.transition_detected(6 * 3600) is False   # 首次无 prev
    assert t.transition_detected(6 * 3600) is False   # night → night
    assert t.transition_detected(8 * 3600) is True    # night → morning_peak


def test_tod_cycle_range_and_green_wave():
    t = TODSchedule()
    assert t.get_cycle_range("morning_peak") == (60, 120)
    assert t.green_wave_enabled("morning_peak") is True
    assert t.green_wave_enabled("off_peak") is False
