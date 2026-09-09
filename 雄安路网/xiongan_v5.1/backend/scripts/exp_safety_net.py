"""第1层验证：给定密度一次性投放下 安全网开/关 对比。

直接无头驱动 Engine + SafetyNet（不入 runtime/Web）：
  python scripts/exp_safety_net.py [steps] [lo_hi]
  例如: python scripts/exp_safety_net.py 900 4_8   # peak 密度

测量每 30 步：在网车辆 / 平均速度 / 累计到达 / 内部车道滞留车道数 /
最长等待 / 平均排队。末段汇总。
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
from app.core.safety_net import SafetyNet  # noqa: E402

NET_PATH = os.path.join(os.path.dirname(BACKEND), "networks",
                        "network", "base_network.net.xml")
MAX_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 900
if len(sys.argv) > 2 and "_" in sys.argv[2]:
    lo, hi = (int(x) for x in sys.argv[2].split("_"))
    PER_ROAD = (lo, hi)
else:
    PER_ROAD = (7, 12)
SAMPLE_EVERY = 30
HALT_SPEED = 0.1


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


def _write_route_file(routes) -> str:
    rng = random.Random()
    out = os.path.join(tempfile.gettempdir(),
                       f"exp_sn_{uuid.uuid4().hex[:8]}.rou.xml")
    lo, hi = PER_ROAD
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for i, edges in enumerate(routes):
        lines.append(f'    <route id="sc{i}" edges="{" ".join(edges)}"/>')
    vid = 0
    for i in range(len(routes)):
        for _ in range(rng.randint(lo, hi)):
            lines.append(f'    <vehicle id="scv{vid}" depart="0" '
                         f'departPos="random_free" route="sc{i}"/>')
            vid += 1
    lines.append("</routes>")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def _edge_from_lane(lane_id: str) -> str:
    return lane_id.rpartition("_")[0] if "_" in lane_id else lane_id


def run_variant(tag: str, safety: bool) -> list[dict]:
    routes = _routes_from_net()
    route_file = _write_route_file(routes)
    eng = Engine()
    eng.connect(NET_PATH, [route_file], begin=0, end=MAX_STEPS + 10,
                step_length=1.0)
    sn = SafetyNet(eng) if safety else None
    samples = []
    internal_stuck = {}
    try:
        for step in range(1, MAX_STEPS + 1):
            eng.step()
            if sn is not None:
                sn.on_step(step)
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
        if sn is not None:
            print(f"  [{tag}] 安全网统计: {sn.get_internal_metrics()}")
        eng.close()
    return samples


def main():
    print(f"路网: {NET_PATH}\n场景: per_road={PER_ROAD[0]}-{PER_ROAD[1]} "
          f"一次性投放  steps={MAX_STEPS}\n")
    print("=== 安全网关闭（当前行为） ===")
    off = run_variant("safety-off", safety=False)
    print("  采样点：", "; ".join(
        f"step={s['step']:>4}/spd={s['avg_speed']:>5}/arr={s['arrived']:>3}/"
        f"is={s['internal_stuck_lanes']:>2}/wait={s['max_wait']:>5}"
        for s in off))
    print("\n=== 安全网开启（防溢出+死锁清空） ===")
    on = run_variant("safety-on", safety=True)
    print("  采样点：", "; ".join(
        f"step={s['step']:>4}/spd={s['avg_speed']:>5}/arr={s['arrived']:>3}/"
        f"is={s['internal_stuck_lanes']:>2}/wait={s['max_wait']:>5}"
        for s in on))
    print("\n=== 汇总（末段最后 3 个采样点均值） ===")
    for tag, arr in (("关闭", off), ("开启", on)):
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
