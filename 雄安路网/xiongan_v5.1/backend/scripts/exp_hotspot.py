# -*- coding: utf-8 -*-
"""hotspot 场景验证：同一车流文件 × fixed/scoot → 自适应是否有疏通增益。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.core.engine import Engine  # noqa: E402
from app.schemes.base import SchemeContext  # noqa: E402
from app.core.scenarios import generate_flow_route_file  # noqa: E402

NET = r"C:\Users\27773\Desktop\xiongan_v5\networks\network\base_network.net.xml"
STEPS = 480
WARMUP = 90
SAMPLE = 30


def make_ctrl(eng):
    ctx = SchemeContext(engine=eng, config={"mode": "auto"},
                        push_event=lambda *a, **k: None)
    from app.schemes.registry import get_scheme
    ctrl = get_scheme("scheme_2")(ctx)
    ctrl.init()
    return ctrl


def run(route_file, kind):
    eng = Engine()
    eng.connect(NET, [route_file], begin=0, end=STEPS + 10, step_length=1.0)
    ctrl = make_ctrl(eng) if kind == "scoot" else None
    rows = []
    t0 = time.time()
    try:
        for step in range(1, STEPS + 1):
            eng.step()
            if ctrl is not None and step > WARMUP:
                ctrl.on_step()
            if step % SAMPLE != 0:
                continue
            vids = eng.get_vehicle_ids()
            spd, wmax = 0.0, 0.0
            for v in vids:
                try:
                    s = eng.get_vehicle_state(v)
                except Exception:
                    continue
                spd += s["speed"]
                wmax = max(wmax, s["waiting_time"])
            eng.get_cumulative_departed()
            rows.append({"step": step, "veh": len(vids),
                         "spd": round(spd / len(vids), 2) if vids else 0.0,
                         "arr": eng.get_cumulative_arrivals(),
                         "wmax": round(wmax, 0)})
    finally:
        eng.close()
    return rows


def show(tag, rows):
    print(f"\n[{tag}]")
    print("  " + " | ".join(
        f"s{r['step']}:veh{r['veh']:>3} spd{r['spd']:>4} arr{r['arr']:>2} "
        f"w{r['wmax']:>3}" for r in rows if r["step"] % 90 == 0))
    tail = rows[-4:]
    a = tail[-1]["arr"]
    spd = sum(r["spd"] for r in tail) / len(tail)
    veh = sum(r["veh"] for r in tail) / len(tail)
    w = max(r["wmax"] for r in tail)
    print(f"  末段均值: spd={spd:.2f} 在网≈{veh:.0f} arrived={a} max_wait={w:.0f}")


def main():
    import sys as _s
    hot = _s.argv[1] if len(_s.argv) > 1 else "7_10"   # e.g. 4_6 / 5_7 / 6_8
    hl, hh = (int(x) for x in hot.split("_"))
    # 热区密度：临时覆写场景配置以便扫描（不动 scenarios.py）
    import app.core.scenarios as sc
    sc.SCENARIOS["hotspot"]["hot_per_road"] = (hl, hh)
    route_file = generate_flow_route_file(NET, "hotspot")
    print(f"route: {route_file}  hot={hl}-{hh}")
    try:
        show("fixed (静态配时)", run(route_file, "fixed"))
        show("scoot (自适应)", run(route_file, "scoot"))
    finally:
        os.remove(route_file)


if __name__ == "__main__":
    main()
