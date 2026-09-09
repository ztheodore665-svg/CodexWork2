from app.schemes.base import SchemeContext
from app.schemes.scheme3.weights import EdgeWeightManager


def _weights(engine):
    w = EdgeWeightManager(SchemeContext(engine=engine))
    w.init()
    return w


def test_weight_ewma_smoothing(mock_engine):
    w = _weights(mock_engine)
    w.update()
    first = w.weight("E1")
    mock_engine.set_edge_travel_time("E1", 1000)
    w.update()
    assert w.weight("E1") != first
    assert w.weight("E1") < 1000


def test_weight_load_penalty(mock_engine):
    w = _weights(mock_engine)
    base = w.weight("E2")
    w.add_load("E2", 5)
    assert w.weight("E2") == base + 5 * w.p["load_penalty_per_vehicle"]
    w.remove_load("E2", 5)
    assert w.weight("E2") == base


def test_weight_congested_edges(mock_engine):
    mock_engine.set_edge_stats("E1", occupancy=0.8)
    w = _weights(mock_engine)
    assert "E1" in w.congested_edges()
    assert "E2" not in w.congested_edges()


def test_weight_status_shape(mock_engine):
    w = _weights(mock_engine)
    s = w.status()
    for k in ("edge_count", "updates", "congested", "total_load", "params"):
        assert k in s
