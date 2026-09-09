from app.schemes.scheme1.webster import WebsterOptimizer


def _new_opt():
    return WebsterOptimizer.__new__(WebsterOptimizer)


def test_webster_cycle_formula():
    t = _new_opt()
    # Y=0.6, L=12 → C=(1.5*12+5)/(1-0.6)=(18+5)/0.4=57.5→58
    assert t._opt_cycle(total_loss=12.0, total_ratio=0.6) == 58


def test_webster_saturation_protection():
    t = _new_opt()
    assert t._opt_cycle(total_loss=12.0, total_ratio=0.97) == 240


def test_webster_cycle_clamped_to_bounds():
    t = _new_opt()
    assert t._opt_cycle(total_loss=12.0, total_ratio=0.1) == 40   # 下界 (18+5)/0.9=25.6→40
    assert t._opt_cycle(total_loss=12.0, total_ratio=0.9) == 230  # (18+5)/0.1=230 < 240


def test_webster_compute_timing_with_mock(mock_engine):
    mock_engine.set_edge_stats("E1", vehicle_count=10, occupancy=0.3, travel_time=20.0)
    mock_engine.set_edge_stats("E2", vehicle_count=8, occupancy=0.25, travel_time=18.0)
    from app.schemes.base import SchemeContext
    opt = WebsterOptimizer(SchemeContext(engine=mock_engine))
    opt.collect_flows()
    plan = opt.compute_timing("T1", period_range=(60, 120))
    assert plan["cycle"] >= 40
    assert len(plan["phases"]) == 3
    assert all(p["display_green"] >= 8 for p in plan["phases"])
    assert "total_flow_ratio" in plan


def test_webster_apply_sets_phases(mock_engine):
    from app.schemes.base import SchemeContext
    opt = WebsterOptimizer(SchemeContext(engine=mock_engine))
    opt.collect_flows()
    timing = opt.compute_all(period_range=(60, 120))
    opt.apply(timing)
    assert mock_engine.get_tls_state("T1")["phase_index"] == 2  # 最后一个相位被应用
