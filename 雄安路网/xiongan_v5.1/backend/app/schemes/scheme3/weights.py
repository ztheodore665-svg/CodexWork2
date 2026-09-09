"""边权重管理器：实时旅行时间 EWMA 平滑 + 负载惩罚 + 拥堵判定。"""

DEFAULT_PARAMS = {
    "alpha": 0.3, "sample_limit": 500, "load_penalty_per_vehicle": 2.0,
    "load_cap": 60.0, "congestion_occupancy": 0.7,
    "update_interval": 5,
}


class EdgeWeightManager:
    def __init__(self, ctx, params: dict | None = None):
        self.ctx = ctx
        self.engine = ctx.engine
        self.p = {**DEFAULT_PARAMS, **(params or {})}
        self._base: dict[str, float] = {}
        self._smoothed: dict[str, float] = {}
        self._load: dict[str, int] = {}
        self._updates = 0

    def init(self) -> None:
        for eid in self.engine.get_edge_ids():
            self._base[eid] = self._free_flow_time(eid)
            self._smoothed[eid] = self._base[eid]

    def _free_flow_time(self, eid: str) -> float:
        length = self.engine.get_edge_length(eid)
        limit = self.engine.get_edge_speed_limit(eid)
        return length / limit if limit > 0 else 1.0

    def update(self) -> None:
        edges = self.engine.get_edge_ids()[: self.p["sample_limit"]]
        for eid in edges:
            st = self.engine.get_edge_stats(eid)
            tt = st.get("travel_time", float("inf"))
            base = self._base.get(eid, self._free_flow_time(eid))
            measured = base if tt == float("inf") or tt <= 0 else tt
            measured = max(measured, base)  # 不低于自由流
            prev = self._smoothed.get(eid, measured)
            self._smoothed[eid] = self.p["alpha"] * measured + (1 - self.p["alpha"]) * prev
        self._updates += 1

    def weight(self, edge_id: str) -> float:
        smoothed = self._smoothed.get(edge_id, self._base.get(edge_id, 1.0))
        penalty = min(self.p["load_cap"],
                      self._load.get(edge_id, 0) * self.p["load_penalty_per_vehicle"])
        return smoothed + penalty

    def add_load(self, edge_id: str, vehicles: int = 1) -> None:
        self._load[edge_id] = self._load.get(edge_id, 0) + vehicles

    def remove_load(self, edge_id: str, vehicles: int = 1) -> None:
        self._load[edge_id] = max(0, self._load.get(edge_id, 0) - vehicles)

    def congested_edges(self) -> list[str]:
        return [e for e in self.engine.get_edge_ids()
                if self.engine.get_edge_stats(e)["occupancy"] > self.p["congestion_occupancy"]]

    def top_congested(self, n: int) -> list[tuple[str, float]]:
        rows = [(e, self.engine.get_edge_stats(e)["occupancy"])
                for e in self.engine.get_edge_ids()]
        return sorted(rows, key=lambda r: r[1], reverse=True)[:n]

    def status(self) -> dict:
        return {"edge_count": len(self._base), "updates": self._updates,
                "congested": len(self.congested_edges()),
                "total_load": sum(self._load.values()), "params": self.p}
