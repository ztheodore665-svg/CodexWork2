"""第0层归因实验：extreme 场景 一次性投放 vs 窗口化投放，对比拥堵指标。

独立无头运行（不依赖 Web 后端 / GUI），直接驱动 SUMO：
  python scripts/exp_extreme_spillback.py

测量：每 30 步采样 在网车辆 / 平均速度 / 累计到达 / 内部车道滞留车辆 /
最长等待 / 平均排队。两种投放形态各跑 MAX_STEPS 步。
"""

import os
import random
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.core.engine import Engine  # noqa: E402

NET_PATH = os.path.join(os.path.dirname(BACKEND), "networks",
                        "network", "base_network.net.xml")
SCENARIO = "extreme"
PER_ROAD = (7, 12)
MAX_STEPS = 900        # 900 s = 15 min 仿真
SAMPLE_EVERY = 30
WINDOW_SEC = 150       # 窗口化投放：0~150s 内均匀出发

HALT_SPEED = 0.1
MAX_SPEED = 13.89


def _routes_from_net(max_steps=16, seed=7):
    import sumolib
    net = sumolib.net.readNet(NET_PATH)
    succ = {}
    for e in net.getEdges():
        out = []
        for conns in e.getOutgoing().values():
            for c in conns:
                if c is not None:
                    out.append(c.getTo().getID())
        succ[e.getID()] = out
    rng = random.Random(seed)
    routes = []
    for eid in sorted(succ):
        route = [eid]
        cur = eid
        for _ in range(max_steps):
            nxts = [s for s in succ.get(cur, []) if s not in route]
            if not nxts:
                break
            cur = rng.choice(nxts)
            route.append(cur)
            if not any(s not in route for s in succ.get(cur, [])):
                break
        if len(route) >= 2:
            routes.append(route)
    return routes


def _write_route_file(routes, windowed: bool) -> str:
    rng = random.Random()  # 每次随机
    out = os.path.join(tempfile.gettempdir(),
                       f"exp_extreme_{'win' if windowed else 'one'}_"
                       f"{uuid.uuid4().hex[:8]}.rou.xml")
    lo, hi = PER_ROAD
    vehs: list[tuple[float, str]] = []
    for i, edges in enumerate(routes):
        k = rng.randint(lo, hi)
        for _ in range(k):
            depart = rng.uniform(0, WINDOW_SEC) if windowed else 0.0
            vehs.append((depart, i))
    # SUMO 要求路由文件按出发时间排序，否则丢弃乱序车辆（"should be sorted"告警）
    vehs.sort(key=lambda t: t[0])
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for i, edges in enumerate(routes):
        lines.append(f'    <route id="sc{i}" edges="{" ".join(edges)}"/>')
    for vid, (depart, i) in enumerate(vehs):
        lines.append(
            f'    <vehicle id="scv{vid}" depart="{depart:.1f}" '
            f'departPos="random_free" route="sc{i}"/>')
    lines.append("</routes>")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def _edge_from_lane(lane_id: str) -> str:
    return lane_id.rpartition("_")[0] if "_" in lane_id else lane_id


def run_variant(tag: str, windowed: bool) -> list[dict]:
    routes = _routes_from_net()
    route_file = _write_route_file(routes, windowed)
    eng = Engine()
    eng.connect(NET_PATH, [route_file], begin=0, end=MAX_STEPS + 10,
                step_length=1.0)
    samples = []
    try:
        internal_stuck = {}  # lane -> 连续滞留步数
        for step in range(1, MAX_STEPS + 1):
            eng.step()
            if step % SAMPLE_EVERY != 0:
                continue
            vids = eng.get_vehicle_ids()
            vcount = len(vids)
            spd_sum = 0.0
            wait_max = 0.0
            internal_now = 0
            stopped_by_edge = {}
            for vid in vids:
                try:
                    st = eng.get_vehicle_state(vid)
                except Exception:
                    continue
                spd_sum += st["speed"]
                wait_max = max(wait_max, st["waiting_time"])
                lane = st.get("lane", "")
                if lane.startswith(":"):
                    internal_now += 1
                    if st["speed"] < HALT_SPEED:
                        internal_stuck[lane] = internal_stuck.get(lane, 0) + 1
                    else:
                        internal_stuck.pop(lane, None)
                elif st["speed"] < HALT_SPEED:
                    e = _edge_from_lane(lane)
                    stopped_by_edge[e] = stopped_by_edge.get(e, 0) + 1
            avg_speed = spd_sum / vcount if vcount else 0.0
            avg_queue = (sum(stopped_by_edge.values()) / len(stopped_by_edge)
                         if stopped_by_edge else 0.0)
            eng.get_cumulative_departed()
            samples.append({
                "tag": tag, "step": step,
                "vehicles": vcount,
                "avg_speed": round(avg_speed, 3),
                "arrived": eng.get_cumulative_arrivals(),
                "internal_stuck_lanes": len(internal_stuck),
                "internal_vehicles": internal_now,
                "max_wait": round(wait_max, 1),
                "avg_queue": round(avg_queue, 3),
            })
    finally:
        eng.close()
    return samples


def fmt_row(s: dict) -> str:
    return (f"step={s['step']:>4}  veh={s['vehicles']:>4}  "
            f"avg_spd={s['avg_speed']:>6}  arrived={s['arrived']:>4}  "
            f"internal_stuck={s['internal_stuck_lanes']:>2}  "
            f"internal_veh={s['internal_vehicles']:>2}  "
            f"max_wait={s['max_wait']:>6}  avg_queue={s['avg_queue']:>5}")


def main():
    print(f"路网: {NET_PATH}")
    print(f"场景: {SCENARIO} per_road={PER_ROAD}  steps={MAX_STEPS}  "
          f"window={WINDOW_SEC}s\n")
    print("=== 一次性投放（当前行为） ===")
    one = run_variant("one-shot", windowed=False)
    print(f"总投放: {len(one)} 采样点; 首末: "
          f"veh {one[0]['vehicles']} -> {one[-1]['vehicles']}")
    print("  采样点：", "; ".join(fmt_row(s) for s in one))
    print("\n=== 窗口化投放（150s 摊匀） ===")
    win = run_variant("windowed", windowed=True)
    print("  采样点：", "; ".join(fmt_row(s) for s in win))
    # 汇总对比
    print("\n=== 汇总（末段最后 3 个采样点均值） ===")
    for tag, arr in (("一次性", one), ("窗口化", win)):
        tail = arr[-3:]
        n = len(tail)
        avg_spd = sum(s["avg_speed"] for s in tail) / n
        arrd = tail[-1]["arrived"]
        stuck = sum(s["internal_stuck_lanes"] for s in tail) / n
        mw = max(s["max_wait"] for s in tail)
        print(f"{tag}: avg_spd={avg_spd:.3f}  arrived={arrd}  "
              f"internal_stuck={stuck:.1f}  max_wait={mw:.1f}")


if __name__ == "__main__":
    main()
