import json

from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import Scheme2Controller


def build_scheme2(engine, torch_available=False, mode=None):
    ctx = SchemeContext(engine=engine, config={
        "torch_available": torch_available, "mode": mode})
    c = Scheme2Controller(ctx)
    c.init()
    return c


def test_scheme2_auto_falls_back_to_scoot_without_torch(mock_engine):
    c = build_scheme2(mock_engine, torch_available=False)
    assert c.mode == "scoot"


def test_scheme2_status_shape(mock_engine):
    c = build_scheme2(mock_engine, torch_available=False)
    s = c.handle_action("get_status", {})
    for k in ("mode", "mappo", "stgcn", "scoot", "encoder", "reward", "decision"):
        assert k in s
    assert "downgrades" in s["decision"]


def test_scheme2_on_step_runs_decisions(mock_engine):
    c = build_scheme2(mock_engine, torch_available=False)
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    for _ in range(10):
        c.on_step()
    assert c._decision_count >= 2      # 每 5 步决策一次
    assert c._scoot_actions > 0


def test_scheme2_switch_modes_without_torch(mock_engine):
    c = build_scheme2(mock_engine, torch_available=False)
    assert c.handle_action("switch_to_scoot", {})["ok"] is True
    out = c.handle_action("switch_to_mappo", {})
    assert out["ok"] is False          # 无 torch 无法切 MAPPO


def test_scheme2_get_predictions(mock_engine):
    c = build_scheme2(mock_engine, torch_available=False)
    mock_engine.set_edge_stats("E1", occupancy=0.2, mean_speed=8.0)
    out = c.handle_action("get_predictions", {})
    assert out["ok"] is True and "predictions" in out


def test_scheme2_registered():
    from app.schemes import has_scheme
    assert has_scheme("scheme_2")


def test_scheme2_scoot_actually_acts(mock_engine):
    """回归：SCOOT 应随相位已持续时长做出 extend/switch，而非永远 keep。"""
    mock_engine.set_edge_stats("E1", occupancy=0.9)   # 进口道高需求
    c = build_scheme2(mock_engine, torch_available=False)  # scoot 模式
    # 模拟平台调用顺序：先 step 引擎再 on_step
    for _ in range(20):
        mock_engine.step()
        c.on_step()
    assert c.scoot._extend_count > 0 or c.scoot._switch_count > 0
    assert c.scoot.status()["switch_count"] + c.scoot.status()["extend_count"] > 0


def test_scheme2_records_edge_features(mock_engine, tmp_path):
    ctx = SchemeContext(engine=mock_engine, config={
        "torch_available": False, "record_dir": str(tmp_path),
        "mode": "scoot", "explore_epsilon": 0.3})
    c = Scheme2Controller(ctx)
    c.init()
    mock_engine.add_vehicle("v1", edges=["E1", "E2"], speed=5.0)
    for _ in range(10):
        c.on_step()
    c.cleanup()
    assert (tmp_path / "edge_features.csv").exists()
    assert (tmp_path / "meta.json").exists()
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["obs_dim"] == c.encoder.dim()
    assert meta["n_tls"] == len(c.encoder.tls_ids)
    assert c.handle_action("get_status", {})["recorder"] is not None
