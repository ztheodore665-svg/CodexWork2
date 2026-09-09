"""LLM Agent 工具集：感知/行动，全部映射到平台已有能力（runtime / scheme / events）。

安全护栏：参数白名单 + 数值范围校验，LLM 输出经校验后才允许执行。
"""

import json


# ── 工具 JSON Schema（供 function calling） ─────────────────

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_network_status",
        "description": "获取路网全局实时指标（在网车辆数、平均速度、平均等待时间、平均排队）（速度单位 m/s，汇报请换算为 km/h）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_tls_status",
        "description": "获取某信号路口的当前相位与排队情况",
        "parameters": {"type": "object",
                       "properties": {"tls_id": {"type": "string", "description": "信号灯 id"}},
                       "required": ["tls_id"]}}},
    {"type": "function", "function": {
        "name": "get_edge_status",
        "description": "获取某条道路的实时状态（车辆数、平均速度、占有率、限速），用于评估通行/给出建议车速（速度单位 m/s，汇报请换算为 km/h）",
        "parameters": {"type": "object",
                       "properties": {"edge_id": {"type": "string", "description": "道路 id"}},
                       "required": ["edge_id"]}}},
    {"type": "function", "function": {
        "name": "plan_route",
        "description": "为车辆规划最优行车路径（Dijkstra，按实时旅行时间加权），返回建议路线边序列与预计行程时间，用于驾驶/绕行建议",
        "parameters": {"type": "object",
                       "properties": {
                           "from_edge": {"type": "string", "description": "起点边 id"},
                           "to_edge": {"type": "string", "description": "终点边 id"}},
                       "required": ["from_edge", "to_edge"]}}},
    {"type": "function", "function": {
        "name": "list_events",
        "description": "列出已注入的扰动事件（施工/突发车流/事故）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "inject_event",
        "description": "注入扰动事件：construction(施工限速) / large_event(突发车流) / accident(事故限速)",
        "parameters": {"type": "object",
                       "properties": {
                           "event_type": {"type": "string",
                                          "enum": ["construction", "large_event", "accident"]},
                           "edge_ids": {"type": "array", "items": {"type": "string"},
                                        "description": "受影响边"},
                           "vehicles": {"type": "integer", "description": "突发车流的车辆数（large_event 用）"}},
                       "required": ["event_type"]}}},
    {"type": "function", "function": {
        "name": "set_params",
        "description": "调整方案二控制器参数（MAPPO/SCOOT）：min_green 最短绿灯(s)、max_green 最长绿灯(s)、switch_clearance 变灯倒计时(s)",
        "parameters": {"type": "object",
                       "properties": {
                           "min_green": {"type": "number", "minimum": 0, "maximum": 20},
                           "max_green": {"type": "number", "minimum": 15, "maximum": 90},
                           "switch_clearance": {"type": "number", "minimum": 5, "maximum": 20}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "switch_mode",
        "description": "切换方案二控制器模式：mappo（AI 强化学习控制）或 scoot（规则自适应）",
        "parameters": {"type": "object",
                       "properties": {"mode": {"type": "string", "enum": ["mappo", "scoot"]}},
                       "required": ["mode"]}}},
    {"type": "function", "function": {
        "name": "generate_report",
        "description": "汇总当前全局指标生成简要态势报告（Markdown）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_network_topology",
        "description": "获取路网拓扑：路口（含关键路口/信号灯）、边（车道数/限速）、主干道、边邻接关系，用于路网级规划与走廊识别",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_region_status",
        "description": "获取一片区域的聚合实时指标（车辆数/平均速度/排队/拥堵度）；edges 传边 id 列表，不传则覆盖全路网（速度单位 m/s，汇报请换算为 km/h）",
        "parameters": {"type": "object",
                       "properties": {"edges": {"type": "array", "items": {"type": "string"},
                                                "description": "区域包含的边 id 列表（可选，默认全路网）"}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "configure_algorithm",
        "description": "设置标准化算法参数（scheme_2 的 min_green/max_green/mode、official 官方方案的 mode/decision_step、scheme_1 的 green_wave/recalc_interval、scheme_3 的 auto_reroute 等），按算法声明校验",
        "parameters": {"type": "object",
                       "properties": {
                           "algorithm_id": {"type": "string", "description": "算法 id：scheme_1 / scheme_2 / scheme_3 / official"},
                           "params": {"type": "object", "description": "要设置的参数键值对"}},
                       "required": ["algorithm_id", "params"]}}},
    {"type": "function", "function": {
        "name": "algorithm_action",
        "description": "调用标准化算法通用动作：switch_mode(切 MAPPO/SCOOT/auto)、switch_plan(官方方案切早高峰/平峰/晚高峰档)、enable_green_wave(开绿波)、reroute_fleet(车队重路由)、add_restricted_zone(限行) 等",
        "parameters": {"type": "object",
                       "properties": {
                           "algorithm_id": {"type": "string", "description": "算法 id：scheme_1 / scheme_2 / scheme_3 / official"},
                           "action": {"type": "string", "description": "动作名（见 /algorithms 能力清单）"},
                           "params": {"type": "object", "description": "动作参数（可选）"}},
                       "required": ["algorithm_id", "action"]}}},
    {"type": "function", "function": {
        "name": "compare_metrics",
        "description": "对比调控前后指标：action=set 记录当前指标为基线；action=compare 与基线对比返回差异（对比后清除基线）；action=clear 清除基线（返回速度单位 m/s，汇报请换算为 km/h）",
        "parameters": {"type": "object",
                       "properties": {"action": {"type": "string",
                                                 "enum": ["set", "compare", "clear"],
                                                 "default": "compare"}},
                       "required": []}}},
]


def _safe_params(schema, args):
    """参数范围校验（minimum/maximum/enum 白名单）。"""
    params = schema.get("function", {}).get("parameters", {})
    props = params.get("properties", {})
    out = {}
    for k, v in (args or {}).items():
        spec = props.get(k, {})
        if spec.get("enum"):
            if isinstance(v, str):
                # 容错：忽略大小写与首尾空格（LLM 常把 SCOOT/MAPPO 写成大写）
                key = v.strip().lower()
                hit = next((e for e in spec["enum"]
                            if str(e).strip().lower() == key), None)
                if hit is None:
                    return None, f"参数 {k}={v} 不在允许范围 {spec['enum']}"
                v = hit
            elif v not in spec["enum"]:
                return None, f"参数 {k}={v} 不在允许范围 {spec['enum']}"
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if isinstance(v, (int, float)) and lo is not None and v < lo:
            return None, f"参数 {k}={v} 低于下限 {lo}"
        if isinstance(v, (int, float)) and hi is not None and v > hi:
            return None, f"参数 {k}={v} 超过上限 {hi}"
        out[k] = v
    return out, None


def execute_tool(runtime, name: str, args: dict) -> dict:
    """执行工具调用。返回 (ok, 结果文本) 结构化 dict，带安全护栏。"""
    schema = next((t for t in TOOL_SCHEMAS
                   if t["function"]["name"] == name), None)
    if schema is None:
        return {"ok": False, "message": f"未知工具: {name}"}
    args, err = _safe_params(schema, args)
    if err:
        return {"ok": False, "message": f"参数校验失败: {err}"}

    try:
        # 引擎访问：平台 runtime 为 session.engine，演示 DemoRuntime 直接为 engine
        eng = (getattr(runtime, "engine", None)
               or getattr(getattr(runtime, "session", None), "engine", None))
        if name == "get_network_status":
            m = runtime.realtime_metrics().get("overall", {})
            return {"ok": True, "data": {
                "vehicle_count": m.get("vehicle_count"),
                "avg_speed": m.get("avg_speed"),
                "avg_waiting_time": m.get("avg_waiting_time"),
                "avg_queue_length": m.get("avg_queue_length")}}
        if name == "get_tls_status":
            tid = args.get("tls_id", "")
            st = eng.get_tls_state(tid)
            edges = eng.get_edge_ids()
            queue = 0
            for e in edges:
                try:
                    q = eng.get_edge_queue(e)
                    if q > 0:
                        queue += q
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": True, "data": {
                "tls_id": tid, "phase_index": st["phase_index"],
                "num_phases": st["num_phases"],
                "phase_duration": st["phase_duration"]}}
        if name == "get_edge_status":
            eid = args.get("edge_id", "")
            stats = eng.get_edge_stats(eid)
            return {"ok": True, "data": {
                "edge_id": eid,
                "vehicle_count": stats["vehicle_count"],
                "mean_speed": round(stats["mean_speed"], 2),
                "occupancy": round(stats["occupancy"], 3),
                "speed_limit": round(eng.get_edge_speed_limit(eid), 2)}}
        if name == "plan_route":
            # 兼容小模型常见参数名：from_edge/to_edge 或 start/end 或 from/to
            frm = args.get("from_edge") or args.get("start") or args.get("from") or ""
            to = args.get("to_edge") or args.get("end") or args.get("to") or ""
            path, cost = _dijkstra(eng, frm, to)
            if path is None:
                return {"ok": False, "message": f"起点 {frm} 到终点 {to} 不可达"}
            return {"ok": True, "data": {
                "from_edge": frm, "to_edge": to,
                "route_edges": path,
                "travel_time_s": round(cost, 1)}}
        if name == "list_events":
            return {"ok": True, "data": runtime.list_events()}
        if name == "inject_event":
            result = runtime.inject_event(
                args.get("event_type"), {
                    "edge_ids": args.get("edge_ids", []),
                    "vehicles": args.get("vehicles", 20)})
            return {"ok": True, "message": f"已注入 {args.get('event_type')}: {result}"}
        if name == "set_params":
            if runtime.scheme is None:
                return {"ok": False, "message": "仿真未启动或无方案激活"}
            return runtime.scheme.handle_action("set_params", args)
        if name == "switch_mode":
            if runtime.scheme is None or runtime.scheme.name != "scheme_2":
                return {"ok": False, "message": "方案二未激活，无法切换模式"}
            action = "switch_to_mappo" if args.get("mode") == "mappo" else "switch_to_scoot"
            return runtime.scheme.handle_action(action, {})
        if name == "generate_report":
            m = runtime.realtime_metrics().get("overall", {})
            st = runtime.status()
            lines = [
                f"# 态势报告（仿真步 {st.get('step')}）",
                f"- 在网车辆：{m.get('vehicle_count')}",
                f"- 平均速度：{round((m.get('avg_speed') or 0) * 3.6, 1)} km/h",
                f"- 平均等待：{m.get('avg_waiting_time')} s",
                f"- 平均排队：{m.get('avg_queue_length')} 辆",
                f"- 累计到达：{m.get('total_throughput')}",
            ]
            events = runtime.list_events()
            if events:
                lines.append("- 活跃事件：" + "、".join(e["event_type"] for e in events[-3:]))
            return {"ok": True, "report": "\n".join(lines)}
        if name == "get_network_topology":
            return _tool_topology(runtime, eng)
        if name == "get_region_status":
            return _tool_region_status(eng, args.get("edges") or [])
        if name == "configure_algorithm":
            return _tool_configure_algorithm(runtime, args)
        if name == "algorithm_action":
            return _tool_algorithm_action(runtime, args)
        if name == "compare_metrics":
            return _tool_compare_metrics(runtime, args.get("action", "compare"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"工具执行失败: {type(exc).__name__}: {exc}"}
    return {"ok": False, "message": f"未处理工具: {name}"}


def _dijkstra(eng, frm: str, to: str):
    """Dijkstra 最优路径（按实时旅行时间加权）。返回 (边序列, 总时间) 或 (None, inf)。"""
    import heapq
    if not frm or not to:
        return None, float("inf")
    INF = float("inf")
    dist = {frm: 0.0}
    prev: dict[str, str] = {}
    pq = [(0.0, frm)]
    visited: set[str] = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        if u == to:
            break
        for v in eng.get_edge_successors(u):
            if v in visited:
                continue
            try:
                tt = eng.get_edge_stats(v).get("travel_time", INF)
            except Exception:  # noqa: BLE001
                tt = INF
            if tt == INF or tt <= 0:
                tt = 1.0
            nd = d + tt
            if nd < dist.get(v, INF):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if to not in dist:
        return None, INF
    path = [to]
    u = to
    while u != frm:
        u = prev.get(u)
        if u is None:
            return None, INF
        path.append(u)
    path.reverse()
    return path, dist[to]


# ── 路网级规划工具 ─────────────────────────────────────────

def _tool_topology(runtime, eng) -> dict:
    """路网拓扑：路口（关键/信号灯）、边（车道/限速）、主干道、邻接。"""
    gj = getattr(runtime, "_geojson", None)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for f in (gj.get("features", []) if gj else []):
        geom = f.get("geometry", {}).get("type")
        p = f.get("properties", {})
        if geom == "Point":
            nodes[p.get("node_id")] = {"tls": bool(p.get("tls_id"))}
        elif geom == "LineString":
            edges.append({"id": p.get("edge_id"),
                          "from": p.get("from_node"), "to": p.get("to_node"),
                          "lanes": p.get("lanes", 1),
                          "speed": round(p.get("speed_limit", 0) or 0, 2)})
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    key_inters = sorted(k for k, d in degree.items() if d >= 4)
    arterial = [e["id"] for e in edges if e["speed"] >= 16.7]
    graph: dict[str, list[str]] = {}
    if eng is not None:
        try:
            for e in edges:
                graph[e["id"]] = list(eng.get_edge_successors(e["id"]))
        except Exception:  # noqa: BLE001
            graph = {}
    tls_ids: list[str] = []
    if eng is not None:
        try:
            tls_ids = sorted(eng.get_tls_ids())
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "data": {
        "intersection_count": len(nodes),
        "edge_count": len(edges),
        "key_intersections": key_inters[:40],      # 度 ≥ 4 的路口
        "tls_ids": tls_ids[:40],
        "arterial_edges": arterial[:60],           # 限速 ≥ 60 km/h 主干道
        "edge_graph": graph,                        # 边 → 后继边（可达关系）
        "edges": [{"id": e["id"], "from": e["from"], "to": e["to"],
                   "lanes": e["lanes"], "speed": e["speed"]} for e in edges][:200],
    }}


def _tool_region_status(eng, edges: list) -> dict:
    """区域聚合指标：车辆数/平均速度/排队/拥堵度。"""
    if eng is None:
        return {"ok": False, "message": "仿真未启动"}
    if not edges:
        try:
            edges = [e for e in eng.get_edge_ids() if not e.startswith(":")]
        except Exception:  # noqa: BLE001
            edges = []
    total_v, speeds, queued = 0, [], 0
    for eid in list(edges)[:80]:
        try:
            st = eng.get_edge_stats(eid)
            total_v += st["vehicle_count"]
            if st["mean_speed"] > 0:
                speeds.append(st["mean_speed"])
            queued += eng.get_edge_queue(eid)
        except Exception:  # noqa: BLE001
            continue
    avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else 0.0
    congestion = round(min(1.0, queued / max(1, total_v)), 3)
    return {"ok": True, "data": {
        "edge_count": len(edges),
        "vehicle_count": total_v,
        "avg_speed": avg_speed,
        "queued_vehicles": queued,
        "congestion": congestion,
    }}


def _tool_configure_algorithm(runtime, args) -> dict:
    """设置标准化算法参数（对接 /algorithms 契约，含护栏校验）。"""
    from app.algorithms.adapter import get_adapter
    aid = str(args.get("algorithm_id", ""))
    try:
        adapter = get_adapter(runtime, aid)
    except KeyError:
        return {"ok": False, "message": f"算法不存在: {aid}"}
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return {"ok": False, "message": "params 需为对象"}
    if not adapter.spec or not adapter.spec.params:
        return {"ok": False, "message": f"算法 {aid} 未声明可配置参数"}
    res = adapter.config(params)
    if not res.get("ok"):
        return {"ok": False, "message": res.get("message", "配置失败")}
    return {"ok": True, "data": {
        "algorithm_id": aid,
        "applied": res.get("applied", []),
        "params": res.get("params", {}),
        "note": res.get("note"),
    }}


def _tool_algorithm_action(runtime, args) -> dict:
    """调用标准化算法通用动作。"""
    from app.algorithms.adapter import get_adapter
    aid = str(args.get("algorithm_id", ""))
    action = str(args.get("action", ""))
    try:
        adapter = get_adapter(runtime, aid)
    except KeyError:
        return {"ok": False, "message": f"算法不存在: {aid}"}
    return adapter.action(action, args.get("params") or {})


def _current_metrics(runtime) -> dict:
    """当前全局指标快照（供对比基线）。"""
    out: dict = {}
    try:
        ov = runtime.realtime_metrics().get("overall", {})
        out["vehicle_count"] = ov.get("vehicle_count", 0)
        out["avg_speed"] = round(ov.get("avg_speed", 0) or 0, 3)
        out["avg_waiting_time"] = round(ov.get("avg_waiting_time", 0) or 0, 3)
        out["avg_queue_length"] = round(ov.get("avg_queue_length", 0) or 0, 3)
        out["total_throughput"] = ov.get("total_throughput", 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        sp = runtime.spotlight()
        out["completion_rate"] = round(sp.get("completion_rate", 0) or 0, 4)
    except Exception:  # noqa: BLE001
        pass
    return out


def _tool_compare_metrics(runtime, action: str) -> dict:
    """调控前后对比：set 记基线 / compare 返回差异（后清基线）/ clear 清除。"""
    action = action or "compare"
    if action == "set":
        runtime._agent_baseline = _current_metrics(runtime)
        return {"ok": True, "message": "已记录当前指标为基线",
                "baseline": runtime._agent_baseline}
    if action == "clear":
        runtime._agent_baseline = None
        return {"ok": True, "message": "基线已清除"}
    base = runtime._agent_baseline
    if base is None:
        runtime._agent_baseline = _current_metrics(runtime)
        return {"ok": True,
                "message": "当前无基线，已自动记录为基线（调控后再调用 compare 返回对比）",
                "baseline": runtime._agent_baseline}
    now = _current_metrics(runtime)
    diff = {}
    for k, v in now.items():
        if k in base:
            diff[k] = {"before": base[k], "after": v,
                       "delta": round(v - base[k], 3)}
    runtime._agent_baseline = None  # 对比后清除，避免陈旧基线
    return {"ok": True, "data": {"comparison": diff}}
