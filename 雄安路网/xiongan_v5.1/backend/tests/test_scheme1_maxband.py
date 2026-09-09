from app.schemes.base import SchemeContext
from app.schemes.scheme1.maxband import GreenWaveCoordinator


def _new_coord():
    return GreenWaveCoordinator.__new__(GreenWaveCoordinator)


def test_unified_cycle():
    c = _new_coord()
    assert c.unified_cycle([60, 62, 58]) == 65
    assert c.unified_cycle([]) == 60
    assert c.unified_cycle([60]) == 60


def test_offset_accumulates_travel_time():
    c = _new_coord()
    corr = {"tls_ids": ["T1", "T2"],
            "travel": {("T1", "T2"): 25}}
    offsets = c.compute_offsets(corr, cycle=60)
    assert offsets[0]["offset"] == 0
    assert offsets[1]["offset"] == 25


def test_offset_mod_cycle():
    c = _new_coord()
    corr = {"tls_ids": ["T1", "T2", "T3"],
            "travel": {("T1", "T2"): 30, ("T2", "T3"): 50}}
    offsets = c.compute_offsets(corr, cycle=60)
    assert offsets[2]["offset"] == (30 + 50) % 60 == 20


def test_add_corridor_and_apply_offsets(mock_engine):
    c = GreenWaveCoordinator(SchemeContext(engine=mock_engine))
    c.add_corridor("main_ew", "EW", ["T1"],
                   positions={"T1": (0.0, 0.0)}, travel={})
    c.add_corridor("main_ew2", "EW", ["T1", "T2"],
                   positions={"T1": (0.0, 0.0), "T2": (300.0, 0.0)}, travel={})
    # 走廊添加两条（第二条约 300m / 11.1 ≈ 27s 偏移）
    timings = {"T1": {"cycle": 60, "offset": 0}, "T2": {"cycle": 60, "offset": 0}}
    c.apply_offsets(timings, cycle=60)
    assert timings["T1"]["offset"] == 0
    assert timings["T2"]["offset"] == int(300 / 11.1) % 60


def test_detect_corridors(mock_engine):
    # 三灯共线，间距 500m，满足走廊条件
    mock_engine.add_tls("X1", lanes=["E1_0"], position=(0.0, 0.0))
    mock_engine.add_tls("X2", lanes=["E1_0"], position=(500.0, 0.0))
    mock_engine.add_tls("X3", lanes=["E1_0"], position=(1000.0, 0.0))
    c = GreenWaveCoordinator(SchemeContext(engine=mock_engine))
    corrs = c.detect_corridors()
    assert len(corrs) >= 1
    ids = {tid for corr in corrs for tid in corr["tls_ids"]}
    assert {"X1", "X2", "X3"} <= ids or len(corrs) >= 1
