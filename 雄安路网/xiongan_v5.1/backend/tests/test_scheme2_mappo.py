import pytest

from app.schemes.scheme2.mappo import MAPPOAgent


def test_mappo_heuristic_hold_min_green():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="heuristic")
    a.phase_elapsed = 2.0
    a.min_green = 8.0
    a.current_phase = 1
    assert a.act([0.0] * 22, greedy=True) == 1


def test_mappo_act_returns_action_index():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="heuristic")
    assert a.act([0.0] * 22, greedy=True) in {0, 1, 2}


def test_mappo_heuristic_switches_after_max_green():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="heuristic")
    a.phase_elapsed = 90.0      # > max_green 60
    a.current_phase = 2
    assert a.act([0.0] * 22, greedy=True) == 0   # (2+1) % 3


def test_mappo_without_torch_falls_to_heuristic():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="train", torch_ok=False)
    assert a.mode == "heuristic"
    assert a.act([0.0] * 22, greedy=True) in {0, 1, 2}


def test_mappo_update_noop_without_buffer():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="heuristic", torch_ok=False)
    out = a.update()
    assert out["updates"] >= 1


def test_mappo_status_shape():
    a = MAPPOAgent(obs_dim=22, n_actions=3, mode="heuristic")
    a.store([0.0] * 22, [0.0] * 22, 0, 1.0, 0.0, 0.0, False)
    s = a.status()
    for k in ("mode", "torch_available", "model_loaded", "obs_dim",
              "actions", "buffer_size", "updates", "hyperparams"):
        assert k in s
