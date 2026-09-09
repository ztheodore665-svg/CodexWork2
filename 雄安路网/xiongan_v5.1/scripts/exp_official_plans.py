"""离线对比实验 v3：官方三档配时 vs auto 选档 vs 基线（base_network）。

口径：所有配置都加载平台 demo 同款 add 文件（timing_safe.xml，见 App 默认启动），
     官方档位通过 setProgramLogic 整程序替换为官方相位序列（与平台方案一致）。
指标：每 30 步全车辆采样（平均等待/平均速度/在网/排队），输出均值 + 最大排队。
用法：python scripts/exp_official_plans.py [steps] [quick_plan]   quick 供抽查单配置
"""
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "backend"))

PROG = json.load(open(os.path.join(BACKEND, "backend", "data",
                                   "official_phase_programs.json"), encoding="utf-8"))
NET = os.path.join(BACKEND, "networks", "network", "base_network.net.xml")
# 用法: python exp_official_plans.py [steps] [route_base] [quick_plan]
ROUTE_NAME = sys.argv[2] if len(sys.argv) > 2 else "routes_clean700"
ROUTE = os.path.join(BACKEND, "networks", "network", ROUTE_NAME + ".rou.xml")
ADD = os.path.join(BACKEND, "networks", "network", "timing_safe.xml")
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1800
EW = {"E", "NE", "SE"}
NS = {"N", "NW", "S", "SW"}


def apply_plan(eng, tid, pid):
    sched = [(p["dur"], p["state"]) for p in PROG[tid]["plans"][pid]["phases"]]
    eng.set_tls_phase_schedule(tid, sched)


def arm_queues(eng, arms):
    ew = ns = 0.0
    for edge, comp in arms.items():
        try:
            q = float(eng.get_edge_queue(edge))
        except Exception:  # noqa: BLE001
            q = 0.0
        if comp in EW:
            ew += q
        elif comp in NS:
            ns += q
    return ew, ns, ew + ns


def sample_stats(eng):
    total_wait, speeds, stopped, n = 0.0, [], 0, 0
    for vid in eng.get_vehicle_ids():
        try:
            st = eng.get_vehicle_state(vid)
        except Exception:  # noqa: BLE001
            continue
        total_wait += st.get("waiting_time", 0.0)
        speeds.append(st.get("speed", 0.0))
        if st.get("speed", 0.0) < 0.1:
            stopped += 1
        n += 1
    return {"avg_wait": round(total_wait / n, 2) if n else 0.0,
            "avg_speed": round(sum(speeds) / len(speeds), 3) if speeds else 0.0,
            "onroad": n, "stopped": stopped}


def run_cfg(name, mode, decision=60):
    from app.core.engine import Engine
    eng = Engine()
    eng.connect(NET, [ROUTE], [ADD], begin=0, end=STEPS + 40, step_length=1.0)
    tids = [t for t in eng.get_tls_ids() if t in PROG]
    cur = {}
    try:
        if mode != "baseline":
            start_pid = {"plan0": 0, "plan1": 1, "plan2": 2, "auto": 0}[mode]
            for tid in tids:
                apply_plan(eng, tid, start_pid)
                cur[tid] = start_pid
        stats, last_decision, max_q = [], 0, 0
        for s in range(1, STEPS + 1):
            eng.step()
            if mode == "auto" and s - last_decision >= decision:
                last_decision = s
                for tid in tids:
                    arms = PROG[tid].get("arms") or {}
                    ew, ns, tot = arm_queues(eng, arms)
                    plans = PROG[tid]["plans"]
                    ew_ratios = [float(p.get("ew_ratio") or 0.5) for p in plans]
                    spread = max(ew_ratios) - min(ew_ratios)
                    cyc = [float(p.get("cycle_built") or 0) for p in plans]
                    if spread > 0.08 and ew + ns > 0:
                        demand = ew / (ew + ns)
                        pid = min(range(3), key=lambda i: abs(ew_ratios[i] - demand))
                    elif tot >= 8.0:
                        pid = max(range(3), key=lambda i: cyc[i])
                    elif tot <= 2.0:
                        pid = min(range(3), key=lambda i: cyc[i])
                    else:
                        pid = cur.get(tid, 0)
                    if pid != cur.get(tid):
                        apply_plan(eng, tid, pid)
                        cur[tid] = pid
            if s % 30 == 0:
                st = sample_stats(eng)
                max_q = max(max_q, st["stopped"])
                stats.append(st)
        n = len(stats) or 1
        return {"name": name,
                "avg_wait_s": round(sum(x["avg_wait"] for x in stats) / n, 2),
                "avg_speed": round(sum(x["avg_speed"] for x in stats) / n, 3),
                "avg_onroad": round(sum(x["onroad"] for x in stats) / n, 1),
                "max_queue": max_q}
    finally:
        eng.close()


def main():
    quick = sys.argv[3] if len(sys.argv) > 3 else ""
    cfgs = [("官方·早高峰固定", "plan0"), ("官方·平峰固定", "plan1"),
            ("官方·晚高峰固定", "plan2"), ("官方·auto选档", "auto"),
            ("基线·路网原配时(timing_safe)", "baseline")]
    rows = []
    for name, mode in cfgs:
        if quick and quick != mode and not (quick == "plan" and mode.startswith("plan")):
            continue
        t0 = time.time()
        try:
            r = run_cfg(name, mode)
            r["wall_s"] = round(time.time() - t0, 1)
            rows.append(r)
            print(r, flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] FAILED: {exc}", flush=True)
    if not quick:
        out_dir = os.path.join(BACKEND, "docs", "figs")
        os.makedirs(out_dir, exist_ok=True)
        import csv
        csv_path = os.path.join(out_dir, f"exp_official_plans_{ROUTE_NAME}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["方案", "平均等待s", "平均速度m_s", "平均在网", "最大排队", "耗时s"])
            for r in rows:
                w.writerow([r["name"], r.get("avg_wait_s"), r.get("avg_speed"),
                            r.get("avg_onroad"), r.get("max_queue"), r.get("wall_s")])
        md_path = os.path.join(out_dir, f"exp_official_plans_{ROUTE_NAME}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 官方三档配时对比实验（base_network 20 路口 · {ROUTE_NAME} · "
                    f"{STEPS}s）\n\n")
            f.write("| 方案 | 平均等待(s) | 平均速度(m/s) | 平均在网 | 最大排队 | 耗时(s) |\n")
            f.write("|---|---|---|---|---|---|\n")
            for r in rows:
                f.write(f"| {r['name']} | {r.get('avg_wait_s')} | {r.get('avg_speed')} "
                        f"| {r.get('avg_onroad')} | {r.get('max_queue')} | {r.get('wall_s')} |\n")
        print("done ->", csv_path, md_path)


if __name__ == "__main__":
    main()
