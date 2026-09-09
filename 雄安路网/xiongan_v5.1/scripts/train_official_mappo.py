"""简化 MAPPO 训练脚本（官方方案 official）：在官方三档配时间学习“选档策略”。

设定（真实信号机语义，非逐秒控制）：
- 每 DECISION（默认 60s）为每个信号路口独立决策一次：动作 = {0:早高峰,1:平峰,2:晚高峰}
- 决策 = 把该路口信号机整程序热替换为官方对应档（与 official 方案运行时完全一致）
- 观测（每路口，与运行时一致）：EW 臂组排队、NS 臂组排队、总排队、（未来可加时段特征）
- 奖励（每路口）：一个决策窗内 官方臂组总排队 减少 → 正奖励
      r = (q_before − q_after)/10 − 0.3·[本次是否切换档位]     （切换惩罚抑制抖档）
- 优化：REINFORCE（reward-to-go、episode 均值作 baseline），参数共享小策略网络；
  用 numpy 实现（无 torch 依赖），权重保存为 JSON，official 方案 mode=mappo 时加载做 argmax。

用法：
  python scripts/train_official_mappo.py [--episodes 8] [--steps 900]
      [--decision 60] [--lr 0.02] [--weights backend/../models/weights/official_plan_policy.json]
说明：
- 每个 episode 独立起一次 SUMO（headless），结束后关闭；训练收敛需多次 episode，
  脚本输出每 episode 平均奖励轨迹供观察（本实现为“可运行、可扩展”的简化 RL，
  大规模收敛建议在服务器上加大 episodes）。
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "backend"))

PROG_JSON = os.path.join(BACKEND, "backend", "data", "official_phase_programs.json")
NET = os.path.join(BACKEND, "networks", "network", "base_network.net.xml")
ROUTE = os.path.join(BACKEND, "networks", "network", "routes_clean700.rou.xml")
ADD = os.path.join(BACKEND, "networks", "network", "timing.add.xml")

EW_SET = {"E", "NE", "SE"}
NS_SET = {"N", "NW", "S", "SW"}


class SmallPolicy:
    """MLP(tanh) → 3 logits。w 扁平化便于有限差分梯度。"""

    def __init__(self, obs_dim=3, n_act=3, hidden=16, seed=0):
        rng = np.random.default_rng(seed)
        n1, n2 = obs_dim + 1, hidden + 1
        self.shapes = [(n1, hidden), (n2, n_act)]
        flat = np.concatenate([rng.standard_normal(self.shapes[0]).ravel() * 0.3,
                               rng.standard_normal(self.shapes[1]).ravel() * 0.3])
        self.w = flat
        self.obs_dim, self.n_act = obs_dim, n_act

    def _w(self):
        n = int(self.shapes[0][0] * self.shapes[0][1])
        return self.w[:n].reshape(self.shapes[0]), self.w[n:].reshape(self.shapes[1])

    def prob(self, obs):
        w1, w2 = self._w()
        h = np.tanh(np.concatenate([obs, [1.0]]) @ w1)
        logits = np.concatenate([h, [1.0]]) @ w2
        logits -= logits.max()
        e = np.exp(logits)
        return e / e.sum()

    def act(self, obs, greedy=False):
        p = self.prob(np.asarray(obs, dtype=np.float64))
        a = int(np.argmax(p)) if greedy else int(
            np.random.choice(self.n_act, p=p))
        return a, math.log(max(p[a], 1e-12)), p

    def grad_logp(self, obs, action):
        """∇_θ log π(a|o)（中心有限差分，参数量小，速度足够）。"""
        n = len(self.w)
        g = np.zeros(n)
        eps = 1e-5
        obs = np.asarray(obs, dtype=np.float64)
        for i in range(n):
            for sgn in (1.0, -1.0):
                w0 = self.w.copy()
                w0[i] += sgn * eps
                p = _prob_with(w0, self.shapes, obs)
                g[i] += sgn * math.log(max(p[action], 1e-12)) / (2 * eps)
        return g

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"w": self.w.tolist(), "obs_dim": self.obs_dim,
                       "n_act": self.n_act, "hidden": self.shapes[0][1],
                       "algo": "REINFORCE-official-plan-v1"}, f)
        return path

    def load(self, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.w = np.asarray(d["w"], dtype=np.float64)
        self.obs_dim, self.n_act = int(d["obs_dim"]), int(d["n_act"])
        hidden = int(d.get("hidden", 16))
        self.shapes = [(self.obs_dim + 1, hidden), (hidden + 1, self.n_act)]


def _prob_with(w, shapes, obs):
    w1, w2 = w, None
    n = int(shapes[0][0] * shapes[0][1])
    w1 = w[:n].reshape(shapes[0]); w2 = w[n:].reshape(shapes[1])
    h = np.tanh(np.concatenate([obs, [1.0]]) @ w1)
    logits = np.concatenate([h, [1.0]]) @ w2
    logits -= logits.max()
    e = np.exp(logits)
    return e / e.sum()


def arm_queues(engine, arms):
    ew = ns = 0.0
    for edge, comp in arms.items():
        try:
            q = float(engine.get_edge_queue(edge))
        except Exception:  # noqa: BLE001
            q = 0.0
        if comp in EW_SET:
            ew += q
        elif comp in NS_SET:
            ns += q
    return ew, ns, ew + ns


def run_episode(policy, plans_data, steps, decision, greedy=False):
    """跑一个 episode：收集 (obs, action, reward, switched)。返回轨迹。"""
    from app.core.engine import Engine
    eng = Engine()
    eng.connect(NET, [ROUTE], [ADD], begin=0, end=steps + 40, step_length=1.0)
    tls_ids = [t for t in eng.get_tls_ids() if t in plans_data]
    cur = {}
    traj = []
    try:
        for _ in range(60):          # 预热：车流进入
            eng.step()
        t = 60
        while t < steps:
            for tid in tls_ids:
                arms = plans_data[tid].get("arms") or {}
                ew, ns, tot = arm_queues(eng, arms)
                obs = [ew / 40.0, ns / 40.0, tot / 80.0]
                a, logp, _ = policy.act(obs, greedy=greedy)
                switched = cur.get(tid, a) != a
                sched = [(ph["dur"], ph["state"])
                         for ph in plans_data[tid]["plans"][a]["phases"]]
                try:
                    eng.set_tls_phase_schedule(tid, sched)
                except Exception:  # noqa: BLE001
                    pass
                cur[tid] = a
                traj.append({"tid": tid, "obs": obs, "action": a,
                             "q_before": tot, "switched": switched})
            for _ in range(decision):
                eng.step()
                t += 1
            # 决策窗结束：给每个路口发奖励
            for item in traj[-len(tls_ids):]:
                arms = plans_data[item["tid"]].get("arms") or {}
                _e, _n, tot = arm_queues(eng, arms)
                item["reward"] = (item["q_before"] - tot) / 10.0 \
                    - (0.3 if item["switched"] else 0.0)
    finally:
        eng.close()
    return traj


def update_policy(policy, traj, lr):
    """REINFORCE：reward-to-go（γ=1）+ episode 均值 baseline。"""
    if not traj:
        return 0.0
    rewards = np.array([it.get("reward", 0.0) for it in traj], dtype=np.float64)
    g = np.zeros_like(policy.w)
    for i, it in enumerate(traj):
        g += (rewards[i:].sum() - rewards.mean()) * policy.grad_logp(it["obs"],
                                                                     it["action"])
    policy.w += lr * g / max(1.0, len(traj))
    return float(rewards.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=6)
    ap.add_argument("--steps", type=int, default=900)
    ap.add_argument("--decision", type=int, default=60)
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--weights", default=os.path.join(
        BACKEND, "backend", "models", "weights", "official_plan_policy.json"))
    args = ap.parse_args()

    plans_data = json.load(open(PROG_JSON, encoding="utf-8"))
    policy = SmallPolicy()
    print(f"简化 MAPPO (official 选档) 训练: episodes={args.episodes} "
          f"steps={args.steps} decision={args.decision} lr={args.lr}\n"
          f"输出: {args.weights}", flush=True)
    t0 = time.time()
    hist = []
    for ep in range(1, args.episodes + 1):
        traj = run_episode(policy, plans_data, args.steps, args.decision)
        mean_r = update_policy(policy, traj, args.lr)
        hist.append(mean_r)
        print(f"[ep {ep}/{args.episodes}] decisions={len(traj)} "
              f"mean_reward={mean_r:.3f} ({(time.time()-t0)/ep:.0f}s/ep)", flush=True)
    os.makedirs(os.path.dirname(args.weights), exist_ok=True)
    policy.save(args.weights)
    print(f"完成 {time.time()-t0:.0f}s -> {args.weights}")
    print(f"episode 平均奖励: {[round(v, 3) for v in hist]}")


if __name__ == "__main__":
    main()
