import pytest

from app.core.engine import Engine, EngineError
from mock_engine import MockEngine


def test_engine_step_and_sim_time():
    e = MockEngine()
    e.connect("net.xml", [], [], 0, 100, 1.0)
    e.step()
    assert e.get_sim_time() == 1.0


def test_engine_state_machine_illegal_transition():
    e = MockEngine()
    e.close()
    with pytest.raises(EngineError):
        e.step()


def test_engine_read_edge_stats_and_vehicles():
    e = MockEngine()
    e.connect("net.xml")
    e.add_edge("E1", length=100.0, speed_limit=10.0)
    e.add_edge("E2", length=100.0, speed_limit=10.0)
    e.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    stats = e.get_edge_stats("E1")
    assert set(stats) == {"vehicle_count", "mean_speed", "occupancy", "travel_time"}
    st = e.get_vehicle_state("v1")
    assert st["route"] == ["E1", "E2"] and st["type"] == "passenger"


def test_engine_vehicle_moves_and_arrives():
    e = MockEngine()
    e.connect("net.xml")
    e.add_edge("E1", length=100.0, speed_limit=10.0)
    e.add_edge("E2", length=100.0, speed_limit=10.0)
    e.add_vehicle("v1", edges=["E1", "E2"], speed=25.0)  # 每步 25m，4 步走完 E1
    for _ in range(4):
        e.step()
    assert e.get_vehicle_state("v1")["lane"] == "E2"
    for _ in range(4):
        e.step()
    assert "v1" not in e.get_vehicle_ids()
    assert e.get_cumulative_arrivals() == 1


def test_engine_tls_read_write():
    e = MockEngine()
    e.connect("net.xml")
    e.add_tls("T1", num_phases=3, phase=0, duration=10.0, lanes=["E1_0"])
    st = e.get_tls_state("T1")
    assert st["num_phases"] == 3 and st["phase_index"] == 0
    e.set_tls_phase("T1", 2, 15.0)
    assert e.get_tls_state("T1")["phase_index"] == 2
    conns = e.get_tls_connections("T1")
    assert conns[0] == ["E1_0"]


def test_engine_missing_vehicle_raises_1002():
    e = MockEngine()
    e.connect("net.xml")
    with pytest.raises(EngineError) as ei:
        e.get_vehicle_state("nope")
    assert ei.value.code == 1002
