"""安全信号配时生成器：按路口 link 几何生成无冲突的硬绿灯(G)配时。

方法：
  1. 通过 TraCI 读取每个路口的受控 link（fromLane→toLane，每 link 一个 state 字符）
  2. 用 sumolib 几何计算每个 link 的进口方向与出口方向，分类为 straight/left/right
  3. 按经典 4 相位方案分组（NS直行+右转 / EW直行+右转 / NS左转 / EW左转）
  4. 每组内用冲突检测模块校验无冲突；每个绿灯车道每相位只给一个硬 G（其余 r），
     右转 link 给 permissive 'g' 允许让行通过
  5. 输出 timing_safe.xml（含黄灯/全红过渡相位）

用法：
    python scripts/gen_safe_timing.py [输出文件] [绿灯时长] [黄灯] [全红]
"""

import math
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import traci  # noqa: E402
import sumolib  # noqa: E402

from app.core.conflict import (  # noqa: E402
    APPROACHES,
    CLOCKWISE,
    COUNTER_CLOCKWISE,
    OPPOSITE,
    simple_conflict,
)

XIONGAN = "C:/Users/27773/Desktop/xiongan"
NET = os.path.join(XIONGAN, "network", "base_network.net.xml")
ROUTES = [os.path.join(XIONGAN, "network", "routes_clean700.rou.xml")]
ADD = [os.path.join(XIONGAN, "network", "timing.add.xml")]


def compass_of(coord, jcoord):
    jx, jy = jcoord
    x, y = coord
    b = math.degrees(math.atan2(y - jy, x - jx)) % 360
    if 45 <= b < 135:
        return "North"
    if 135 <= b < 225:
        return "West"
    if 225 <= b < 315:
        return "South"
    return "East"


def classify(approach, exit_app):
    if exit_app == OPPOSITE[approach]:
        return "straight"
    if exit_app == COUNTER_CLOCKWISE[approach]:
        return "left"
    if exit_app == CLOCKWISE[approach]:
        return "right"
    return "straight"


def far_end_of(edge, junction_id, net):
    """返回边远离路口那一端的坐标（用于方位角计算）。"""
    if edge.getToNode().getID() == junction_id:
        return edge.getFromNode().getCoord()
    if edge.getFromNode().getID() == junction_id:
        return edge.getToNode().getCoord()
    return None


def build_tls(tls_id, links, net, jcoord):
    """构建路口数据。

    返回 (links_info, lanes)：
      links_info[idx] = {src, to, direction, approach}
      lanes[src_lane] = {approach, allowed:set, links:[idx]}
    """
    links_info = {}
    lanes = {}
    for idx, lnk in enumerate(links):
        inner = lnk[0] if (len(lnk) == 1 and isinstance(lnk[0], tuple)) else lnk
        parts = tuple(inner)
        if len(parts) < 2:
            continue
        frm = parts[0]
        rest = [p for p in parts[1:] if p]
        to = rest[-1] if rest else ""
        try:
            from_edge = net.getEdge(frm.rpartition("_")[0])
            to_edge = net.getEdge(to.rpartition("_")[0])
        except KeyError:
            continue
        if from_edge is None or to_edge is None:
            continue
        f_far = far_end_of(from_edge, tls_id, net)
        t_far = far_end_of(to_edge, tls_id, net)
        if f_far is None or t_far is None:
            continue
        approach = compass_of(f_far, jcoord)
        exit_app = compass_of(t_far, jcoord)
        direction = classify(approach, exit_app)
        links_info[idx] = {"src": frm, "to": to,
                           "direction": direction, "approach": approach}
        lane = lanes.setdefault(frm, {"approach": approach, "allowed": set(),
                                      "links": []})
        lane["allowed"].add(direction)
        lane["links"].append(idx)
    return links_info, lanes


def _links_compatible(i, j, links_info):
    """两个 link 能否同相位：源车道/目标车道不同，且具体运动方向无冲突。"""
    a, b = links_info[i], links_info[j]
    if a["src"] == b["src"]:
        return False          # 同一源车道，避免双绿灯
    if a["to"] == b["to"]:
        return False          # 同出口车道，避免汇入冲突
    return not simple_conflict(a["approach"], a["direction"],
                               b["approach"], b["direction"])


def build_phases(links_info, lanes):
    """贪心独立集分组：按每条 link 的具体运动方向判兼容，保证全覆盖与组内安全。"""
    idxs = sorted(links_info)
    unassigned = set(idxs)
    phases = []
    while unassigned:
        phase = []
        for i in sorted(unassigned):
            if all(_links_compatible(i, j, links_info) for j in phase):
                phase.append(i)
                unassigned.remove(i)
        phases.append(phase)
    return phases


def state_for_phase(phase_links, links_info, n_links):
    """生成 (green_state, yellow_state, allred_state)。右转给让行 g。"""
    green = ["r"] * n_links
    for idx in phase_links:
        if links_info[idx]["direction"] == "right":
            green[idx] = "g"
        else:
            green[idx] = "G"
    green_s = "".join(green)
    yellow_s = green_s.replace("G", "y").replace("g", "y")
    allred_s = "r" * n_links
    return green_s, yellow_s, allred_s


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(XIONGAN, "network", "timing_safe.xml")
    green_dur = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    yellow = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    all_red = int(sys.argv[4]) if len(sys.argv) > 4 else 2

    # 1) 连接读 link 结构
    sumo_home = os.environ.get("SUMO_HOME", "")
    sumo_bin = os.path.join(sumo_home, "bin", "sumo.exe") if sumo_home else "sumo"
    cmd = [sumo_bin, "-n", NET, "-r", ROUTES[0], "-a", ADD[0],
           "-b", "0", "-e", "5", "--no-step-log", "--quit-on-end"]
    traci.start(cmd)
    net = sumolib.net.readNet(NET)
    node_coords = {n.getID(): n.getCoord() for n in net.getNodes()}

    root = ET.Element("additional")
    stats = []
    starved = []
    for tls_id in traci.trafficlight.getIDList():
        links = traci.trafficlight.getControlledLinks(tls_id)
        jcoord = node_coords.get(tls_id)
        if jcoord is None:
            continue
        links_info, lanes = build_tls(tls_id, links, net, jcoord)
        phases = build_phases(links_info, lanes)
        if not phases:
            continue
        n_links = len(links)
        logic = ET.SubElement(root, "tlLogic", id=tls_id, type="static",
                              programID="safe", offset="0")
        green_pos = set()
        for ph in phases:
            gs, ys, ar = state_for_phase(ph, links_info, n_links)
            green_pos.update(i for i, c in enumerate(gs) if c in "Gg")
            ET.SubElement(logic, "phase", duration=str(green_dur), state=gs)
            ET.SubElement(logic, "phase", duration=str(yellow), state=ys)
            ET.SubElement(logic, "phase", duration=str(all_red), state=ar)
        uncovered = [i for i in range(n_links) if i not in green_pos]
        if uncovered:
            starved.append(f"{tls_id}: 未覆盖 link 数 {len(uncovered)}")
        stats.append((tls_id, len(phases), n_links))
    traci.close()
    avg_ph = sum(p for _, p, _ in stats) / len(stats) if stats else 0

    ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
    print(f"已写入 {out}")
    if starved:
        print(f"[饿死] 路口数={len(starved)}，未覆盖 link：")
        for s in starved:
            print("  ", s)
    else:
        print(f"[OK] 全部受控 link 均覆盖绿灯；平均每路口 {avg_ph:.1f} 个相位")


if __name__ == "__main__":
    main()
