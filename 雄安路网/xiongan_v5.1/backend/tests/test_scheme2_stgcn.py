from app.schemes.base import SchemeContext
from app.schemes.scheme2.stgcn import TrafficPredictor


def _predictor(engine, mode="ewma"):
    return TrafficPredictor(SchemeContext(engine=engine, config={}), adj={}, mode=mode)


def test_predictor_ewma_fallback():
    p = _predictor(None)
    for i in range(12):
        p.update({f"e{i}": {"flow": 10.0 + i, "speed": 30.0, "occupancy": 0.2}})
    out = p.predict()
    assert set(out) == {f"e{i}" for i in range(12)}
    assert 10.0 <= out["e0"] <= 22.0
    assert p.mode() == "ewma"


def test_predictor_ewma_smoothing():
    p = _predictor(None)
    p.update({"E1": {"flow": 10.0}})
    first = p.predict()["E1"]
    p.update({"E1": {"flow": 20.0}})
    second = p.predict()["E1"]
    assert first == 10.0
    assert 10.0 < second < 20.0   # 平滑未过冲


def test_predictor_status_shape():
    p = _predictor(None)
    p.update({"E1": {"flow": 5.0}})
    s = p.status()
    for k in ("mode", "model_loaded", "edge_count", "history_len", "updates", "predictions"):
        assert k in s


def test_predictor_mode_explicit():
    p = _predictor(None, mode="online")
    assert p.mode() == "online"
