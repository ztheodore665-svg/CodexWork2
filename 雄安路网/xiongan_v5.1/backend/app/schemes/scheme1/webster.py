"""Webster 最优配时优化器：按实时流量计算各交叉口周期与绿灯分配。"""

from app.core.datacollector import edge_from_lane

SATURATION_FLOW = {"arterial": 1800, "secondary": 1600, "local": 1400, "default": 1600}
YELLOW_TIME = 3.0
ALL_RED_TIME = 2.0
MIN_GREEN = 8.0
CYCLE_MIN, CYCLE_MAX = 40, 240
SATURATION_CAP = 0.95


class WebsterOptimizer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.engine = ctx.engine
        # tls_id -> {phase_index: [edge_ids]}
        self.phase_edges: dict[str, dict[int, list[str]]] = self._build_phase_edges()
        self._flows: dict[str, dict[tuple, float]] = {}
        self._using_default: dict[str, bool] = {}

    def _build_phase_edges(self) -> dict[str, dict[int, list[str]]]:
        mapping: dict[str, dict[int, list[str]]] = {}
        for tid in self.engine.get_tls_ids():
            try:
                conns = self.engine.get_tls_connections(tid)
            except Exception:  # noqa: BLE001
                continue
            mapping[tid] = {phase: list({edge_from_lane(l) for l in lanes})
                            for phase, lanes in conns.items()}
        return mapping

    # ── 流量采集 ────────────────────────────────────────────

    def collect_flows(self) -> None:
        self._flows = {}
        for tid, phases in self.phase_edges.items():
            self._flows[tid] = {}
            for phase, edges in phases.items():
                for e in edges:
                    stats = self.engine.get_edge_stats(e)
                    q = self._estimate_flow(stats)
                    self._flows[tid][(phase, e)] = q

    @staticmethod
    def _estimate_flow(stats: dict) -> float:
        occ = stats.get("occupancy", 0.0)
        tt = stats.get("travel_time", float("inf"))
        if occ >= 0.05:
            return occ * 1800.0
        if tt and tt != float("inf") and tt > 0:
            return 1800.0 / tt
        return 0.0

    # ── 配时计算 ────────────────────────────────────────────

    def _saturation(self, edge_id: str) -> float:
        limit = self.engine.get_edge_speed_limit(edge_id)
        if limit >= 16.7:
            return SATURATION_FLOW["arterial"]
        if limit >= 11.1:
            return SATURATION_FLOW["secondary"]
        return SATURATION_FLOW["local"]

    def _opt_cycle(self, total_loss: float, total_ratio: float) -> int:
        if total_ratio >= SATURATION_CAP:
            return CYCLE_MAX
        c = (1.5 * total_loss + 5.0) / (1.0 - total_ratio)
        return max(CYCLE_MIN, min(CYCLE_MAX, int(round(c))))

    def compute_timing(self, tls_id: str, period_range: tuple[int, int]) -> dict:
        phases_edges = self.phase_edges.get(tls_id, {})
        if not phases_edges:
            return {"tls_id": tls_id, "cycle": CYCLE_MIN, "phases": [],
                    "offset": 0.0, "total_loss": 0.0, "total_flow_ratio": 0.0,
                    "using_default": True, "step": 0}
        flows = self._flows.get(tls_id, {})
        losses = 0.0
        ratios = {}
        for phase, edges in phases_edges.items():
            q = sum(flows.get((phase, e), 0.0) for e in edges)
            s = max((self._saturation(e) for e in edges), default=SATURATION_FLOW["default"])
            ratios[phase] = q / s if s > 0 else 0.0
            losses += YELLOW_TIME + ALL_RED_TIME
        Y = sum(ratios.values())
        cycle = self._opt_cycle(losses, Y)
        using_default = Y >= SATURATION_CAP
        cycle = max(period_range[0], min(period_range[1], cycle))
        total_loss = losses
        if Y > 0:
            greens = {p: (r / Y) * (cycle - total_loss) for p, r in ratios.items()}
        else:
            n = max(1, len(phases_edges))
            greens = {p: (cycle - total_loss) / n for p in phases_edges}
        phases = []
        for phase, edges in phases_edges.items():
            g = max(MIN_GREEN, greens.get(phase, MIN_GREEN))
            phases.append({
                "phase_index": phase,
                "effective_green": round(g, 1),
                "yellow": YELLOW_TIME,
                "all_red": ALL_RED_TIME,
                "loss": YELLOW_TIME + ALL_RED_TIME,
                "display_green": round(g, 1),
            })
        return {
            "tls_id": tls_id, "cycle": cycle, "phases": phases, "offset": 0.0,
            "total_loss": round(total_loss, 2), "total_flow_ratio": round(Y, 3),
            "using_default": using_default, "step": 0,
        }

    def compute_all(self, period_range: tuple[int, int]) -> dict[str, dict]:
        return {tid: self.compute_timing(tid, period_range) for tid in self.phase_edges}

    def apply(self, timing: dict) -> None:
        for tid, plan in timing.items():
            for phase in plan.get("phases", []):
                self.engine.set_tls_phase(tid, phase["phase_index"],
                                          duration=phase["display_green"])
