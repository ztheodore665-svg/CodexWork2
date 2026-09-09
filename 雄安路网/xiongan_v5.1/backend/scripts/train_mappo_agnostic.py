"""路网无关 MAPPO 训练：多随机路网 × 多车流域随机化轮换训练同一模型。

观测为路网无关固定 18 维（agnostic 模式），动作"保持/推进"；
多个不同几何/车道/seed 的随机路网轮换训练 → 策略学的是"路口级通用决策"，
可在**未见过**的路网（不同路口数/拓扑）上直接部署使用（critic 仅训练期使用）。

用法：
    # 1. 生成随机路网（见 gen_random_networks.py）
    # 2. 训练
    python scripts/train_mappo_agnostic.py \
        --networks-dir data/networks/train --rounds 8 --steps 4000 \
        --out models/weights/mappo_agnostic --cpu-ratio 0.6
    # 3. 断点续训
    python scripts/train_mappo_agnostic.py --networks-dir data/networks/train \
        --rounds 8 --steps 4000 --out models/weights/mappo_agnostic \
        --resume models/weights/mappo_agnostic_ckpt.pt
"""

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine import Engine
from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import N_ACTIONS, Scheme2Controller
from app.schemes.scheme2.encoder import StateEncoder
from app.schemes.scheme2.mappo import MAPPOAgent


def make_agent(engine, entropy=0.1, update_interval=300, init=None):
    enc = StateEncoder(SchemeContext(engine=engine), obs_mode="agnostic")
    agent = MAPPOAgent(obs_dim=enc.dim(), n_actions=N_ACTIONS,
                       mode="train", torch_ok=True,
                       global_dim=enc.dim() * max(1, len(enc.tls_ids)),
                       entropy=entropy, update_interval=update_interval)
    if init:
        print(f"[agent] 加载初始权重 {init}: {agent.load(init)}")
    return agent


