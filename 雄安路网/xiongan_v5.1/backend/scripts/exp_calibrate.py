# -*- coding: utf-8 -*-
"""峰期参数校准：固定投放（同种子）× 控制器 × 密度档 → 找"算法能疏通"的可区分区。

引擎直驱（不经 runtime/Web），固定配时基线 = SUMO 静态程序（不干预）；
自适应 = Scheme2Controller（auto：有权重→MAPPO，否则→SCOOT）。

用法：
    python scripts/exp_calibrate.py smoke          # 快速验证控制器可跑
    python scripts/exp_calibrate.py scan [steps]   # 扫描密度档
"""

import os
import random
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.core.engine import Engine  # noqa: E402
from app.schemes.base import SchemeContext  # noqa: E402

NET_PATH = os.path.join(os.path.dirname(BACKEND), "networks",
                        "network", "base_network.net.xml")
# v5（同源水印版）权重：MAPPO legacy base_network 训练
V5_WEIGHTS = r"C:\Users\27773\Desktop\xiongan_v5\backend\models\weights\mappo_act_full"
SAMPLE_EVERY = 30
HALT_SPEED = 0.1
WARMUP = 90          # 对齐平台：预热期无控制器，纯静态推进散车
ROUTE_MAX = 6        # 短路由 → 部分车辆能在窗口内到达（arrived 有区分度）

# 候选密度档（per_road 区间）；底下的 routes 数 ~75-80，总量≈档×75
BANDS = [(1, 2), (2, 3), (3, 5), (4, 6), (5, 8), (7, 12)]


def _routes_from_net(seed=7, max_steps=ROUTE_MAX):
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


def _write_route_file(routes, lo, hi, seed=11) -> tuple[str, int]:
    """固定种子 → 每次同档同投放（可比）。返回 (路径, 总车辆数)。"""
    rng = random.Random(seed + lo * 100 + hi)
    out = os.path.join(tempfile.gettempdir(),
                       f"calib_{lo}_{hi}_{uuid.uuid4().hex[:6]}.rou.xml")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for i, edges in enumerate(routes):
        lines.append(f'    <route id="sc{i}" edges="{" ".join(edges)}"/>')
    vid = 0
    for i in range(len(routes)):
        for _ in range(rng.randint(lo, hi)):
            lines.append(f'    <vehicle id="cv{vid}" depart="0" '
                         f'departPos="random_free" route="sc{i}"/>')
            vid += 1
    lines.append("</routes>")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out, vid


def _make_controller(engine, kind: str):
    if kind == "fixed":
        return None
    ctx = SchemeContext(engine=engine, config={}, push_event=lambda *a, **k: None)
    from app.schemes.registry import get_scheme
    cls = get_scheme("scheme_2")
    # mode=auto：有权重加载→MAPPO；否则 SCOOT
    conf = {"mode": "auto"}
    if kind == "mappo":
        conf["mappo_weights"] = V5_WEIGHTS
    ctx.config = conf
    ctrl = cls(ctx)
    ctrl.init()
    return ctrl


def run_band(lo, hi, kind, steps):
    routes = _routes_from_net()
    route_file, total = _write_route_file(routes, lo, hi)
    eng = Engine()
    eng.connect(NET_PATH, [route_file], begin=0, end=steps + 10, step_length=1.0)
    ctrl = _make_controller(eng, kind) if kind != "fixed" else None
    samples = []
    try:
        for step in range(1, steps + 1):
            eng.step()
            # 预热期（前 WARMUP 步）无控制器，对齐平台"投放→散开→再控制"
            if ctrl is not None and step > WARMUP:
                try:
                    ctrl.on_step()
                except Exception:  # noqa: BLE001 控制器异常不杀实验
                    pass
            if step % SAMPLE_EVERY != 0:
                continue
            vids = eng.get_vehicle_ids()
            spd_sum, wait_max = 0.0, 0.0
            for vid in vids:
                try:
                    st = eng.get_vehicle_state(vid)
                except Exception:
                    continue
                spd_sum += st["speed"]
                wait_max = max(wait_max, st["waiting_time"])
            eng.get_cumulative_departed()
            samples.append({
                "step": step,
                "vehicles": len(vids),
                "avg_speed": round(spd_sum / len(vids), 3) if vids else 0.0,
                "arrived": eng.get_cumulative_arrivals(),
                "max_wait": round(wait_max, 1),
            })
    finally:
        eng.close()
        try:
            os.remove(route_file)
        except OSError:
            pass
    return {"lo": lo, "hi": hi, "total": total, "kind": kind,
            "steps": steps, "samples": samples}


def summarize(res) -> str:
    s = res["samples"]
    if not s:
        return "no samples"
    tail = s[-3:]
    n = len(tail)
    spd = sum(x["avg_speed"] for x in tail) / n
    arr = tail[-1]["arrived"]
    mw = max(x["max_wait"] for x in tail)
    return (f"band={res['lo']}-{res['hi']} total={res['total']} "
            f"{res['kind']:>6}: spd={spd:5.3f} arrived={arr:3d} max_wait={mw:5.1f}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 540
    if mode == "smoke":
        routes = _routes_from_net()
        route_file, total = _write_route_file(routes, 2, 3)
        eng = Engine()
        eng.connect(NET_PATH, [route_file], begin=0, end=120, step_length=1.0)
        t0 = time.time()
        try:
            ctrl = _make_controller(eng, "scoot")
            for step in range(1, 100):
                eng.step()
                if ctrl is not None:
                    ctrl.on_step()
            print(f"[smoke] scoot 100 步 OK, mode={ctrl.mode if ctrl else 'fixed'} "
                  f"cost={time.time()-t0:.1f}s")
            ctrl2 = _make_controller(eng, "mappo")
            print(f"[smoke] mappo load -> mode={ctrl2.mode} "
                  f"(scoot 表示权重未加载)")
            for step in range(1, 60):
                eng.step()
                ctrl2.on_step()
            print("[smoke] mappo 60 步 OK")
        finally:
            eng.close()
        return
    print(f"扫描档位 × 控制器（steps={steps}）")
    for lo, hi in BANDS:
        for kind in ("fixed", "scoot"):
            t0 = time.time()
            res = run_band(lo, hi, kind, steps)
            print(summarize(res) + f"  [{time.time()-t0:.0f}s]")
    print("DONE")


if __name__ == "__main__":
    main()
