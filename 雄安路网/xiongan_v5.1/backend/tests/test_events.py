from app.events.injector import EventInjector
from app.schemes.base import SchemeContext


def _injector(mock_engine, network=None):
    events = []
    ctx = SchemeContext(engine=mock_engine,
                        push_event=lambda t, m, d=None: events.append((t, m)))
    inj = EventInjector(ctx, network=network)
    inj._events_pushed = events
    return inj


def test_event_inject_construction(mock_engine):
    inj = _injector(mock_engine)
    out = inj.inject("construction", {"edge_ids": ["E2"], "reason": "施工"}, step=100)
    assert out["ok"] and "E2" in inj.active_edges()
    # 限速降为接近 0
    assert mock_engine.get_edge_speed_limit("E2") < 1.0


def test_event_inject_accident(mock_engine):
    inj = _injector(mock_engine)
    orig = mock_engine.get_edge_speed_limit("E1")
    out = inj.inject("accident", {"edge_ids": ["E1"]}, step=100)
    assert out["ok"]
    assert mock_engine.get_edge_speed_limit("E1") < orig


def test_event_inject_large_event_adds_vehicles(mock_engine):
    inj = _injector(mock_engine)
    before = len(mock_engine.get_vehicle_ids())
    out = inj.inject("large_event", {"edge_ids": ["E1", "E2"], "vehicles": 5}, step=50)
    assert out["ok"] and out["result"]["added"] == 5
    assert len(mock_engine.get_vehicle_ids()) == before + 5


def test_event_unknown_type(mock_engine):
    inj = _injector(mock_engine)
    out = inj.inject("flood", {}, step=1)
    assert out["ok"] is False


def test_event_list_and_clear(mock_engine):
    inj = _injector(mock_engine)
    inj.inject("accident", {"edge_ids": ["E1"]}, step=1)
    assert len(inj.list_events()) == 1
    inj.clear()
    assert inj.active_edges() == set()


def test_event_syncs_network_restriction(mock_engine):
    from app.schemes.base import SchemeContext
    from app.schemes.scheme3.graph import RoadNetwork
    net = RoadNetwork(SchemeContext(engine=mock_engine))
    inj = _injector(mock_engine, network=net)
    inj.inject("construction", {"edge_ids": ["E2"]}, step=1)
    assert net.is_restricted("E2")


def test_runtime_inject_event_requires_simulation():
    from app.core.runtime import AppRuntime
    from app.core.session import SessionError
    from app.ws.manager import WSManager
    import pytest
    rt = AppRuntime(WSManager(), settings=None)
    with pytest.raises(SessionError):
        rt.inject_event("accident", {"edge_ids": ["E1"]})
