"""交通场景生成：按场景密度一次性投放车辆（不再用 flow 长窗口逐渐进场）。

设计（对应需求）：
- **区间随道路数量线性提升**：每道路车辆数按区间 (min,max) 随机取值，
  总投放量 = Σ 各道路随机值，道路越多总车越多，不再用固定总量。
- **一次性投放**：路由从路网每条边随机续走生成（车辆遍布各条道路，非仅入口），
  所有车辆 depart=0 + departPos=random_free 立刻全部上路，按道路随机数量投放。
- **高倍速无头预热**：投放后由会话在启动阶段直接步进（不回调、不推送、不休眠），
  车辆散开/形成排队后再启动线程向前端推送 → 前端"投放时不渲染、投放完再渲染"。

场景切换是启动参数（与方案类似），不改变信号/路网结构，纯车流密度。
"""

import os
import random
import tempfile
import uuid
import xml.etree.ElementTree as ET

# 每道路车辆数区间（min,max）。总投放量 = Σ 各道路随机取值，随道路数量线性提升。
# 以基础路网 80 边校准（scripts/exp_calibrate.py，warmup 90 + 短路由，固定种子对比）：
#   ≤2 辆/路：全通无排队（无对比）；3~5：排队出现、自适应算法增益可见（推荐演示区）；
#   ≥5 辆/路：>400 投放接近/超过网络并发上限（约 400）→ 全网饱和后调度无增益
#   （SCOOT/MAPPO 频繁切换反损绿时，实测 arrived 低于固定配时）。
# 四档在"可区分窗口"内保留梯度：深夜 / 平峰 / 高峰 / 极高峰（不进入死锁区）。
#
# hotspot 为"空间不均"场景（体现算法疏通的核心场景）：路网中央一块区域过饱和
# （排队向四周外溢），外围畅通——让自适应/绿波/Agent 有"疏通局部拥堵"的发挥空间；
# 均匀饱和时所有算法无差异，空间不均才存在调度增益。
SCENARIOS: dict[str, dict] = {
    "sparse":  {"label": "深夜 · 低流量", "per_road": (1, 2)},
    "normal":  {"label": "平峰 · 中流量", "per_road": (2, 3)},
    "peak":    {"label": "高峰 · 高流量", "per_road": (3, 5)},
    "extreme": {"label": "极高峰 · 拥堵", "per_road": (4, 6)},
    "hotspot": {"label": "区域热点 · 局部拥堵", "kind": "hotspot",
                "light_per_road": (1, 2),       # 非热点区：畅通
                "hot_per_road": (5, 7),         # 热点区：饱和排队，但车流仍可流动
                "hot_fraction": 0.35,           # 热点区规模（占路由起点边的比例）
                "desc": "路网中心区域饱和、排队外溢、外围畅通；实测自适应后期"
                        "平均速度约高固定配时 50%+（scripts/exp_hotspot.py）"},
}

# 启动预热时长（仿真秒）：一次性投放后无头高倍速步进到该时刻，车辆散开/形成
# 排队后再渲染。预热在会话线程启动前完成，期间不向前端推送任何数据。
WARMUP_STEPS = 90

_DEFAULT_SCENARIO = "normal"


def list_scenarios() -> list[dict]:
    """场景清单（前端右上角下拉）。"""
    out = []
    for k, v in SCENARIOS.items():
        if v.get("kind") == "hotspot":
            lp = v["light_per_road"]
            hp = v["hot_per_road"]
            out.append({"id": k, "label": v["label"],
                        "per_road": (f"外围 {lp[0]}–{lp[1]} 辆/路 · "
                                     f"热点 {hp[0]}–{hp[1]} 辆/路"),
                        "desc": v.get("desc", "")})
        else:
            pr = v.get("per_road", (0, 0))
            out.append({"id": k, "label": v["label"],
                        "per_road": f"{pr[0]}–{pr[1]} 辆/路"})
    return out


def get_default_scenario() -> str:
    return _DEFAULT_SCENARIO


def _parse_route_file(route_file: str | None) -> list[list[str]]:
    """从既有 .rou.xml 提取 <route> 边序列（已知有效的 OD 路由，兜底用）。"""
    if not route_file or not os.path.isfile(route_file):
        return []
    try:
        root = ET.parse(route_file).getroot()
        return [r.get("edges", "").split()
                for r in root.iter("route")
                if len((r.get("edges") or "").split()) >= 2]
    except Exception:  # noqa: BLE001 解析失败则走路网兜底
        return []


