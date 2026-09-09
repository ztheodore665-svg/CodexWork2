"""车队管理器：识别/注册/跟踪车队车辆，重路由冷却与历史记录。"""

from app.core.datacollector import edge_from_lane

FLEET_CATEGORY_RULES = {
    "bus": ["bus", "route"],
    "ride_hail": ["fleet", "ride"],
    "delivery": ["deliver"],
    "emergency": ["emergency", "ambulance"],
}
CATEGORY_BY_TYPE = {
    "bus": "bus", "coach": "bus", "truck": "delivery", "trailer": "delivery",
    "emergency": "emergency",
}


class FleetManager:
    def __init__(self, ctx, cooldown: int = 60):
        self.ctx = ctx
        self.engine = ctx.engine
        self.cooldown = cooldown
        self._fleet: dict[str, dict] = {}
        self._step = 0

    def tick(self, step: int) -> None:
        self._step = step

    # ── 识别 ────────────────────────────────────────────────

    def _categorize(self, veh_id: str, veh_type: str) -> str | None:
        low = veh_id.lower()
        for cat, prefixes in FLEET_CATEGORY_RULES.items():
            if any(low.startswith(p) for p in prefixes):
                return cat
        return CATEGORY_BY_TYPE.get(veh_type)

    def scan(self) -> list[str]:
        new: list[str] = []
        for vid in self.engine.get_vehicle_ids():
            if vid in self._fleet:
                continue
            try:
                vtype = self.engine.get_vehicle_state(vid)["type"]
            except Exception:  # noqa: BLE001
                continue
            cat = self._categorize(vid, vtype)
            if cat:
                self._fleet[vid] = self._new_record(vid, cat)
                new.append(vid)
        return new

    def _new_record(self, veh_id: str, category: str) -> dict:
        return {
            "id": veh_id, "category": category, "destination": None,
            "route": [], "reroutes": 0, "original_route": [], "history": [],
            "last_reroute_step": -self.cooldown, "auto_reroute": True,
            "manual": False, "in_network": True, "arrived": False,
            "estimated_saved": 0.0,
        }

    # ── 注册/注销 ───────────────────────────────────────────

    def register(self, veh_id: str, category: str, destination: str | None = None) -> dict:
        rec = self._fleet.get(veh_id)
        if rec is None:
            rec = self._new_record(veh_id, category)
            rec["manual"] = True
            self._fleet[veh_id] = rec
        rec["destination"] = destination or rec.get("destination")
        return rec

    def unregister(self, veh_id: str) -> bool:
        return self._fleet.pop(veh_id, None) is not None

    # ── 查询 ────────────────────────────────────────────────

    def fleet_vehicles(self, category: str | None = None) -> list[dict]:
        out = []
        for rec in self._fleet.values():
            if category and rec["category"] != category:
                continue
            rec = dict(rec)
            rec["current_edge"] = self._current_edge(rec["id"])
            out.append(rec)
        return out

    def _current_edge(self, veh_id: str) -> str | None:
        try:
            lane = self.engine.get_vehicle_state(veh_id)["lane"]
            return edge_from_lane(lane)
        except Exception:  # noqa: BLE001
            return None

    # ── 事件处理 ────────────────────────────────────────────

    def on_depart(self, veh_id: str) -> None:
        rec = self._fleet.get(veh_id)
        if rec:
            rec["in_network"] = True

    def on_arrive(self, veh_id: str) -> None:
        rec = self._fleet.get(veh_id)
        if rec:
            rec["arrived"] = True
            rec["in_network"] = False
            if not rec["manual"]:
                self._fleet.pop(veh_id, None)

    # ── 重路由管理 ──────────────────────────────────────────

    def can_reroute(self, veh_id: str) -> bool:
        rec = self._fleet.get(veh_id)
        if not rec or not rec["auto_reroute"]:
            return False
        return (self._step - rec["last_reroute_step"]) >= self.cooldown

    def record_reroute(self, veh_id: str, new_route: list[str], saved_time: float) -> None:
        rec = self._fleet.get(veh_id)
        if not rec:
            return
        if not rec["original_route"]:
            rec["original_route"] = list(rec["route"])
        rec["route"] = list(new_route)
        rec["reroutes"] += 1
        rec["history"].append(list(new_route))
        rec["last_reroute_step"] = self._step
        rec["estimated_saved"] += saved_time

    def status(self) -> dict:
        by_cat: dict[str, int] = {}
        active = arrived = reroutes = 0
        for rec in self._fleet.values():
            by_cat[rec["category"]] = by_cat.get(rec["category"], 0) + 1
            active += 1 if rec["in_network"] else 0
            arrived += 1 if rec["arrived"] else 0
            reroutes += rec["reroutes"]
        return {"registered": len(self._fleet), "active": active, "arrived": arrived,
                "reroutes": reroutes, "by_category": by_cat,
                "cooldown": self.cooldown}
