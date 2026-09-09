"""验证方案二训练模型：动作分布（非退化）+ 真实路网效果对比（通用路网）。

三行对比：
    1. 固定配时基线（控制器不干预，SUMO 静态程序自动轮转）
    2. SCOOT 自适应
    3. 方案二（训练模型，贪心推理）

用法：
    python scripts/verify_model.py --prefix models/weights/mappo_act_full --steps 2200
换路网：
    python scripts/verify_model.py --prefix models/weights/netA --steps 2200 \
        --net D:/net/road.net.xml --routes D:/net/eval.rou.xml \
        [--add D:/net/timing.xml]
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.datacollector import DataCollector
from app.core.engine import Engine
from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import Scheme2Controller

XIONGAN = "C:/Users/27773/Desktop/xiongan"
DEFAULT_NET = os.path.join(XIONGAN, "network", "base_network.net.xml")
DEFAULT_ROUTES = [os.path.join(XIONGAN, "network", "routes_clean700.rou.xml")]
DEFAULT_ADD = [os.path.join(XIONGAN, "network", "timing_safe.xml")]


def run(cfg, steps, tag, net_file, routes_list, add_list, warmup=1000):
    """窗口平均：warmup 之后每 50 步采样一次指标取均值（降低单点噪声）。"""
    eng = Engine()
    eng.connect(net_file, routes_list, add_list, begin=0, end=steps, step_length=1.0)
    ctx = SchemeContext(engine=eng, config=cfg)
    c = Scheme2Controller(ctx)
    c.init()
    dc = DataCollector(eng)
    acc = {"vehicle_count": [], "avg_speed": [], "avg_waiting_time": []}
    for s in range(1, steps + 1):
        eng.step()
        c.on_step()
        if s >= warmup and s % 50 == 0:
            ov = dc.overall()
            for k in acc:
                acc[k].append(ov[k])
    n = len(acc["vehicle_count"]) or 1
    print(f"{tag}: veh={sum(acc['vehicle_count']) / n:6.1f} "
          f"speed={sum(acc['avg_speed']) / n:.2f} "
          f"wait={sum(acc['avg_waiting_time']) / n:.0f}s (窗口均值)")
    return c, eng


def action_distribution(c, n_samples=100):
    """各路口贪心动作分布：基于真实仿真轨迹采样（非退化=动作随时间变化）。

    固定观测下的 argmax 必然是单点（确定性策略），测不出状态依赖；
    这里统计 infer 阶段真实轨迹上每个路口的贪心动作集合。
    """
    from collections import defaultdict
    print("各路口贪婪动作分布（真实轨迹采样，非退化=动作随时间变化）:")
    per_tls: dict[str, Counter] = defaultdict(Counter)
    for tid, a in getattr(c, "_action_log", []):
        per_tls[tid][a] += 1
    degenerate = True
    for tid in c.encoder.tls_ids:
        acts = per_tls.get(tid, Counter())
        if len(acts) > 1:
            degenerate = False
        n_stages = len(c.encoder.green_phases.get(tid, [0]))
        print(f"  tls={tid} 绿灯阶段数={n_stages} 分布={dict(acts)}")
    return not degenerate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", default="models/weights/mappo_trained",
                   help="模型权重前缀（_actor.pt/_critic.pt）")
    p.add_argument("--steps", type=int, default=2000, help="仿真步数")
    p.add_argument("--net", default=DEFAULT_NET, help="路网 net.xml（默认雄安路网）")
    p.add_argument("--routes", nargs="*", default=DEFAULT_ROUTES,
                   help="评估车流 rou.xml 列表（默认雄安 routes_clean700）")
    p.add_argument("--add", nargs="*", default=DEFAULT_ADD,
                   help="附加文件 add.xml（默认雄安 timing_safe.xml）")
    p.add_argument("--obs-mode", default="legacy",
                   help="观测模式：legacy（雄安路网版）或 agnostic（路网无关版）")
    p.add_argument("--warmup", type=int, default=1000,
                   help="预热步数（此前的指标不计入均值；短车流场景请调小）")
    args = p.parse_args()

    net_file = args.net
    routes_list = list(args.routes) or DEFAULT_ROUTES
    add_list = list(args.add) or []
    prefix, steps = args.prefix, args.steps

    for f in [net_file] + routes_list + add_list:
        if not os.path.exists(f):
            print(f"[error] 文件不存在: {f}")
            sys.exit(1)
    if not (os.path.exists(f"{prefix}_actor.pt") and os.path.exists(f"{prefix}_critic.pt")):
        print(f"[error] 模型权重不存在: {prefix}_actor.pt / {prefix}_critic.pt")
        sys.exit(1)

    c, eng = run({"mode": "fixed"}, steps, "固定配时(安全)基线",
                 net_file, routes_list, add_list, warmup=args.warmup)
    eng.close()
    c2, eng = run({"torch_available": True, "mode": "scoot"}, steps, "SCOOT自适应",
                  net_file, routes_list, add_list, warmup=args.warmup)
    eng.close()
    cfg = {"torch_available": True, "mode": "auto",
           "obs_mode": args.obs_mode,
           "mappo_weights": prefix, "stgcn_weights": "models/weights/stgcn.pt"}
    c3, eng = run(cfg, steps, "方案二(训练模型)", net_file, routes_list, add_list,
                  warmup=args.warmup)
    st = c3.handle_action("get_status", {})
    print(f"mode={c3.mode} mappo_loaded={st['mappo']['model_loaded']} "
          f"updates={c3.mappo._updates}")
    ok = action_distribution(c3)
    print("动作分布非退化:", ok)
    eng.close()


if __name__ == "__main__":
    main()