def discover_scenarios(networks_dir: str) -> list[tuple[str, str]]:
    """返回 [(路网目录, 车流文件路径)]，按 路网→车流 轮换。"""
    nets = sorted(glob.glob(os.path.join(networks_dir, "*")))
    nets = [d for d in nets if os.path.isdir(d)]
    if not nets:
        print(f"[error] 未找到训练路网目录: {networks_dir}（先跑 gen_random_networks.py）")
        sys.exit(1)
    scenes = []
    for d in nets:
        net_file = os.path.join(d, "net.net.xml")
        assert os.path.exists(net_file), f"缺少路网: {net_file}"
        trips = sorted(glob.glob(os.path.join(d, "*.rou.xml")))
        if not trips:
            print(f"[error] 路网 {d} 缺少车流 *.rou.xml")
            sys.exit(1)
        for t in trips:
            scenes.append((net_file, t))
    print(f"[scenes] {len(scenes)} 个场景（{len(nets)} 路网 × 多车流）")
    return scenes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--networks-dir", default="data/networks/train",
                   help="训练路网目录（每个子目录一个路网 net.net.xml + *.rou.xml 车流）")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--steps", type=int, default=5000)
    p.add_argument("--out", default="models/weights/mappo_agnostic")
    p.add_argument("--init", default=None)
    p.add_argument("--resume", default=None)
    p.add_argument("--eps0", type=float, default=0.4)
    p.add_argument("--update-interval", type=int, default=300)
    p.add_argument("--entropy", type=float, default=0.4)
    p.add_argument("--entropy-min", type=float, default=0.02)
    p.add_argument("--entropy-decay", type=float, default=0.75)
    p.add_argument("--cpu-ratio", type=float, default=0.3,
                   help="PyTorch 使用的 CPU 线程比例（默认 0.3，控制训练负载）")
    p.add_argument("--ckpt-every", type=int, default=500,
                   help="场景内断点保存间隔（步）；默认每 500 步保存一次，中断最多丢 500 步")
    args = p.parse_args()

    try:
        import torch
        n_threads = max(1, int((os.cpu_count() or 4) * args.cpu_ratio))
        torch.set_num_threads(n_threads)
        print(f"[cpu] torch 线程 {n_threads}（{args.cpu_ratio * 100:.0f}% × "
              f"{os.cpu_count() or 4} 核）")
    except Exception:  # noqa: BLE001
        pass

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    ckpt_path = f"{args.out}_ckpt.pt"
    scenes = discover_scenarios(args.networks_dir)
    n_scenes = len(scenes)

    agent = None
    start_rnd, start_si, eps = 1, 0, args.eps0
    if args.resume:
        if not os.path.exists(args.resume):
            print(f"[resume] 断点文件不存在: {args.resume}")
            sys.exit(1)
        agent = MAPPOAgent(obs_dim=1, n_actions=1, mode="train",
                           torch_ok=True, global_dim=1)
        extra = agent.load_checkpoint(args.resume)
        if extra is None:
            print(f"[resume] 断点加载失败: {args.resume}")
            sys.exit(1)
        start_rnd = int(extra.get("round", 1))
        start_si = int(extra.get("scenario", 0))
        eps = float(extra.get("eps", args.eps0))
        agent.hp["update_interval"] = args.update_interval
        agent.mode = "train"
        print(f"[resume] 从断点继续: round={start_rnd} scenario={start_si} "
              f"eps={eps:.3f} entropy={agent.hp.get('entropy', args.entropy):.3f} "
              f"updates={agent._updates}")
        if start_rnd > args.rounds:
            print("[resume] 断点进度已超过 --rounds")
            return
    elif args.init:
        print("[init] 注意：--init 只加载权重，--resume 才是完整断点续训")

    t0 = time.perf_counter()
    for rnd in range(start_rnd, args.rounds + 1):
        if agent is not None and (rnd > start_rnd or (rnd == start_rnd and start_si == 0)):
            agent.hp["entropy"] = max(args.entropy_min,
                                      args.entropy * (args.entropy_decay ** rnd))
        si0 = start_si if rnd == start_rnd else 0
        for si in range(si0, n_scenes):
            net_file, routes_file = scenes[si]
            eng = Engine()
            eng.connect(net_file, [routes_file], [], begin=0, end=args.steps,
                        step_length=1.0)
            if agent is None:
                agent = make_agent(eng, entropy=args.entropy,
                                   update_interval=args.update_interval,
                                   init=args.init)
                print(f"[agent] obs_dim={agent.obs_dim} n_actions={agent.n_actions} "
                      f"global_dim={agent.global_dim}")

            ctx = SchemeContext(engine=eng, config={
                "torch_available": True, "mode": "mappo",
                "obs_mode": "agnostic",
                "mappo_agent": agent,
                "explore_epsilon": eps, "explore_min": 0.03,
                "explore_decay": 1.0,
                "save_dir": args.out,
            })
            c = Scheme2Controller(ctx)
            c.init()
            for step in range(1, args.steps + 1):
                eng.step()
                c.on_step()
                # 场景内密集断点：中断最多丢 --ckpt-every 步（进度仍指向当前场景，
                # 恢复时从该场景重跑剩余步数，权重为最新）
                if args.ckpt_every > 0 and step % args.ckpt_every == 0:
                    agent.save_checkpoint(ckpt_path, {
                        "round": rnd, "scenario": si, "eps": eps,
                        "entropy": agent.hp.get("entropy")})
                if step % 1000 == 0:
                    rw = agent._last_rewards[-400:]
                    rw_mean = sum(rw) / len(rw) if rw else 0.0
                    rw_std = ((sum((r - rw_mean) ** 2 for r in rw) / len(rw)) ** 0.5
                              if rw else 0.0)
                    print(f"R{rnd}S{si} {os.path.basename(os.path.dirname(net_file))} "
                          f"{os.path.basename(routes_file)} step {step:5d} "
                          f"tls={len(eng.get_tls_ids()):3d} "
                          f"veh={len(eng.get_vehicle_ids()):3d} eps={eps:.3f} "
                          f"rew={rw_mean:+.3f}±{rw_std:.3f} upd={agent._updates}")
            c.cleanup()
            eng.close()
            print(f"--- R{rnd}S{si} 完成, 累计更新 {agent._updates} ---")
            nxt_rnd, nxt_si = ((rnd, si + 1) if si + 1 < n_scenes else (rnd + 1, 0))
            agent.save_checkpoint(ckpt_path, {
                "round": nxt_rnd, "scenario": nxt_si, "eps": eps,
                "entropy": agent.hp.get("entropy")})
            print(f"[checkpoint] 已保存断点 {ckpt_path} (进度 R{nxt_rnd}S{nxt_si})")
        eps = max(0.03, eps * 0.9)
        agent.save(args.out)
        print(f"[round {rnd}/{args.rounds}] 保存权重 {args.out}_actor/critic.pt")

    print(f"训练完成, 总更新 {agent._updates} 次, 耗时 {time.perf_counter()-t0:.0f}s")


if __name__ == "__main__":
    main()
