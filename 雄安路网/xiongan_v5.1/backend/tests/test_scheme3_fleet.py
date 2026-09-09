from app.schemes.base import SchemeContext
from app.schemes.scheme3.fleet import FleetManager


def _fleet(engine, cooldown=60):
    return FleetManager(SchemeContext(engine=engine), cooldown=cooldown)


def test_fleet_auto_identify_bus_prefix(mock_engine):
    mock_engine.add_vehicle("bus_01", veh_type="bus", edges=["E1", "E2"])
    f = _fleet(mock_engine)
    assert f.scan() == ["bus_01"]
    assert f.fleet_vehicles("bus")[0]["id"] == "bus_01"


def test_fleet_identify_by_type(mock_engine):
    mock_engine.add_vehicle("truck_1", veh_type="truck", edges=["E1", "E2"])
    f = _fleet(mock_engine)
    f.scan()
    assert f.fleet_vehicles("delivery")[0]["id"] == "truck_1"


def test_fleet_reroute_cooldown():
    f = _fleet(None)
    f.register("ride_1", "ride_hail", destination="E9")
    f.record_reroute("ride_1", ["E1"], 5.0)     # 记录后进入冷却
    assert f.can_reroute("ride_1") is False
    f.tick(70)                                   # 冷却 60 步后
    assert f.can_reroute("ride_1") is True


def test_fleet_register_unregister(mock_engine):
    f = _fleet(mock_engine)
    f.register("v1", "custom", destination="E3")
    assert f.fleet_vehicles("custom")[0]["id"] == "v1"
    assert f.unregister("v1") is True
    assert f.fleet_vehicles("custom") == []


def test_fleet_status_shape(mock_engine):
    f = _fleet(mock_engine)
    f.register("bus_9", "bus", destination="E3")
    s = f.status()
    for k in ("registered", "active", "arrived", "reroutes", "by_category", "cooldown"):
        assert k in s
    assert s["by_category"]["bus"] == 1
