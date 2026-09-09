from app.core.datacollector import DataCollector, edge_from_lane


def test_edge_from_lane():
    assert edge_from_lane("E1_0") == "E1"
    assert edge_from_lane("E1") == "E1"


def test_collector_incremental(mock_engine):
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    mock_engine.add_vehicle("v2", edges=["E2", "E3"], speed=5.0)
    dc = DataCollector(mock_engine)
    out1 = dc.collect(1)
    assert {a["id"] for a in out1["vehicles"]["added"]} == {"v1", "v2"}
    assert out1["vehicles"]["removed"] == []
    assert out1["overall"] is None  # 非 60 倍数步无全局指标

    mock_engine.step()
    out2 = dc.collect(2)
    assert {u["id"] for u in out2["vehicles"]["updated"]} == {"v1", "v2"}
    assert out2["vehicles"]["removed"] == []


def test_collector_removed_when_vehicle_arrives(mock_engine):
    # 短边：v1 每步 50m，2 步走完 E1，再 2 步走完 E2 到达
    mock_engine.add_edge("S1", length=50.0)
    mock_engine.add_edge("S2", length=50.0)
    mock_engine.add_vehicle("v1", edges=["S1", "S2"], speed=50.0)
    dc = DataCollector(mock_engine)
    dc.collect(1)
    for _ in range(4):
        mock_engine.step()
    out = dc.collect(5)
    assert out["vehicles"]["removed"] == ["v1"]


def test_collector_overall_at_60(mock_engine):
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    mock_engine.add_vehicle("v2", edges=["E2", "E3"], speed=5.0)
    dc = DataCollector(mock_engine)
    out = dc.collect(60)
    assert out["overall"]["vehicle_count"] == 2
    assert out["overall"]["avg_speed"] > 0
    assert "total_throughput" in out["overall"]


def test_collector_tls_only_on_change(mock_engine):
    dc = DataCollector(mock_engine)
    mock_engine.set_tls_phase("T1", 1, 8.0)
    out = dc.collect(1)
    assert "T1" in out["tls"]
    # 状态未变，不重复推送
    out2 = dc.collect(2)
    assert out2["tls"] == {}


def test_collector_intersection_metrics(mock_engine):
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    dc = DataCollector(mock_engine)
    out = dc.collect(1)
    inter = out["intersections"]["T1"]
    for k in ("queue_length", "waiting_time", "throughput", "current_phase", "phase_duration"):
        assert k in inter
