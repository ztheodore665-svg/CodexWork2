from app.schemes.base import SchemeContext
from app.schemes.scheme2.encoder import StateEncoder


def _encoder(engine, max_edges=4, max_neighbors=4):
    return StateEncoder(SchemeContext(engine=engine), max_edges=max_edges,
                        max_neighbors=max_neighbors)


def test_encoder_dim(mock_engine):
    enc = _encoder(mock_engine)
    # max_phases=3 → 3+1+12+4+2=22
    assert enc.dim() == 22


def test_encoder_output_length_and_range(mock_engine):
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    enc = _encoder(mock_engine)
    obs = enc.encode("T1", 5.0, 0.05)
    assert len(obs) == 22
    assert all(0.0 <= x <= 1.0 for x in obs)


def test_encoder_phase_one_hot(mock_engine):
    mock_engine.set_tls_phase("T1", 1, 8.0)
    enc = _encoder(mock_engine)
    obs = enc.encode("T1", 5.0, 0.05)
    assert obs[1] == 1.0   # one-hot 第 1 位为 1
    assert obs[0] == 0.0


def test_encoder_neighbors_shared_edge(mock_engine):
    # T2 与 T1 共享 E1_0 → 互为邻居
    mock_engine.add_tls("T2", num_phases=3, phase=0, duration=10.0, lanes=["E1_0"])
    enc = _encoder(mock_engine)
    assert "T2" in enc._neighbors.get("T1", []) or "T1" in enc._neighbors.get("T2", [])


def test_encoder_global_state_concatenates(mock_engine):
    mock_engine.add_tls("T2", num_phases=3, phase=0, duration=10.0, lanes=["E3_0"])
    enc = _encoder(mock_engine)
    gs = enc.global_state()
    assert len(gs) == enc.dim() * len(enc.tls_ids)
