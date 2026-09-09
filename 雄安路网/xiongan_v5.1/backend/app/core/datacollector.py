"""数据采集器：按步采集车辆/信号灯增量与全局/路口指标。"""

from typing import Any

HALT_SPEED = 0.1  # m/s，低于此速度视为排队/停车


def edge_from_lane(lane_id: str) -> str:
    """车道 "E1_0" → 边 "E1"（去除末段 "_N"）。"""
    return lane_id.rpartition("_")[0] if "_" in lane_id else lane_id


class DataCollector:
    def __init__(self, engine):
        self._engine = engine
        self._prev_vehicles: dict[str, dict] = {}
        self._prev_tls: dict[str, dict] = {}

    # ── 主入口 ──────────────────────────────────────────────

    def collect(self, sim_step: int) -> dict:
        """返回增量更新；overall 仅在 60 的倍数步计算（其余为 None）。"""
        vehicles = self._collect_vehicles()
        tls = self._collect_tls()
        overall = self._compute_overall() if sim_step % 60 == 0 else None
        intersections = self._compute_intersections()
        return {
            "vehicles": vehicles,
            "tls": tls,
            "overall": overall,
            "intersections": intersections,
        }

    def overall(self) -> dict:
        """实时全局指标快照（供 /metrics/realtime）。"""
        return self._compute_overall()

    def emissions(self) -> dict:
        """累计排放/油耗（对所有在网车辆求和）。"""
        total = {"fuel_consumed": 0.0, "co2": 0.0, "co": 0.0, "nox": 0.0}
        for vid in self._engine.get_vehicle_ids():
            try:
                em = self._engine.get_vehicle_emissions(vid)
            except Exception:  # noqa: BLE001
                continue
            total["fuel_consumed"] += em.get("fuel", 0.0)
            total["co2"] += em.get("co2", 0.0)
            total["co"] += em.get("co", 0.0)
            total["nox"] += em.get("nox", 0.0)
        return {k: round(v, 3) for k, v in total.items()}

    def avg_speed(self) -> float:
        """轻量每步平均速度（不计算延误/排队），供 simulation_step 推送。"""
        total, n = 0.0, 0
        for vid in self._engine.get_vehicle_ids():
            try:
                total += self._engine.get_vehicle_state(vid)["speed"]
                n += 1
            except Exception:  # noqa: BLE001
                continue
        return round(total / n, 3) if n else 0.0

    def snapshot(self) -> dict:
        """无副作用的实时快照（不更新增量追踪），供 /metrics/realtime。"""
        return {"overall": self._compute_overall(),
                "intersections": self._compute_intersections()}

    # ── 车辆增量 ────────────────────────────────────────────

    def _collect_vehicles(self) -> dict:
        engine = self._engine
        current: dict[str, dict] = {}
        for vid in engine.get_vehicle_ids():
            try:
                current[vid] = engine.get_vehicle_state(vid)
            except Exception:  # noqa: BLE001 车辆可能刚消失
                continue

        prev = self._prev_vehicles
        added = [vid for vid in current if vid not in prev]
        removed = [vid for vid in prev if vid not in current]
        updated = []
        for vid in current:
            if vid in prev and self._changed(current[vid], prev[vid]):
                updated.append({"id": vid, **self._delta(current[vid], prev[vid])})
        self._prev_vehicles = current
        return {
            "added": [{"id": v, **current[v]} for v in added],
            "updated": updated,
            "removed": removed,
        }

    @staticmethod
    def _changed(cur: dict, prev: dict) -> bool:
        for key in ("x", "y", "angle", "speed", "waiting_time"):
            if abs(cur.get(key, 0.0) - prev.get(key, 0.0)) > 1e-6:
                return True
        return cur.get("lane") != prev.get("lane")

    @staticmethod
    def _delta(cur: dict, prev: dict) -> dict:
        return {k: v for k, v in cur.items()
                if k not in ("route", "type") and cur.get(k) != prev.get(k)}

    # ── 信号灯增量 ──────────────────────────────────────────

    def _collect_tls(self) -> dict:
        engine = self._engine
        out: dict[str, dict] = {}
        prev = self._prev_tls
        for tid in engine.get_tls_ids():
            try:
                st = engine.get_tls_state(tid)
            except Exception:  # noqa: BLE001
                continue
            if self._prev_tls.get(tid) != st:
                # 附加逐车道 link 信息（与 state_str 字符对齐），前端逐进口渲染信号灯
                st = {**st, "links": engine.get_tls_links(tid)}
                out[tid] = st
        self._prev_tls = {tid: engine.get_tls_state(tid)
                          for tid in engine.get_tls_ids()
                          if self._safe_state(engine, tid)}
        return out

    @staticmethod
    def _safe_state(engine, tid) -> bool:
        try:
            engine.get_tls_state(tid)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── 全局指标 ────────────────────────────────────────────

    def _compute_overall(self) -> dict:
        engine = self._engine
        vehicles = self._all_vehicle_states()
        vcount = len(vehicles)
        if vcount:
            avg_speed = sum(v["speed"] for v in vehicles) / vcount
            avg_delay = sum(self._vehicle_delay(v) for v in vehicles) / vcount
            avg_waiting = sum(v["waiting_time"] for v in vehicles) / vcount
        else:
            avg_speed = avg_delay = avg_waiting = 0.0
        # 平均排队：按"每条有车辆的边"的排队车辆数求均值（真正的排队长度）
        stopped_by_edge: dict[str, int] = {}
        for v in vehicles:
            if v["queue_length"] > 0:
                e = edge_from_lane(v["lane"])
                stopped_by_edge[e] = stopped_by_edge.get(e, 0) + 1
        avg_queue = (sum(stopped_by_edge.values()) / len(stopped_by_edge)
                     if stopped_by_edge else 0.0)
        engine.get_cumulative_departed()  # 每步累计出发数（供完成率计算）
        return {
            "vehicle_count": vcount,
            "avg_speed": round(avg_speed, 3),
            "avg_delay": round(avg_delay, 3),
            "total_throughput": engine.get_cumulative_arrivals(),
            "avg_queue_length": round(avg_queue, 3),
            "avg_waiting_time": round(avg_waiting, 3),
        }

    def _vehicle_delay(self, v: dict) -> float:
        free = self._free_speed(v.get("lane", ""))
        return max(0.0, free - v.get("speed", 0.0))

    def _free_speed(self, lane_id: str) -> float:
        try:
            return self._engine.get_edge_speed_limit(edge_from_lane(lane_id))
        except Exception:  # noqa: BLE001
            return 13.89

    def _all_vehicle_states(self) -> list[dict]:
        out = []
        engine = self._engine
        for vid in engine.get_vehicle_ids():
            try:
                v = engine.get_vehicle_state(vid)
            except Exception:  # noqa: BLE001
                continue
            v["queue_length"] = 1 if v["speed"] < HALT_SPEED else 0
            out.append(v)
        return out

    # ── 路口指标 ────────────────────────────────────────────

    def _compute_intersections(self) -> dict[str, dict]:
        engine = self._engine
        vehicles = self._all_vehicle_states()
        prev_vehicles = self._prev_vehicles
        out: dict[str, dict] = {}
        for tid in engine.get_tls_ids():
            try:
                conns = engine.get_tls_connections(tid)
            except Exception:  # noqa: BLE001
                continue
            approach_edges = {edge_from_lane(l) for lanes in conns.values() for l in lanes}
            on = [v for v in vehicles if edge_from_lane(v["lane"]) in approach_edges]
            queue = [v for v in on if v["speed"] < HALT_SPEED]
            # 吞吐：上一步在进口道、本步已离开（通过路口）的车辆数
            passed = 0
            for v in on:
                prev = prev_vehicles.get(v["route"][0], {}) if v["route"] else {}
                if prev and edge_from_lane(prev.get("lane", "")) in approach_edges \
                        and edge_from_lane(v["lane"]) not in approach_edges:
                    passed += 1
            st = engine.get_tls_state(tid)
            out[tid] = {
                "queue_length": len(queue),
                "waiting_time": round(sum(v["waiting_time"] for v in on) / len(on), 3) if on else 0.0,
                "throughput": passed,
                "current_phase": st["phase_index"],
                "phase_duration": st["phase_duration"],
            }
        return out
