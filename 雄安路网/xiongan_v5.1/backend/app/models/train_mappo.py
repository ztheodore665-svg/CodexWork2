"""MAPPO 策略训练脚本（独立运行，建议单独开窗口执行）。

用法：
    python -m app.models.train_mappo --steps 2000 --epochs 5 --obs-dim 22 --actions 3
    python -m app.models.train_mappo --data data/mappo_experiences.npz

CPU 占用：默认使用约 60% 的 CPU 线程，可用 --cpu-ratio 调整。
数据：--data 为 npz（键 obs/global/action/reward/value/done）；缺省用合成数据验证管线。
输出：--out 前缀，生成 *_actor.pt 与 *_critic.pt。
"""

import argparse
import os
import sys

import numpy as np

from app.schemes.scheme2.stgcn import torch_available

if not torch_available():
    print("[train_mappo] 需要 PyTorch：pip install torch")
    sys.exit(1)

import torch  # noqa: E402


def set_cpu_threads(ratio: float) -> int:
    cores = os.cpu_count() or 4
    n = max(1, int(cores * ratio))
    torch.set_num_threads(n)
    print(f"[train_mappo] CPU 线程 {n}/{cores}（{int(ratio * 100)}%）")
    return n


def synthetic_experiences(n: int, obs_dim: int, n_actions: int):
    rng = np.random.RandomState(42)
    obs = rng.rand(n, obs_dim).astype(np.float32)
    global_obs = rng.rand(n, obs_dim * 2).astype(np.float32)
    action = rng.randint(0, n_actions, n).astype(np.int64)
    reward = rng.randn(n).astype(np.float32) * 0.5 - 1.0
    value = np.zeros(n, dtype=np.float32)
    done = np.zeros(n, dtype=np.float32)
    return obs, global_obs, action, reward, value, done


def load_experiences(path: str):
    """加载单文件或目录下所有 npz 分片，自动推断 obs_dim/n_actions/global_dim。

    返回 (obs, gobs, action, reward, value, done, obs_dim, n_actions, global_dim)。
    """
    files = []
    if os.path.isdir(path):
        files = sorted(f for f in os.listdir(path) if f.endswith(".npz"))
        if not files:
            files = sorted(f for f in os.listdir(path) if f.endswith(".npy"))
        files = [os.path.join(path, f) for f in files]
    else:
        files = [path]
    if not files:
        raise SystemExit(f"[train_mappo] 未找到数据: {path}")
    obs, gobs, act, rew, val, done = [], [], [], [], [], []
    for f in files:
        d = np.load(f)
        n = len(d["reward"])
        obs.append(d["obs"])
        gobs.append(d["global"])
        act.append(d["action"])
        rew.append(d["reward"])
        val.append(d.get("value", np.zeros(n, dtype=np.float32)))
        done.append(d["done"])
    obs = np.concatenate(obs)
    gobs = np.concatenate(gobs)
    action = np.concatenate(act)
    reward = np.concatenate(rew)
    value = np.concatenate(val)
    done = np.concatenate(done)
    obs_dim = int(obs.shape[1])
    n_actions = int(action.max()) + 1
    global_dim = int(gobs.shape[1])
    return obs, gobs, action, reward, value, done, obs_dim, n_actions, global_dim


def train(args) -> None:
    set_cpu_threads(args.cpu_ratio)
    if args.data:
        (obs, gobs, action, reward, value, done,
         obs_dim, n_actions, global_dim) = load_experiences(args.data)
    else:
        obs, gobs, action, reward, value, done = synthetic_experiences(
            args.steps, args.obs_dim, args.actions)
        obs_dim, n_actions, global_dim = args.obs_dim, args.actions, args.obs_dim * 2
    print(f"[train_mappo] 样本数={len(obs)} obs_dim={obs_dim} "
          f"n_actions={n_actions} global_dim={global_dim}")

    from app.schemes.scheme2.mappo import MAPPOAgent
    agent = MAPPOAgent(obs_dim=obs_dim, n_actions=n_actions,
                       mode="train", torch_ok=True,
                       global_dim=global_dim,
                       update_interval=args.update_interval)
    n = len(obs)
    total_updates = 0
    for epoch in range(args.epochs):
        for step in range(n):
            agent.store(obs[step].tolist(), gobs[step].tolist(), int(action[step]),
                        float(reward[step]), 0.0, float(value[step]),
                        bool(done[step]))
            if (step + 1) % args.update_interval == 0:
                info = agent.update()
                total_updates += 1
        print(f"[train_mappo] epoch {epoch + 1}/{args.epochs} 累计更新 {total_updates} 次")
    print(f"[train_mappo] 训练完成，共 {total_updates} 次 PPO 更新")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    if agent.save(args.out):
        print(f"[train_mappo] 完成，权重保存到 {args.out}_actor.pt / {args.out}_critic.pt")
    else:
        print("[train_mappo] 保存失败（检查输出目录）")


def main() -> None:
    p = argparse.ArgumentParser(description="MAPPO 策略训练")
    p.add_argument("--data", default=None, help="经验 npz（obs/global/action/reward/value/done），缺省合成")
    p.add_argument("--steps", type=int, default=2000, help="合成样本数")
    p.add_argument("--obs-dim", type=int, default=22)
    p.add_argument("--actions", type=int, default=3)
    p.add_argument("--update-interval", type=int, default=300)
    p.add_argument("--epochs", type=int, default=1, help="对数据重复训练轮数")
    p.add_argument("--cpu-ratio", type=float, default=0.6,
                   help="使用的 CPU 线程比例（默认 0.6，约六成）")
    p.add_argument("--out", default="models/weights/checkpoint")
    train(p.parse_args())


if __name__ == "__main__":
    main()
