from app.schemes.base import SchemeContext
from app.schemes.scheme2.scoot import SCOOTController


def _scoot(engine, params=None):
    return SCOOTController(SchemeContext(engine=engine), params=params)


def test_scoot_cycle_adaptation():
    s = _scoot(None)
    assert s.adjust_cycle(0.9) > s.adjust_cycle(0.5)   # 过饱和增大
    assert s.adjust_cycle(0.2) < s.adjust_cycle(0.5)   # 低饱和减小


def test_scoot_distribute_greens_min():
    s = _scoot(None, {"min_green": 8})
    g = s.distribute_greens({"A": 100, "B": 10}, cycle=60)
    assert all(v >= 8 for v in g.values())


def test_scoot_distribute_equal_when_no_demand():
    s = _scoot(None)
    g = s.distribute_greens({"A": 0, "B": 0, "C": 0}, cycle=60)
    assert len(set(g.values())) == 1


def test_scoot_decide_rules():
    s = _scoot(None)
    s._elapsed["T1"] = 10.0   # 已超最小绿灯，进入规则 3/4
    # 需求低且他相高 → switch
    assert s.decide("T1", {"phase_index": 0}, {0: 0.0, 1: 0.9}) == "switch"
    # 需求高未到最大 → extend
    assert s.decide("T1", {"phase_index": 0}, {0: 0.9, 1: 0.0}) == "extend"


def test_scoot_commit_cycle_clamped():
    s = _scoot(None)
    s.commit_cycle(500)
    assert s._cycle == s.p["max_cycle"]


def test_scoot_status_shape():
    s = _scoot(None)
    st = s.status()
    for k in ("active_intersections", "switch_count", "extend_count",
              "cycle_adjust_count", "cycle", "params"):
        assert k in st
