"""生成"路网无关"训练用随机路网 + 车流（域随机化）。

用 SUMO netgenerate 批量生成带信号灯的路网（grid / spider 两种拓扑，
不同 seed / 边长 / 车道数），并用 randomTrips + duarouter 生成车流。

约定（与 train_mappo_agnostic.py 配套）：
- 训练路网统一 16 路口（grid 4×4），保证共享 Critic 维度一致；
- 验收路网与训练不同规模/拓扑（如 grid 3×3 / 5×5、spider 13 路口），用于测泛化。

用法：
    python scripts/gen_random_networks.py [--out data/networks] [--train 6] [--dur 600]
"""

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

SUMO_HOME = os.environ.get("SUMO_HOME", "E:/")
BIN = os.path.join(SUMO_HOME, "bin")
TOOLS = os.path.join(SUMO_HOME, "tools")
NETGEN = os.path.join(BIN, "netgenerate.exe")
DUAROUTER = os.path.join(BIN, "duarouter.exe")
RANDOM_TRIPS = os.path.join(TOOLS, "randomTrips.py")


def run(cmd, tag):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        print(f"[{tag}] 失败: {' '.join(cmd)}\n{r.stderr[-500:]}")
        sys.exit(1)


def count_tls(net_file: str) -> int:
    root = ET.parse(net_file).getroot()
    return len(root.findall("tlLogic"))


def gen_net(kind: str, seed: int, net_file: str, length: int,
            lanes: int, x: int = 4, y: int = 4) -> int:
    if kind == "grid":
        cmd = [NETGEN, "--grid", f"--grid.x-number", str(x), "--grid.y-number", str(y),
               "--grid.length", str(length), "--grid.attach-length", str(length // 2),
               "--tls.guess", "true", "--tls.guess.threshold", "0",
               "--default.lanenumber", str(lanes), "--seed", str(seed),
               "--output-file", net_file]
    else:  # spider
        cmd = [NETGEN, "--spider", "--spider.arm-number", "6",
               "--spider.circle-number", "2", "--spider.space-radius", str(length),
               "--spider.attach-length", str(length),
               "--tls.guess", "true", "--tls.guess.threshold", "0",
               "--default.lanenumber", str(lanes), "--seed", str(seed),
               "--output-file", net_file]
    run(cmd, "netgenerate")
    return count_tls(net_file)


def gen_trips(net_file: str, out_prefix: str, dur: int, period: float,
              seed: int) -> None:
    trips = f"{out_prefix}_trips.xml"
    run([sys.executable, RANDOM_TRIPS, "-n", net_file, "-b", "0", "-e", str(dur),
         "-p", str(period), "--seed", str(seed), "-o", trips], "randomTrips")
    run([DUAROUTER, "-n", net_file, "-r", trips, "-o", f"{out_prefix}.rou.xml",
         "--ignore-errors", "--no-warnings"], "duarouter")
    os.remove(trips)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/networks", help="输出根目录")
    p.add_argument("--train", type=int, default=6, help="训练路网数量")
    p.add_argument("--dur", type=int, default=600, help="车流时长（仿真秒）")
    args = p.parse_args()

    train_dir = os.path.join(args.out, "train")
    eval_dir = os.path.join(args.out, "eval")
    os.makedirs(train_dir, exist_ok=True)
    os.makedirs(eval_dir, exist_ok=True)

    # 训练路网：统一 16 路口 grid（4×4），几何/车道/seed 随机化
    print("=== 生成训练路网（16 路口 grid）===")
    for i in range(args.train):
        seed = 100 + i
        length = 180 + (i % 3) * 60
        lanes = 2 + (i % 2)
        d = os.path.join(train_dir, f"net_{i:02d}")
        os.makedirs(d, exist_ok=True)
        net_file = os.path.join(d, "net.net.xml")
        n = gen_net("grid", seed, net_file, length, lanes)
        assert n == 16, f"路网 {d} 信号灯数 {n} != 16"
        # 两个车流：中流量(2s/辆) 与 高流量(1s/辆)
        gen_trips(net_file, os.path.join(d, "traffic_med"), args.dur, 2.0, seed)
        gen_trips(net_file, os.path.join(d, "traffic_high"), args.dur, 1.0, seed + 1)
        print(f"  net_{i:02d}: {n} 信号灯, 车道={lanes}, 边长={length}m, 车流×2 OK")

    # 验收路网：未参与训练的不同规模/拓扑
    print("=== 生成验收路网（未训练）===")
    specs = [
        ("eval_grid9", "grid", 9, 3, 3),
        ("eval_grid25", "grid", 25, 5, 5),
        ("eval_spider13", "spider", 13, 0, 0),
    ]
    for name, kind, expect, x, y in specs:
        d = os.path.join(eval_dir, name)
        os.makedirs(d, exist_ok=True)
        net_file = os.path.join(d, "net.net.xml")
        n = gen_net(kind, 700, net_file, 200, 2, x=x, y=y)
        assert n == expect, f"验收路网 {name} 信号灯数 {n} != {expect}"
        gen_trips(net_file, os.path.join(d, "traffic_med"), args.dur, 2.0, 701)
        print(f"  {name}: {n} 信号灯 OK")

    print(f"\n完成。训练路网 {args.train} 个（16 路口），验收路网 3 个，输出在 {args.out}")


if __name__ == "__main__":
    main()
