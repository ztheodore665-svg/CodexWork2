# -*- coding: utf-8 -*-
"""四典型路口：结构解析 + Webster 最优配时 + 无头仿真对比（原 vs 建议）。"""
import os
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from glob import glob

BASE = r"C:\Users\27773\Desktop\赛题资料\路口仿真案例"
GREEN = set("Gg")


def read_struct(i):
    d = os.path.join(BASE, f"sumo工程_路口{i}")
    net_f = glob(os.path.join(d, "*.net.xml"))[0]
    rou = glob(os.path.join(d, "*.rou.xml"))[0]
    tree = ET.parse(net_f)
    root = tree.getroot()
    # 车道数 per 非内边
    edge_lanes = {}
    for e in root.iter("edge"):
        eid = e.get("id", "")
        if eid.startswith(":"):
            continue
        edge_lanes[eid] = sum(1 for _ in e.findall("lane"))
    # TLS + 相位 + 连接(linkIndex 对齐 state)
    tls_info = []
    for tl in root.iter("tlLogic"):
        tid = tl.get("id")
        conns = sorted(
            [c for c in root.iter("connection") if c.get("tl") == tid],
            key=lambda c: int(c.get("linkIndex", 0)))
        phases = [(p.get("state"), float(p.get("duration"))) for p in tl.findall("phase")]
        tls_info.append({"id": tid, "phases": phases,
                         "conns": [{"from": c.get("from"), "dir": c.get("dir"),
                                    "to": c.get("to")} for c in conns]})
    return net_f, rou, root, edge_lanes, tls_info


def phase_served_froms(state, conns):
    """state 中 G 的字符下标 → 对应连接 from 边 集合。"""
    froms = set()
    for idx, ch in enumerate(state):
        if ch in GREEN and idx < len(conns):
            f = conns[idx].get("from")
            if f:
                froms.add(f)
    return froms


def webster(edge_lanes, tls_info, q_flow, amber_total=3.0):
    """按相位流量求 Webster 建议配时。返回 dict。"""
    tl = tls_info[0]
    conns = tl["conns"]
    green_phases = [i for i, (st, du) in enumerate(tl["phases"])
                    if any(c in GREEN for c in st)]
    y_list, served = [], []
    for i in green_phases:
        st = tl["phases"][i][0]
        froms = phase_served_froms(st, conns)
        # 该相位饱和容量与流量
        s = sum(edge_lanes.get(f, 1) for f in froms) * 1800.0
        q = sum(q_flow.get(f, 0.0) for f in froms)
        y_list.append(max(0.0, q / s) if s else 0.0)
        served.append((i, froms))
    Y = sum(y_list)
    n_g = len(green_phases)
    L = n_g * 4.0  # 每相位切换损失(黄3+启动1)
    C0 = (1.5 * L + 5.0) / max(1e-6, 1.0 - Y) if Y < 1.0 else 240.0
    C0 = max(40, min(180, int(C0)))
    greens = {}
    for (i, froms), y in zip(served, y_list):
        if Y > 0:
            g = (C0 - L) * y / Y
        else:
            g = (C0 - L) / n_g
        greens[i] = int(round(max(10, g)))
    return {"C": C0, "Y": Y, "L": L, "n_g": n_g,
            "y": dict(zip([s[0] for s in served], [round(v, 3) for v in y_list])),
            "greens": greens, "served": {i: sorted(f) for i, f in served}}


def read_flows(i):
    d = os.path.join(BASE, f"sumo工程_路口{i}")
    flow_f = glob(os.path.join(d, "*.flow.xml"))
    q = {}
    if flow_f:
        r = ET.parse(flow_f[0]).getroot()
        for fl in r.iter("flow"):
            frm = fl.get("from")
            if frm:
                number = float(fl.get("number", 0))
                dur = float(fl.get("end", 3600)) - float(fl.get("begin", 0))
                q[frm] = round(number * 3600.0 / dur if dur else 0, 0)
    return q


def run(net_f, rou_f, modify=None, steps=900):
    """无头跑给定 net（可先替换 tlLogic duration）。返回指标。"""
    tmp = tempfile.mkdtemp(prefix="isect_")
    try:
        net2 = os.path.join(tmp, "net.xml")
        shutil.copy(net_f, net2)
        if modify:
            t = ET.parse(net2)
            for tl in t.getroot().iter("tlLogic"):
                for idx, dur in modify.items():
                    if idx < len(list(tl.findall("phase"))):
                        list(tl.findall("phase"))[idx].set("duration", str(int(dur)))
            t.write(net2, encoding="utf-8", xml_declaration=True)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app.core.engine import Engine
        eng = Engine()
        eng.connect(net2, [rou_f], begin=0, end=steps + 5, step_length=1.0)
        total_wait, total_veh, max_q = 0.0, 0, 0
        try:
            for _ in range(steps):
                eng.step()
                if (_ + 1) % 30 != 0:
                    continue
                vids = eng.get_vehicle_ids()
                wait, stopped = 0.0, 0
                for v in vids:
                    try:
                        st = eng.get_vehicle_state(v)
                    except Exception:
                        continue
                    wait += st.get("waiting_time", 0)
                    if st["speed"] < 0.1:
                        stopped += 1
                total_wait += wait
                max_q = max(max_q, stopped)
                total_veh = max(total_veh, len(vids))
        finally:
            eng.close()
        return {"avg_wait_s_per_sample": round(total_wait / max(1, steps // 30), 1),
                "avg_onroad": total_veh, "max_queue": max_q}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    # 让脚本可 import 平台 Engine
    sys.path.insert(0, r"C:\Users\27773\Desktop\xiongan_v5.1\backend")
    out = {}
    for i in range(1, 5):
        net_f, rou_f, root, edge_lanes, tls_info = read_struct(i)
        q = read_flows(i)
        w = webster(edge_lanes, tls_info, q)
        # 汇总结果
        tl = tls_info[0]
        phases = tl["phases"]
        print(f"\n===== 路口{i} =====")
        print(f"  进口流量(veh/h): {q}  车道: {edge_lanes}")
        print(f"  原相位时长: {[(i, int(du)) for i, (_, du) in enumerate(phases)]}")
        print(f"  Webster: C0={w['C']}s Y={round(w['Y'],3)} L={w['L']}")
        print(f"    相位关键比 y={w['y']}  建议绿时(相位下标->s)={w['greens']}")
        # 修改建议：绿色相位时长 → 建议；黄色相位(3s)不变；原 0 的保持
        mod = {}
        for gi, g in w["greens"].items():
            mod[gi] = g
        print(f"  建议相位时长: {[(i, mod.get(i, int(du))) for i, (_, du) in enumerate(phases)]}")
        t0 = time.time()
        base = run(net_f, rou_f, steps=900)
        sug = run(net_f, rou_f, modify=mod, steps=900)
        print(f"  基线 vs 建议(900s): {base} -> {sug}  [{time.time()-t0:.0f}s]")
        out[i] = {"flows": q, "lanes": edge_lanes, "webster": w, "base": base, "sug": sug}
    import json
    print("\nJSON:", json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
