"""Mock 仿真引擎：实现与 app.core.engine.Engine 完全一致的接口，供单元测试使用。

内存维护边/车辆/信号灯状态，step() 推进车辆沿路线移动，支持受限边、
旅行时间覆盖等测试辅助方法，供各方案模块测试复用。
"""

from app.core.engine import EngineError


class MockEngine:
    def __init__(self):
        self._connected = False
        self._step = 0
        self._edges: dict[str, dict] = {}
        self._vehicles: dict[str, dict] = {}
        self._tls: dict[str, dict] = {}
        self._restricted: dict[str, str] = {}      # edge_id -> reason
        self._edge_overrides: dict[str, dict] = {}  # edge_id -> 统计覆盖
        self._arrived_cumulative = 0
        self._departed_buffer: list[str] = []
        self._arrived_buffer: list[str] = []

    # ── Engine 契约：生命周期 ─────────────────────────────────

    def connect(self, net_file, route_files=None, add_files=None,
                begin=0, end=86400, step_length=1.0):
        self._connected = True
        self._step = 0
        self._begin, self._end = begin, end
        self.step_length = step_length

    def close(self):
        self._connected = False

    def step(self):
        if not self._connected:
            raise EngineError(1001, "仿真引擎未连接")
        self._step += 1
        self._departed_buffer = []
        self._arrived_buffer = []
        for tid in self._tls:
            self._tls[tid]["elapsed"] = self._tls[tid].get("elapsed", 0.0) + 1.0
        for vid in list(self._vehicles):
            self._move_vehicle(vid)
        return self._step

    def get_sim_time(self):
        self._require()
        return float(self._step)

    # ── Engine 契约：读取 ────────────────────────────────────

    def get_edge_ids(self):
        self._require()
        return list(self._edges)

    def get_edge_stats(self, edge_id):
        self._require()
        base = self._edges.get(edge_id)
        if base is None:
            return {"vehicle_count": 0, "mean_speed": 0.0, "occupancy": 0.0,
                    "travel_time": float("inf")}
        ov = self._edge_overrides.get(edge_id, {})
        vc = ov.get("vehicle_count", base.get("vehicle_count", 0))
        ms = ov.get("mean_speed", base.get("mean_speed", 10.0))
        occ = ov.get("occupancy", base.get("occupancy", 0.0))
        tt = ov.get("travel_time", base.get("travel_time", 10.0))
        return {"vehicle_count": vc, "mean_speed": ms, "occupancy": occ,
                "travel_time": tt}

    def get_vehicle_ids(self):
        self._require()
        return list(self._vehicles)

    def get_vehicle_state(self, veh_id):
        self._require()
        v = self._vehicles.get(veh_id)
        if v is None:
            raise EngineError(1002, f"车辆不存在: {veh_id}")
        return {
            "x": v["x"], "y": v["y"], "angle": v["angle"], "speed": v["speed"],
            "lane": v["lane"], "type": v["type"], "route": list(v["route"]),
            "waiting_time": v["waiting_time"],
        }

    def get_departed_vehicles(self):
        self._require()
        return list(self._departed_buffer)

    def get_arrived_vehicles(self):
        self._require()
        return list(self._arrived_buffer)

    def get_cumulative_arrivals(self):
        self._require()
        return self._arrived_cumulative

    def get_edge_speed_limit(self, edge_id):
        self._require()
        return float(self._edges.get(edge_id, {}).get("speed_limit", 13.89))

    def get_edge_length(self, edge_id):
        self._require()
        return float(self._edges.get(edge_id, {}).get("length", 0.0))

    def get_edge_successors(self, edge_id):
        self._require()
        e = self._edges.get(edge_id)
        if not e:
            return []
        to = e["to_node"]
        return [eid for eid, ed in self._edges.items() if ed["from_node"] == to]

    def get_edge_road_type(self, edge_id):
        return self._edges.get(edge_id, {}).get("road_type", "secondary")

    def get_tls_ids(self):
        self._require()
        return list(self._tls)

    def get_tls_state(self, tls_id):
        self._require()
        t = self._tls.get(tls_id)
        if t is None:
            raise EngineError(1002, f"信号灯不存在: {tls_id}")
        return {
            "state_str": t["state_str"], "phase_index": t["phase_index"],
            "num_phases": t["num_phases"], "duration": t["duration"],
            "phase_duration": t["duration"], "elapsed": t["elapsed"],
        }

    def get_tls_connections(self, tls_id):
        self._require()
        t = self._tls.get(tls_id)
        if t is None:
            raise EngineError(1002, f"信号灯不存在: {tls_id}")
        return {i: list(t["lanes"]) for i in range(t["num_phases"])}

    # ── Engine 契约：写入 ────────────────────────────────────

    def set_tls_phase(self, tls_id, phase_index, duration):
        self._require()
        t = self._tls.get(tls_id)
        if t is None:
            raise EngineError(1002, f"信号灯不存在: {tls_id}")
        t["phase_index"] = phase_index
        t["duration"] = duration
        t["elapsed"] = 0.0
        n = t["num_phases"]
        t["state_str"] = ("G" if phase_index == n - 1 else "r") * n

    def set_tls_program(self, tls_id, program_id):
        self._require()
        if tls_id not in self._tls:
            raise EngineError(1002, f"信号灯不存在: {tls_id}")
        self._tls[tls_id]["program"] = program_id

    def set_vehicle_route(self, veh_id, edges):
        self._require()
        v = self._vehicles.get(veh_id)
        if v is None:
            raise EngineError(1002, f"车辆不存在: {veh_id}")
        v["route"] = list(edges)
        v["idx"] = 0
        v["pos"] = 0.0
        v["lane"] = edges[0] if edges else ""
        v["route_changes"] = v.get("route_changes", 0) + 1

    def reroute_vehicle(self, veh_id, with_travel_time=True):
        self._require()
        if veh_id not in self._vehicles:
            raise EngineError(1002, f"车辆不存在: {veh_id}")
        self._vehicles[veh_id]["reroutes"] = self._vehicles[veh_id].get("reroutes", 0) + 1

    def set_edge_speed_limit(self, edge_id, speed):
        self._require()
        if edge_id not in self._edges:
            raise EngineError(1002, f"边不存在: {edge_id}")
        self._edges[edge_id]["speed_limit"] = float(speed)

    def add_vehicle_trip(self, veh_id, from_edge, to_edge, depart=0.0):
        self._require()
        if from_edge not in self._edges or to_edge not in self._edges:
            raise EngineError(1002, f"边不存在: {from_edge}/{to_edge}")
        self.add_vehicle(veh_id, veh_type="passenger",
                         edges=[from_edge, to_edge], speed=5.0)
        return veh_id

    # ── 测试辅助 ─────────────────────────────────────────────

    def add_edge(self, edge_id, length=100.0, speed_limit=10.0,
                 road_type="secondary", from_node="n1", to_node="n2"):
        self._edges[edge_id] = {
            "length": length, "speed_limit": speed_limit, "road_type": road_type,
            "from_node": from_node, "to_node": to_node,
            "vehicle_count": 0, "mean_speed": speed_limit, "occupancy": 0.0,
            "travel_time": length / speed_limit,
        }
        return self

    def add_vehicle(self, veh_id, veh_type="passenger", edges=None, speed=5.0,
                    waiting_time=0.0, angle=90.0):
        route = list(edges) if edges else [next(iter(self._edges), "")]
        self._vehicles[veh_id] = {
            "x": 0.0, "y": 0.0, "angle": angle, "speed": speed, "lane": route[0],
            "type": veh_type, "route": route, "waiting_time": waiting_time,
            "idx": 0, "pos": 0.0,
            "emissions": {"fuel": 0.0, "co2": 0.0, "co": 0.0, "nox": 0.0},
        }
        self._departed_buffer.append(veh_id)
        return self

    def get_vehicle_emissions(self, veh_id):
        self._require()
        v = self._vehicles.get(veh_id)
        if v is None:
            raise EngineError(1002, f"车辆不存在: {veh_id}")
        return dict(v.get("emissions", {"fuel": 0.0, "co2": 0.0, "co": 0.0, "nox": 0.0}))

    def set_vehicle_emissions(self, veh_id, fuel=0.0, co2=0.0, co=0.0, nox=0.0):
        if veh_id in self._vehicles:
            self._vehicles[veh_id]["emissions"] = {"fuel": fuel, "co2": co2,
                                                   "co": co, "nox": nox}
        return self

    def add_tls(self, tls_id, num_phases=3, phase=0, duration=10.0, lanes=None,
                position=(0.0, 0.0)):
        self._tls[tls_id] = {
            "state_str": ("G" if phase == num_phases - 1 else "r") * num_phases,
            "phase_index": phase, "num_phases": num_phases, "duration": duration,
            "lanes": list(lanes) if lanes else [], "position": position,
            "elapsed": 0.0,
        }
        return self

    def get_tls_position(self, tls_id):
        self._require()
        t = self._tls.get(tls_id)
        if t is None:
            raise EngineError(1002, f"信号灯不存在: {tls_id}")
        return t.get("position")

    def set_edge_travel_time(self, edge_id, value):
        ov = self._edge_overrides.setdefault(edge_id, {})
        ov["travel_time"] = value
        return self

    def set_edge_stats(self, edge_id, vehicle_count=None, mean_speed=None,
                       occupancy=None, travel_time=None):
        ov = self._edge_overrides.setdefault(edge_id, {})
        if vehicle_count is not None:
            ov["vehicle_count"] = vehicle_count
        if mean_speed is not None:
            ov["mean_speed"] = mean_speed
        if occupancy is not None:
            ov["occupancy"] = occupancy
        if travel_time is not None:
            ov["travel_time"] = travel_time
        return self

    def restrict(self, edge_id, reason="restricted"):
        self._restricted[edge_id] = reason

    def is_restricted(self, edge_id):
        return edge_id in self._restricted

    def remove_vehicle(self, veh_id):
        if veh_id in self._vehicles:
            del self._vehicles[veh_id]

    def get_edge_def(self, edge_id):
        return dict(self._edges.get(edge_id, {}))

    def edge_count(self):
        return len(self._edges)

    # ── 内部 ─────────────────────────────────────────────────

    def _move_vehicle(self, vid):
        v = self._vehicles[vid]
        route = v["route"]
        if v["idx"] >= len(route):
            del self._vehicles[vid]
            return
        edge = route[v["idx"]]
        length = self._edges.get(edge, {}).get("length", 100.0)
        v["pos"] += v["speed"]
        v["x"] = v["pos"]  # 位置沿当前边推进，供增量采集识别变化
        if v["pos"] >= length:
            v["idx"] += 1
            v["pos"] = 0.0
            if v["idx"] >= len(route):
                del self._vehicles[vid]
                self._arrived_cumulative += 1
                self._arrived_buffer.append(vid)
                return
        v["lane"] = route[v["idx"]]

    def _require(self):
        if not self._connected:
            raise EngineError(1001, "仿真引擎未连接")
