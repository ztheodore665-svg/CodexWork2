from app.schemes.base import SchemeContext
from app.schemes.scheme3.controller import Scheme3Controller


def build_scheme3(engine):
    events = []
    ctx = SchemeContext(engine=engine, config={},
                        push_event=lambda t, m, d=None: events.append(t))
    c = Scheme3Controller(ctx)
    c.init()
    c._events = events
    return c


def test_scheme3_controller_reroute_fleet(mock_engine):
    mock_engine.add_vehicle("bus_01", veh_type="bus", edges=["E1", "E2"])
    c = build_scheme3(mock_engine)
    c.handle_action("register_fleet", {"vehicle_ids": ["bus_01"], "category": "bus",
                                       "destinations": ["E3"]})
    out = c.handle_action("reroute_fleet", {"vehicle_ids": ["bus_01"], "destinations": ["E3"]})
    assert out["results"][0]["ok"] is True


def test_scheme3_add_restricted_zone(mock_engine):
    c = build_scheme3(mock_engine)
    c.handle_action("add_restricted_zone", {"edge_ids": ["E2"], "reason": "demo"})
    assert c.network.is_restricted("E2")
    c.handle_action("remove_restricted_zone", {"edge_ids": ["E2"]})
    assert not c.network.is_restricted("E2")


def test_scheme3_auto_reroute_on_congestion(mock_engine):
    mock_engine.add_vehicle("bus_01", veh_type="bus", edges=["E1", "E2"])
    mock_engine.set_edge_stats("E1", occupancy=0.9)
    c = build_scheme3(mock_engine)
    c.handle_action("register_fleet", {"vehicle_ids": ["bus_01"], "category": "bus",
                                       "destinations": ["E3"]})
    for _ in range(30):
        c.on_step()
    assert c._auto_reroute_count >= 1


def test_scheme3_get_status_shape(mock_engine):
    c = build_scheme3(mock_engine)
    s = c.handle_action("get_status", {})
    for k in ("network", "weights", "fleet", "planner", "auto_reroute", "stats"):
        assert k in s


def test_scheme3_get_congestion_map(mock_engine):
    mock_engine.set_edge_stats("E1", occupancy=0.85)
    c = build_scheme3(mock_engine)
    out = c.handle_action("get_congestion_map", {})
    assert "E1" in out["congested"]


def test_scheme3_registered():
    from app.schemes import has_scheme
    assert has_scheme("scheme_3")
