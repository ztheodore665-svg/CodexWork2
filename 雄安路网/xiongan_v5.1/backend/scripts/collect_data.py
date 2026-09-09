"""采集真实路网训练数据：方案二 ε-贪心探索 + 在线学习录制 MAPPO 经验与 STGCN 边特征。

用于"换路网重训"：在新路网上跑一段 ε-贪心探索仿真 → 自动产出
edge_features.csv、experiences_*.npz、meta.json → 再用离线训练脚本训练。

用法（默认雄安路网）：
    python scripts/collect_data.py --steps 6000 --record-dir data/run1 \
        --routes C:/Users/27773/Desktop/xiongan/network/routes_train.rou.xml \
        --explore 0.2 --save-prefix models/weights/mappo_online
换路网：
    python scripts/collect_data.py --steps 6000 --record-dir data/netA \
        --routes D:/net/routes.rou.xml --explore 0.3 \
        --save-prefix models/weights/netA_online \
        --net D:/net/road.net.xml [--add D:/net/timing.xml]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.datacollector import DataCollector
from app.core.engine import Engine
from app.schemes.base import SchemeContext
from app.schemes.scheme2.controller import Scheme2Controller

XIONGAN = "C:/Users/27773/Desktop/xiongan"
DEFAULT_NET = os.path.join(XIONGAN, "network", "base_network.net.xml")
DEFAULT_ADD = os.path.join(XIONGAN, "network", "timing_safe.xml")
BASE_RECORD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=int, default=2000, help="仿真步数")
    p.add_argument("--record-dir", default=os.path.join(BASE_RECORD_DIR, "run1"),
                   help="录制目录（产出 edge_features.csv / experiences_*.npz / meta.json）")
    p.add_argument("--routes", default="routes_clean700.rou.xml", help="车流 rou.xml")
    p.add_argument("--explore", type=float, default=0.2, help="探索率 ε")
    p.add_argument("--save-prefix", default="models/weights/mappo_online",
                   help="在线权重保存前缀")
    p.add_argument("--net", default=DEFAULT_NET, help="路网 net.xml（默认雄安路网）")
    p.add_argument("--add", default=DEFAULT_ADD, help="附加文件 add.xml（默认雄安 timing_safe.xml）")
    args = p.parse_args()

    net_file = args.net
    routes = [args.routes]
    add = [args.add]
    for f in [net_file, args.routes] + add:
        if not os.path.exists(f):
            print(f"[error] 文件不存在: {f}")
            sys.exit(1)

    eng = Engine()
    eng.connect(net_file, routes, add, begin=0, end=max(100, args.steps), step_length=1.0)
    print(f"已连接: net={net_file} edges={len(eng.get_edge_ids())} "
          f"tls={len(eng.get_tls_ids())}")

    ctx = SchemeContext(engine=eng, config={
        "torch_available": True, "mode": "auto",
        "explore_epsilon": args.explore, "record_dir": args.record_dir,
        "save_dir": args.save_prefix,
    })
    ctrl = Scheme2Controller(ctx)
    ctrl.init()
    dc = DataCollector(eng)
    print(f"方案二模式={ctrl.mode} 探索率={args.explore} "
          f"录制目录={args.record_dir} 在线权重={args.save_prefix}")

    t0 = time.perf_counter()
    for step in range(1, args.steps + 1):
        eng.step()
        ctrl.on_step()
        if step % 1000 == 0:
            ov = dc.overall()
            rw = ctrl.mappo._last_rewards[-500:]
            rw_mean = sum(rw) / len(rw) if rw else 0.0
            print(f"step {step:5d}: veh={ov['vehicle_count']:3d} "
                  f"speed={ov['avg_speed']:.1f} "
                  f"wait={ov['avg_waiting_time']:.0f}s eps={ctrl.explore_epsilon:.3f} "
                  f"rew={rw_mean:+.3f} updates={ctrl.mappo._updates}")
    elapsed = time.perf_counter() - t0

    ctrl.cleanup()   # 关闭录制器（写盘 npz/csv）并保存在线权重
    eng.close()
    print(f"\n完成 {args.steps} 步, 耗时 {elapsed:.0f}s ({args.steps / elapsed:.0f} 步/秒)")
    rec_status = ctrl.recorder.status() if ctrl.recorder else None
    print(f"录制: {rec_status}")
    print(f"在线权重: {args.save_prefix}_actor.pt / {args.save_prefix}_critic.pt")
    if os.path.exists(os.path.join(args.record_dir, "meta.json")):
        print("meta.json:", os.path.join(args.record_dir, "meta.json"))


if __name__ == "__main__":
    main()