def _routes_from_net(net_path: str, max_steps: int = 16,
                     seed: int = 7):
    """从路网**每条边**生成一条随机续走路由（车辆遍布各条道路，非仅入口）。

    返回 (路由列表, sumolib 路网对象)，供热点分区等按空间信息的场景使用。
    路由以边升序排列，每条以该边为起点随机续走至出口/无新后继。
    """
    import sumolib

    net = sumolib.net.readNet(net_path)
    succ: dict[str, list[str]] = {}
    for e in net.getEdges():
        out = []
        for conns in e.getOutgoing().values():
            for c in conns:
                if c is not None:
                    out.append(c.getTo().getID())
        succ[e.getID()] = out
    rng = random.Random(seed)
    routes: list[list[str]] = []
    for eid in sorted(succ):
        route = [eid]
        cur = eid
        for _ in range(max_steps):
            nxts = [s for s in succ.get(cur, []) if s not in route]
            if not nxts:
                break
            cur = rng.choice(nxts)
            route.append(cur)
            # 到达出口（不再进入内部）或环回时停止
            if not any(s not in route for s in succ.get(cur, [])):
                break
        if len(route) >= 2:
            routes.append(route)
    return routes, net


def _hot_edges(net, fraction: float) -> set[str]:
    """热点边集：距离全路网质心最近的 fraction×N 条边（格网中心自然连片成热区）。"""
    import math
    centers: dict[str, tuple[float, float]] = {}
    for e in net.getEdges():
        pts = e.getShape()
        if not pts:
            continue
        p = pts[len(pts) // 2] if len(pts) > 1 else pts[0]
        centers[e.getID()] = (float(p[0]), float(p[1]))
    if not centers:
        return set()
    cx = sum(x for x, _ in centers.values()) / len(centers)
    cy = sum(y for _, y in centers.values()) / len(centers)
    ranked = sorted(centers,
                    key=lambda eid: (centers[eid][0] - cx) ** 2
                                    + (centers[eid][1] - cy) ** 2)
    n = max(4, int(round(len(ranked) * max(0.05, min(0.9, fraction)))))
    return set(ranked[:n])


def generate_flow_route_file(net_path: str, scenario: str,
                             source_route_file: str | None = None) -> str:
    """按场景生成一次性投放车流 .rou.xml（写入系统临时目录），返回其路径。

    scenario 非法时回退到默认场景。路由由路网每条边随机续走生成（优先），
    失败时退回源车流路由。车辆 depart=0 + departPos=random_free 立即全部上路，
    每道路按 SCENARIOS 区间随机取车辆数；每次启动随机分布（不缓存）。
    """
    key = scenario if scenario in SCENARIOS else _DEFAULT_SCENARIO
    spec = SCENARIOS[key]
    kind = spec.get("kind", "uniform")
    try:
        routes, net = _routes_from_net(net_path)
    except Exception:  # noqa: BLE001 sumolib 不可用/解析失败则用源车流路由兜底
        routes, net = [], None
    if not routes:
        routes = _parse_route_file(source_route_file)
    if not routes:
        raise ValueError("无法为场景生成路由：路网无可通行边")
    hot: set[str] = set()
    if kind == "hotspot" and net is not None:
        try:
            hot = _hot_edges(net, spec.get("hot_fraction", 0.35))
        except Exception:  # noqa: BLE001 热点分区失败退化为均匀 light 投放
            pass
    rng = random.Random()  # 每次启动随机（不固定种子 → 每次投放分布不同）
    out_path = os.path.join(
        tempfile.gettempdir(),
        f"xiongan_scenario_{key}_{len(routes)}r_{uuid.uuid4().hex[:8]}.rou.xml")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for i, edges in enumerate(routes):
        lines.append(f'    <route id="sc{i}" edges="{" ".join(edges)}"/>')
    vid = 0
    for i, edges in enumerate(routes):
        if kind == "hotspot":
            lo, hi = (spec["hot_per_road"] if edges[0] in hot
                      else spec["light_per_road"])
        else:
            lo, hi = spec["per_road"]
        k = rng.randint(lo, hi)
        for _ in range(k):
            lines.append(
                f'    <vehicle id="scv{vid}" depart="0" '
                f'departPos="random_free" route="sc{i}"/>')
            vid += 1
    lines.append("</routes>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path
