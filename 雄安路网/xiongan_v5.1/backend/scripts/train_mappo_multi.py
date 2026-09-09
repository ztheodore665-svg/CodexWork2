"""多场景正统 MAPPO 训练：多种车流轮换训练同一模型（通用路网）。

原理：单一均匀车流下模型易找到"始终某相位"的局部满意解；
多种需求（稀疏/低/中/高）轮换训练，固定策略会在部分场景失效，
迫使模型学习状态依赖的控制，避免策略坍缩。

通用性（适用于任意符合 SUMO 标准的路网）：
    - 路网/车流/附加文件全部由 --net/--routes/--add 指定，无硬编码；
    - 观测维度、绿灯阶段数、过渡相位全部按运行时路网自动解析；
    - 权重按路网重训（不同路网维度不同，直接复用权重会失败）。

断点续训：
    每个场景完成后自动保存 {out}_ckpt.pt（权重 + 优化器 + 训练进度）。
    中断后可随时用 --resume {out}_ckpt.pt 从断点继续，无需从头开始。
    （--init 只加载权重，不恢复优化器与进度，仅用于冷启动续训。）

用法（默认雄安路网，可直接跑）：
    python scripts/train_mappo_multi.py --rounds 8 --steps 4000 \
        --out models/weights/mappo_act_full --eps0 0.3 --update-interval 200 \
        --entropy 0.4 --entropy-min 0.02 --cpu-ratio 0.6
换路网：
    python scripts/train_mappo_multi.py --net D:/net/road.net.xml \
        --routes D:/net/r1.rou.xml D:/net/r2.rou.xml D:/net/r3.rou.xml \
        [--add D:/net/timing.xml] --out models/weights/netA \
        --rounds 8 --steps 4000 --cpu-ratio 0.6
断点续训：
    python scripts/train_mappo_multi.py --rounds 8 --steps 4000 \
        --out models/weights/mappo_act_full --resume models/weights/mappo_act_full_ckpt.pt
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine import Engine
from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import N_ACTIONS, Scheme2Controller
from app.schemes.scheme2.encoder import StateEncoder
from app.schemes.scheme2.mappo import MAPPOAgent

XIONGAN = "C:/Users/27773/Desktop/xiongan"
DEFAULT_NET = os.path.join(XIONGAN, "network", "base_network.net.xml")
DEFAULT_ADD = [os.path.join(XIONGAN, "network", "timing_safe.xml")]
DEFAULT_ROUTES = [os.path.join(XIONGAN, "network", f) for f in (
    "routes_div_sparse.rou.xml",   # 800/40000s 稀疏
    "routes_div_low.rou.xml",      # 400/40000s 低
    "routes_div_med.rou.xml",      # 1500/30000s 中
    "routes_div_high.rou.xml",     # 4000/20000s 高（最拥堵）
    "routes_clean700.rou.xml",     # 演示/评估场景
)]


def make_agent(engine, entropy=0.1, update_interval=300, init=None):
    enc = StateEncoder(SchemeContext(engine=engine))
    agent = MAPPOAgent(obs_dim=enc.dim(), n_actions=N_ACTIONS,
                       mode="train", torch_ok=True,
                       global_dim=enc.dim() * max(1, len(enc.tls_ids)),
                       entropy=entropy, update_interval=update_interval)
    if init:
        print(f"[agent] 加载初始权重 {init}: {agent.load(init)}")
    return agent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--net", default=DEFAULT_NET, help="路网 net.xml（默认雄安路网）")
    p.add_argument("--routes", nargs="*", default=DEFAULT_ROUTES,
                   help="车流 rou.xml 列表（多场景轮换；默认雄安 5 场景）")
    p.add_argument("--add", nargs="*", default=DEFAULT_ADD,
                   help="附加文件 add.xml（信号程序等，可多个；默认雄安 timing_safe.xml）")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--steps", type=int, default=5000)
    p.add_argument("--out", default="models/weights/mappo_20i")
    p.add_argument("--init", default=None, help="冷启动续训：加载该前缀权重（无优化器/进度）")
    p.add_argument("--resume", default=None,
                   help="断点续训：加载该 checkpoint.pt（权重+优化器+进度）并继续")
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

    net_file = args.net
    routes_list = list(args.routes) or DEFAULT_ROUTES
    add_list = list(args.add) or []

    # 限制 torch 线程数，避免训练吃满 CPU（SUMO 仿真本身单线程）
    try:
        import torch
        n_threads = max(1, int((os.cpu_count() or 4) * args.cpu_ratio))
        torch.set_num_threads(n_threads)
        print(f"[cpu] torch 线程 {n_threads}（{args.cpu_ratio * 100:.0f}% × "
              f"{os.cpu_count() or 4} 核）")
    except Exception:  # noqa: BLE001
        pass

    for f in [net_file] + routes_list + add_list:
        if not os.path.exists(f):
            print(f"[error] 文件不存在: {f}")
            sys.exit(1)
    print(f"[net] {net_file}")
    print(f"[routes] {routes_list}")
    print(f"[add] {add_list}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    ckpt_path = f"{args.out}_ckpt.pt"

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
            print(f"[resume] 断点加载失败（维度不匹配或文件损坏）: {args.resume}")
            sys.exit(1)
        start_rnd = int(extra.get("round", 1))
        start_si = int(extra.get("scenario", 0))
        eps = float(extra.get("eps", args.eps0))
        # 续训以 CLI 为准：更新间隔、训练模式；熵从断点继续
        agent.hp["update_interval"] = args.update_interval
        agent.mode = "train"
        print(f"[resume] 从断点继续: round={start_rnd} scenario={start_si} "
              f"eps={eps:.3f} entropy={agent.hp.get('entropy', args.entropy):.3f} "
              f"updates={agent._updates}")
        if start_rnd > args.rounds:
            print("[resume] 断点进度已超过 --rounds；如需继续请增大 --rounds")
            return
    elif args.init:
        print("[init] 注意：--init 只加载权重（无优化器/进度），--resume 才是完整断点续训")

    n_scenarios = len(routes_list)
    t0 = time.perf_counter()
    for rnd in range(start_rnd, args.rounds + 1):
        # 每轮进入时按轮次衰减熵（断点中途恢复时不重复衰减，熵以断点值为准）
        if agent is not None and (rnd > start_rnd or (rnd == start_rnd and start_si == 0)):
            agent.hp["entropy"] = max(args.entropy_min,
                                      args.entropy * (args.entropy_decay ** rnd))
        si0 = start_si if rnd == start_rnd else 0
        for si in range(si0, n_scenarios):
            routes_file = routes_list[si]
            routes = [routes_file]
            eng = Engine()
            eng.connect(net_file, routes, add_list, begin=0, end=args.steps,
                        step_length=1.0)
            if agent is None:
                agent = make_agent(eng, entropy=args.entropy,
                                   update_interval=args.update_interval,
                                   init=args.init)
                print(f"[agent] obs_dim={agent.obs_dim} n_actions={agent.n_actions} "
                      f"global_dim={agent.global_dim}")

            ctx = SchemeContext(engine=eng, config={
                "torch_available": True, "mode": "mappo",
                "mappo_agent": agent,
                "explore_epsilon": eps, "explore_min": 0.03,
                "explore_decay": 1.0,   # 探索率由本脚本外部衰减
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
                    print(f"R{rnd}S{si} {os.path.basename(routes_file)} step {step:5d} "
                          f"veh={len(eng.get_vehicle_ids()):3d} "
                          f"eps={eps:.3f} rew={rw_mean:+.3f}±{rw_std:.3f} "
                          f"upd={agent._updates}")
            c.cleanup()
            eng.close()
            print(f"--- R{rnd}S{si} 完成, 累计更新 {agent._updates} ---")
            # 断点：每个场景完成即保存完整训练状态，可随时中断续训
            nxt_rnd, nxt_si = ((rnd, si + 1) if si + 1 < n_scenarios
                               else (rnd + 1, 0))
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
