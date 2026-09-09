"""数据录制模块：采集 STGCN 边特征序列与 MAPPO 交互经验，供离线训练。

换路网重训流程：在目标路网上启动方案二（record_dir + explore_epsilon）跑几轮仿真
→ 自动产出 edge_features.csv 与 experiences_*.npz 与 meta.json
→ 用 models/train_stgcn.py / train_mappo.py 指向该目录训练。
obs_dim/n_actions/global_dim 由编码器在运行时按路网推导，无需手工配置。
"""

import json
import os
from datetime import datetime

import numpy as np

EXPERIENCE_FLUSH_THRESHOLD = 5000


class DataRecorder:
    def __init__(self, record_dir: str, encoder=None, edge_interval: int = 5):
        self.dir = os.path.abspath(record_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.encoder = encoder
        self.edge_interval = edge_interval
        self._edge_path = os.path.join(self.dir, "edge_features.csv")
        self._edge_file = open(self._edge_path, "w", encoding="utf-8")
        self._edge_file.write("step,edge,flow,speed,occupancy\n")
        self._experiences: list[tuple] = []
        self._shard_index = 0
        self._edge_steps = 0

    # ── STGCN 边特征（CSV） ─────────────────────────────────

    def record_edge_features(self, engine, step: int, interval: int | None = None) -> None:
        interval = interval or self.edge_interval
        if step - self._edge_steps < interval:
            return
        rows = []
        for eid in engine.get_edge_ids():
            st = engine.get_edge_stats(eid)
            flow = st["occupancy"] * 1800.0
            rows.append(f"{step},{eid},{flow:.3f},{st['mean_speed']:.3f},{st['occupancy']:.3f}\n")
        self._edge_file.writelines(rows)
        self._edge_file.flush()
        self._edge_steps = step

    # ── MAPPO 经验（npz 分片） ──────────────────────────────

    def record_experience(self, obs, gobs, action, reward, done) -> None:
        self._experiences.append((list(obs), list(gobs), int(action),
                                  float(reward), float(done)))
        if len(self._experiences) >= EXPERIENCE_FLUSH_THRESHOLD:
            self._flush_experiences()

    def _flush_experiences(self) -> None:
        if not self._experiences:
            return
        obs, gobs, act, rew, done = zip(*self._experiences)
        path = os.path.join(self.dir, f"experiences_{self._shard_index:03d}.npz")
        arrays = {
            "obs": np.asarray(obs, dtype=np.float32),
            "global": np.asarray(gobs, dtype=np.float32),
            "action": np.asarray(act, dtype=np.int64),
            "reward": np.asarray(rew, dtype=np.float32),
            "value": np.zeros(len(rew), dtype=np.float32),
            "done": np.asarray(done, dtype=np.float32),
        }
        np.savez(path, **arrays)
        self._shard_index += 1
        self._experiences = []

    # ── 元信息（供训练脚本自动推断维度） ─────────────────────

    def flush_meta(self, n_actions: int | None = None, note: str = "") -> None:
        obs_dim = self.encoder.dim() if self.encoder else None
        global_dim = None
        n_tls = max_phases = max_stages = max_edges = edge_count = None
        if self.encoder:
            n_tls = len(self.encoder.tls_ids)
            max_phases = self.encoder.max_phases
            max_stages = self.encoder.max_stages
            max_edges = self.encoder.max_edges
            global_dim = obs_dim * max(1, n_tls)
            edge_count = sum(len(v) for v in self.encoder._approach_edges.values())
        meta = {
            "n_actions": n_actions or (self.encoder.max_stages if self.encoder else None),
            "n_tls": n_tls, "max_phases": max_phases, "max_stages": max_stages,
            "max_edges": max_edges,
            "obs_dim": obs_dim, "global_dim": global_dim, "edge_count": edge_count,
            "edge_interval": self.edge_interval,
            "recorded_at": datetime.now().isoformat(), "note": note,
        }
        with open(os.path.join(self.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def close(self) -> None:
        self._flush_experiences()
        if not self._edge_file.closed:
            self._edge_file.close()

    def status(self) -> dict:
        return {"dir": self.dir, "edge_rows": self._edge_steps,
                "experience_shards": self._shard_index,
                "buffered_experiences": len(self._experiences)}
