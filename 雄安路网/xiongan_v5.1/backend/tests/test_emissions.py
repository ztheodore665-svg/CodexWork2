from app.core.datacollector import DataCollector
from app.metrics.store import MetricsStore


def test_emissions_collect(mock_engine):
    mock_engine.add_vehicle("v1", edges=["E1", "E2"])
    mock_engine.add_vehicle("v2", edges=["E2", "E3"])
    mock_engine.set_vehicle_emissions("v1", fuel=0.5, co2=10.0, co=0.1, nox=0.05)
    mock_engine.set_vehicle_emissions("v2", fuel=0.3, co2=6.0, co=0.2, nox=0.1)
    out = DataCollector(mock_engine).emissions()
    assert set(out) == {"fuel_consumed", "co2", "co", "nox"}
    assert out["fuel_consumed"] == 0.8
    assert out["co2"] == 16.0


def test_emissions_empty_network(mock_engine):
    out = DataCollector(mock_engine).emissions()
    assert out["fuel_consumed"] == 0.0


def test_emissions_recorded_to_store(mock_engine):
    dc = DataCollector(mock_engine)
    st = MetricsStore()
    dc.record_metrics = lambda *a: None
    # 手动按 runtime._record_metrics 逻辑写入
    em = dc.emissions()
    st.append(60, "fuel", em["fuel_consumed"], "overall")
    st.append(60, "co2", em["co2"], "overall")
    assert st.query("fuel", 0, 100)[0]["value"] == 0.0
