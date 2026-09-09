from app.schemes.base import SchemeContext
from app.schemes.scheme1.controller import Scheme1Controller


def build_scheme1(engine):
    events = []
    ctx = SchemeContext(engine=engine, config={},
                        push_event=lambda t, m, d=None: events.append(t))
    c = Scheme1Controller(ctx)
    c.init()
    c._events = events
    return c


def test_scheme1_controller_switch_tod(mock_engine):
    c = build_scheme1(mock_engine)
    out = c.handle_action("switch_tod_plan", {"plan_name": "night"})
    assert out["ok"] and c.tod.is_forced()
    assert c.tod.get_period(8 * 3600) == "night"


def test_scheme1_get_status_shape(mock_engine):
    c = build_scheme1(mock_engine)
    s = c.handle_action("get_status", {})
    for k in ["period", "corridors", "timing_stats", "offset_plans", "stats"]:
        assert k in s
    assert "transitions" in s["stats"] and "recalculations" in s["stats"]


def test_scheme1_release_force(mock_engine):
    c = build_scheme1(mock_engine)
    c.handle_action("switch_tod_plan", {"plan_name": "evening_peak"})
    out = c.handle_action("release_force", {})
    assert out["ok"] and not c.tod.is_forced()


def test_scheme1_recalculate_action(mock_engine):
    c = build_scheme1(mock_engine)
    before = c._recalc_count
    out = c.handle_action("recalculate", {})
    assert out["ok"] and c._recalc_count > before


def test_scheme1_unknown_action(mock_engine):
    c = build_scheme1(mock_engine)
    out = c.handle_action("nope", {})
    assert out["ok"] is False


def test_scheme1_registered():
    from app.schemes import has_scheme
    assert has_scheme("scheme_1")
