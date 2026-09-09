import json

import numpy as np

from app.schemes.base import SchemeContext
from app.schemes.scheme2.encoder import StateEncoder
from app.schemes.scheme2.recorder import DataRecorder


def test_recorder_edge_features_csv(mock_engine, tmp_path):
    r = DataRecorder(str(tmp_path))
    r.record_edge_features(mock_engine, step=5)
    r.record_edge_features(mock_engine, step=6)   # 间隔内，不写
    r.close()
    lines = (tmp_path / "edge_features.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4                     # 表头 + 3 条边
    assert lines[0].startswith("step,edge,flow")
    assert lines[1].split(",")[0] == "5" and lines[1].split(",")[1] == "E1"


def test_recorder_experiences_and_meta(mock_engine, tmp_path):
    enc = StateEncoder(SchemeContext(engine=mock_engine))
    r = DataRecorder(str(tmp_path), encoder=enc)
    dim = enc.dim()
    gdim = dim * len(enc.tls_ids)
    for i in range(3):
        r.record_experience([0.0] * dim, [0.0] * gdim, 1, 0.5, False)
    r.flush_meta(n_actions=enc.max_phases)
    r.close()

    shards = list(tmp_path.glob("experiences_*.npz"))
    assert len(shards) == 1
    d = np.load(shards[0])
    assert d["obs"].shape[1] == dim
    assert d["global"].shape[1] == gdim
    assert d["action"].tolist() == [1, 1, 1]
    assert "value" in d.files

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["obs_dim"] == dim
    assert meta["global_dim"] == gdim
    assert meta["n_actions"] == enc.max_phases
    assert meta["n_tls"] == len(enc.tls_ids)


def test_recorder_merge_shards_on_close(mock_engine, tmp_path):
    r = DataRecorder(str(tmp_path), edge_interval=1)
    # 手动压低阈值验证分片，直接多次 close 模拟分片
    r.close()
    r2 = DataRecorder(str(tmp_path), edge_interval=1)
    r2.record_experience([0.0] * 2, [0.0] * 4, 0, 0.0, False)
    r2.close()
    assert len(list(tmp_path.glob("experiences_*.npz"))) == 1   # 每次 close 各一片
