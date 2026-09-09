"""相位-车道几何映射：为每个路口的每个绿色相位计算其服务的上下游边对。

供压力奖励（max-pressure）使用：reward = Σ(上游排队 − 下游排队) 于该相位服务的 link。
"""

import math
import os

from app.core.conflict import CLOCKWISE, COUNTER_CLOCKWISE, OPPOSITE


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


def _far_end(edge, junction_id, net):
    if edge.getToNode().getID() == junction_id:
        return edge.getFromNode().getCoord()
    if edge.getFromNode().getID() == junction_id:
        return edge.getToNode().getCoord()
    return None


def build_phase_serving(engine):
    """为每个路口计算绿色相位 → [(上游边, 下游边)]。

    返回 {tls_id: {phase_index: [(src_edge, dst_edge), ...]}}（仅绿色相位）。
    需引擎已连接，且可从 net_file 加载 sumolib 网络。
    """
    import traci
    import sumolib

    net_file = engine.net_file()
    net = sumolib.net.readNet(net_file)
    node_coords = {n.getID(): n.getCoord() for n in net.getNodes()}

    serving = {}
    for tls_id in engine.get_tls_ids():
        jcoord = node_coords.get(tls_id)
        if jcoord is None:
            continue
        links = traci.trafficlight.getControlledLinks(tls_id)
        # 收集每个 link 的 (全局槽位序, 上游边, 下游边)。
        # 关键：TraCI getControlledLinks 顺序 = state 字符序，且部分路口存在
        # **空槽**（受控序号缺号但仍占一个字符位，如 0,1,3,4… 缺 2）。
        # 必须记录全局槽位序号并按它取 state 字符，否则压缩序号与相位状态
        # 字符错位，绿灯服务的 link 会映射错方向（与前端灯位错位同源）。
        link_meta = []
        for slot, lnk in enumerate(links):
            inner = lnk[0] if (len(lnk) == 1 and isinstance(lnk[0], tuple)) else lnk
            parts = tuple(inner)
            if len(parts) < 2:
                continue
            frm = parts[0]
            rest = [p for p in parts[1:] if p]
            to = rest[-1] if rest else ""
            if not to:
                continue
            try:
                from_edge = net.getEdge(frm.rpartition("_")[0])
                to_edge = net.getEdge(to.rpartition("_")[0])
            except KeyError:
                continue
            f_far = _far_end(from_edge, tls_id, net)
            t_far = _far_end(to_edge, tls_id, net)
            if f_far is None or t_far is None:
                continue
            approach = compass_of(f_far, jcoord)
            exit_app = compass_of(t_far, jcoord)
            direction = classify(approach, exit_app)
            link_meta.append((slot, direction, from_edge.getID(), to_edge.getID()))

        # 相位 → 该相位放行的 link（按 state 判断 G/g）
        try:
            logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
        except traci.TraCIException:
            continue
        active = traci.trafficlight.getProgram(tls_id)
        logic = next((lg for lg in logics if lg.programID == active), logics[0])
        by_phase = {}
        for pi, ph in enumerate(logic.phases):
            if not ("G" in ph.state or "g" in ph.state):
                continue
            # 该相位放行的 link：state[全局槽位] 为 G/g
            served = []
            for slot, _, src, dst in link_meta:
                if slot < len(ph.state) and ph.state[slot] in "Gg":
                    served.append((src, dst))
            if served:
                # 去重：同一 (上游,下游) 可能被多股车道覆盖，避免压力重复计数
                by_phase[pi] = list(dict.fromkeys(served))
        serving[tls_id] = by_phase
    return serving
