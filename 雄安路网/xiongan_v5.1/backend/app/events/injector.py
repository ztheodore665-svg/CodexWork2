"""扰动事件注入：施工占道（车道封闭）/ 大型活动（突发车流）/ 事故（局部限速）。

所有事件都产生真实仿真效果并推送前端事件日志，形成"数字靶场"。
"""

import random

ACCIDENT_SPEED_RATIO = 0.4   # 事故后限速降为原限速的 40%
CONSTRUCTION_SPEED = 0.5     # 施工车道限速到接近 0（爬行）


class EventInjector:
    EVENT_TYPES = ("construction", "large_event", "accident")

    def __init__(self, ctx, network=None):
        self.ctx = ctx
        self.engine = ctx.engine
        self.network = network          # 可选：RoadNetwork（同步受限边）
        self._events: list[dict] = []
        self._restricted: set[str] = set()
        self._speed_backup: dict[str, float] = {}
        self._event_seq = 0             # 注入车辆 vid 唯一序列（防同一步多次注入撞名）

    # ── 主入口 ──────────────────────────────────────────────

    def inject(self, event_type: str, params: dict, step: int) -> dict:
        handler = {"construction": self._construction,
                   "large_event": self._large_event,
                   "accident": self._accident}.get(event_type)
        if handler is None:
            return {"ok": False, "message": f"未知事件类型: {event_type}，"
                                            f"支持 {self.EVENT_TYPES}"}
        result = handler(params, step)
        record = {"event_type": event_type, "step": step, "params": params,
                  "result": result}
        self._events.append(record)
        self.ctx.push_event("event_injected",
                            f"{event_type} 在步 {step} 注入",
                            {"params": params, "result": result})
        return {"ok": True, "event_type": event_type, "result": result}

    # ── 事件实现 ────────────────────────────────────────────

    def _construction(self, params: dict, sim_step: int) -> dict:
        """车道封闭：受影响边限速爬行 + 标记受限。"""
        for eid in params.get("edge_ids", []):
            if eid not in self._speed_backup:
                self._speed_backup[eid] = self.engine.get_edge_speed_limit(eid)
            self._set_speed(eid, CONSTRUCTION_SPEED)
            self._restricted.add(eid)
            if self.network is not None:
                self.network.add_restricted_edge(eid, "construction")
        return {"restricted": sorted(self._restricted)}

    def _accident(self, params: dict, sim_step: int) -> dict:
        """事故：受影响边限速降至 40%。"""
        for eid in params.get("edge_ids", []):
            if eid not in self._speed_backup:
                self._speed_backup[eid] = self.engine.get_edge_speed_limit(eid)
            self._set_speed(eid, self._speed_backup[eid] * ACCIDENT_SPEED_RATIO)
        return {"slowed": list(params.get("edge_ids", []))}

    def _large_event(self, params: dict, sim_step: int) -> dict:
        """大型活动：动态注入一批车辆，形成突发车流（正常速度 + 随机路线）。

        mode=all_entries 时从路网全部入口边注入；否则用 params.edge_ids 定点注入。
        to_edge 可指定终点（定点注入时），否则随机终点。
        """
        if params.get("mode") == "all_entries":
            edges = self._entry_edges()
        else:
            edges = params.get("edge_ids") or self.engine.get_edge_ids()[:10]
        count = int(params.get("vehicles", 10))
        if not edges:
            return {"added": 0, "message": "无可注入边的边"}
        added = []
        veh_type = params.get("veh_type") or "DEFAULT_VEHTYPE"
        to_param = params.get("to_edge")
        for _i in range(count):
            self._event_seq += 1
            vid = f"event_{self._event_seq}"
            frm = edges[_i % len(edges)]
            route = self._random_route(frm, to_param)
            if not route:
                continue
            try:
                self.engine.add_vehicle_route(vid, route, depart=float(sim_step),
                                              veh_type=veh_type)
                added.append(vid)
            except Exception:  # noqa: BLE001
                continue
        return {"added": len(added), "vehicles": added}

    def _random_route(self, frm: str, to: str | None = None,
                      max_len: int = 10) -> list[str] | None:
        """从起点随机走一段合理路线（正常速度；可指定终点）。"""
        route = [frm]
        cur = frm
        for _ in range(max_len):
            succ = self.engine.get_edge_successors(cur)
            if not succ:
                break
            if to and to in succ:
                route.append(to)
                break
            cands = [s for s in succ if s not in route]
            if not cands:
                break
            nxt = random.choice(cands)
            route.append(nxt)
            cur = nxt
            if to and cur == to:
                break
        return route if len(route) >= 2 else None

    def _entry_edges(self) -> list[str]:
        """路网入口边：没有任何边通向它的边（车流从这些边进入路网）。"""
        engine = self.engine
        all_e = engine.get_edge_ids()
        succ_set: set[str] = set()
        for e in all_e:
            succ_set.update(engine.get_edge_successors(e))
        entries = [e for e in all_e if e not in succ_set]
        return entries or all_e

    def _set_speed(self, eid: str, speed: float) -> None:
        try:
            self.engine.set_edge_speed_limit(eid, speed)
        except Exception:  # noqa: BLE001
            pass

    # ── 查询 ────────────────────────────────────────────────

    def list_events(self) -> list[dict]:
        return list(self._events)

    def active_edges(self) -> set[str]:
        return set(self._restricted)

    def clear(self) -> None:
        """恢复原限速并清空受限标记（可选：仿真停止时调用）。"""
        for eid, speed in self._speed_backup.items():
            try:
                self.engine.set_edge_speed_limit(eid, speed)
            except Exception:  # noqa: BLE001
                pass
            self._restricted.discard(eid)
        if self.network is not None:
            self.network.clear_restricted_edges()
        self._speed_backup.clear()
