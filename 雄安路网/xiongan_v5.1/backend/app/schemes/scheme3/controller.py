"""方案三控制器：受限车队路线引导（动态 Dijkstra + 负载均衡）。"""

from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.schemes.base import BaseScheme
from app.schemes.registry import register_scheme
from app.schemes.scheme3.fleet import FleetManager
from app.schemes.scheme3.graph import RoadNetwork
from app.schemes.scheme3.router import RoutePlanner
from app.schemes.scheme3.weights import EdgeWeightManager

UPDATE_INTERVAL = 5
SCAN_INTERVAL = 10
POSITION_INTERVAL = 10
REROUTE_INTERVAL = 30
MAX_REROUTE_PER_ROUND = 20


@register_scheme
class Scheme3Controller(BaseScheme):
    """方案三：受限车队路线引导。"""

    name = "scheme_3"

    # 标准化声明（MCP-like）；kind=vehicle：本方案为车辆级路径引导，非信号控制
    algorithm_spec = AlgorithmSpec(
        kind="vehicle",
        description="方案三：受限车队路线引导（动态 Dijkstra + 负载均衡，车端协同）",
        params=[
            ParamSpec("auto_reroute", "自动重路由", type="bool", default=True,
                      desc="是否周期性地为车队自动重规划路线"),
            ParamSpec("cooldown", "重路由冷却", type="number", default=30,
                      minimum=0, maximum=600, unit="步",
                      desc="同一车辆两次重路由的最小间隔"),
        ],
        observables=["vehicle_count", "congestion", "edge_flows"],
        metrics=["auto_reroutes", "manual_reroutes"],
        capabilities=["reroute_fleet", "register_fleet", "add_restricted_zone",
                      "enable_auto_reroute", "get_congestion_map"],
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        self.network = RoadNetwork(ctx)
        self.weights = EdgeWeightManager(ctx)
        self.fleet = FleetManager(ctx)
        self.planner = RoutePlanner(ctx, self.network, self.weights)
        self._auto_reroute = True
        self._step = 0
        self._auto_reroute_count = 0
        self._manual_reroute_count = 0

    # ── 生命周期 ────────────────────────────────────────────

    def init(self) -> None:
        self.weights.init()
        self.fleet.scan()
        self._load_restricted_zones()

    def _load_restricted_zones(self) -> None:
        zones = self.ctx.config.get("restricted_zones") or []
        for zone in zones:
            for eid in zone.get("edge_ids", []):
                self.network.add_restricted_edge(eid, zone.get("reason", "config"))

    def on_step(self) -> None:
        self._step += 1
        self.fleet.tick(self._step)
        if self._step % UPDATE_INTERVAL == 0:
            self.weights.update()
        if self._step % SCAN_INTERVAL == 0:
            self.fleet.scan()
        if self._step % POSITION_INTERVAL == 0:
            self._update_fleet_loads()
        if self._auto_reroute and self._step % REROUTE_INTERVAL == 0:
            self._auto_reroute_fleet()

    def cleanup(self) -> None:
        pass

    # ── 自动重路由 ──────────────────────────────────────────

    def _auto_reroute_fleet(self) -> None:
        congested = set(self.weights.congested_edges())
        if not congested:
            return
        count = 0
        for rec in self.fleet.fleet_vehicles():
            if count >= MAX_REROUTE_PER_ROUND:
                break
            vid = rec["id"]
            if rec.get("current_edge") not in congested:
                continue
            if not self.fleet.can_reroute(vid):
                continue
            dest = rec.get("destination")
            if not dest:
                continue
            frm = rec["current_edge"]
            before = self._est_time(frm, dest)
            out = self.planner.plan_batch([{"vehicle_id": vid, "from": frm, "to": dest}])[0]
            if out["ok"] and out["route"]:
                after = self._route_time(out["route"])
                self.fleet.record_reroute(vid, out["route"], max(0.0, before - after))
                self._auto_reroute_count += 1
                count += 1
                self.ctx.push_event("fleet_rerouted",
                                    f"{vid} 自动重路由，节省约 {max(0.0, before - after):.1f}s",
                                    {"vehicle": vid, "route": out["route"]})

    def _est_time(self, frm: str, to: str) -> float:
        route = self.planner.shortest_path(frm, to)
        return self._route_time(route) if route else 0.0

    def _route_time(self, route: list[str]) -> float:
        return sum(self.weights.weight(e) for e in route)

    def _update_fleet_loads(self) -> None:
        for rec in self.fleet.fleet_vehicles():
            cur = rec.get("current_edge")
            if cur:
                self.weights.add_load(cur, 1)

    # ── API 动作 ────────────────────────────────────────────

    def handle_action(self, action: str, params: dict) -> dict:
        if action == "reroute_fleet":
            return self._reroute_fleet(params)
        if action == "register_fleet":
            vids = params.get("vehicle_ids", [])
            cat = params.get("category", "custom")
            dests = params.get("destinations") or {}
            if isinstance(dests, list):   # 支持按序列表 ["E3", ...]
                dests = {v: dests[i] for i, v in enumerate(vids) if i < len(dests)}
            for vid in vids:
                self.fleet.register(vid, cat, dests.get(vid))
            return {"ok": True, "registered": len(vids)}
        if action == "unregister_fleet":
            removed = [vid for vid in params.get("vehicle_ids", [])
                       if self.fleet.unregister(vid)]
            return {"ok": True, "removed": removed}
        if action == "add_restricted_zone":
            for eid in params.get("edge_ids", []):
                self.network.add_restricted_edge(eid, params.get("reason", "manual"))
            return {"ok": True}
        if action == "remove_restricted_zone":
            for eid in params.get("edge_ids", []):
                self.network.remove_restricted_edge(eid)
            return {"ok": True}
        if action == "clear_restricted_zones":
            self.network.clear_restricted_edges()
            return {"ok": True}
        if action == "enable_auto_reroute":
            self._auto_reroute = bool(params.get("enabled", True))
            return {"ok": True, "enabled": self._auto_reroute}
        if action == "set_params":
            self.fleet.cooldown = int(params.get("cooldown", self.fleet.cooldown))
            if "auto_reroute" in params:
                self._auto_reroute = bool(params["auto_reroute"])
            return {"ok": True}
        if action == "get_params":
            return {"ok": True, "params": {
                "auto_reroute": self._auto_reroute,
                "cooldown": self.fleet.cooldown,
            }}
        if action == "get_fleet_status":
            return {"ok": True,
                    "fleet": self.fleet.fleet_vehicles(params.get("category")),
                    "status": self.fleet.status()}
        if action == "get_route_recommendations":
            recs = self.planner.recommend(params.get("from_edge"), params.get("to_edge"),
                                          params.get("k"))
            return {"ok": True, "recommendations": recs}
        if action == "get_congestion_map":
            return {"ok": True, "congested": self.weights.congested_edges()}
        if action == "get_status":
            return self._get_status()
        return {"ok": False, "message": f"未知动作: {action}"}

    def get_internal_metrics(self) -> dict:
        st = self._get_status()
        return {"auto_reroutes": st["stats"]["auto_reroutes"],
                "manual_reroutes": st["stats"]["manual_reroutes"]}

    def _reroute_fleet(self, params: dict) -> dict:
        vids = params.get("vehicle_ids", [])
        dests = params.get("destinations") or {}
        if isinstance(dests, list):
            dests = {v: dests[i] for i, v in enumerate(vids) if i < len(dests)}
        results = []
        for vid in vids:
            dest = dests.get(vid) or self.fleet._fleet.get(vid, {}).get("destination")
            rec = self.fleet._fleet.get(vid, {})
            frm = rec.get("current_edge") or self._current_edge_of(vid)
            if not dest:
                results.append({"vehicle_id": vid, "ok": False, "message": "无目的地"})
                continue
            out = self.planner.plan_batch([{"vehicle_id": vid, "from": frm, "to": dest}])[0]
            results.append(out)
            if out["ok"] and out["route"]:
                self.fleet.record_reroute(vid, out["route"], 0.0)
                self._manual_reroute_count += 1
        return {"ok": True, "results": results}

    def _current_edge_of(self, vid: str) -> str:
        try:
            from app.core.datacollector import edge_from_lane
            return edge_from_lane(self.ctx.engine.get_vehicle_state(vid)["lane"])
        except Exception:  # noqa: BLE001
            return ""

    def _get_status(self) -> dict:
        return {
            "network": {"nodes": self.network.node_count(),
                        "edges": self.network.edge_count(),
                        "restricted": len(self.network.restricted_edges())},
            "weights": self.weights.status(),
            "fleet": self.fleet.status(),
            "planner": self.planner.status(),
            "auto_reroute": self._auto_reroute,
            "stats": {"auto_reroutes": self._auto_reroute_count,
                      "manual_reroutes": self._manual_reroute_count},
        }
