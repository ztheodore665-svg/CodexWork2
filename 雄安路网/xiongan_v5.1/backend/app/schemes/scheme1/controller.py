"""方案一控制器：TOD 调度 + Webster 配时 + MAXBAND 绿波协调。"""

from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.schemes.base import BaseScheme
from app.schemes.registry import register_scheme
from app.schemes.scheme1.maxband import GreenWaveCoordinator
from app.schemes.scheme1.tod import TODSchedule
from app.schemes.scheme1.webster import WebsterOptimizer

RECALC_INTERVAL = 300


@register_scheme
class Scheme1Controller(BaseScheme):
    """方案一：分时多算法切换（TOD + Webster + MAXBAND 绿波）。"""

    name = "scheme_1"

    # 标准化声明（MCP-like：参数/观测/指标/能力）
    algorithm_spec = AlgorithmSpec(
        kind="signal",
        description="方案一：分时调度 + Webster 配时 + MAXBAND 绿波协调（固定配时类）",
        params=[
            ParamSpec("green_wave", "绿波协调", type="bool", default=True,
                      desc="是否启用 MAXBAND 绿波协调（对走廊路口统一周期与相位差）"),
            ParamSpec("recalc_interval", "配时重算间隔", type="number",
                      default=RECALC_INTERVAL, minimum=60, maximum=900, unit="步",
                      desc="每隔多少仿真步重新计算一次配时"),
        ],
        observables=["edge_flows", "edge_queues", "tls_queues", "tls_waiting",
                     "congestion"],
        metrics=["transitions", "recalculations"],
        capabilities=["recalculate", "switch_tod_plan", "enable_green_wave",
                      "add_corridor"],
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        self.tod = TODSchedule()
        self.webster = WebsterOptimizer(ctx)
        self.maxband = GreenWaveCoordinator(ctx)
        self._step = 0
        self._last_recalc = -999
        self._recalc_count = 0
        self._transition_count = 0
        self._last_action = ""
        self._timing: dict = {}
        self._recalc_interval = float(
            ctx.config.get("recalc_interval", RECALC_INTERVAL))

    # ── 生命周期 ────────────────────────────────────────────

    def init(self) -> None:
        self.webster.collect_flows()
        self.maxband.detect_corridors()
        self._recalculate()

    def on_step(self) -> None:
        self._step += 1
        sim_time = int(self.ctx.engine.get_sim_time())
        if self.tod.transition_detected(sim_time):
            self._transition_count += 1
            self._recalculate()
        elif self._step - self._last_recalc >= self._recalc_interval:
            self._recalculate()

    def cleanup(self) -> None:
        self._timing = {}

    # ── 配时重算 ────────────────────────────────────────────

    def _recalculate(self) -> None:
        sim_time = int(self.ctx.engine.get_sim_time())
        period = self.tod.get_period(sim_time)
        period_range = self.tod.get_cycle_range(period)
        self.webster.collect_flows()
        timing = self.webster.compute_all(period_range)
        if self.tod.green_wave_enabled(period):
            cycle = self.maxband.unified_cycle([p["cycle"] for p in timing.values()])
            self.maxband.apply_offsets(timing, cycle)
        self.webster.apply(timing)
        self._timing = timing
        self._last_recalc = self._step
        self._recalc_count += 1
        self.ctx.push_event("timing_recalculated",
                            f"配时已重算: 时段={period}，路口数={len(timing)}")

    # ── API 动作 ────────────────────────────────────────────

    def handle_action(self, action: str, params: dict) -> dict:
        self._last_action = action
        if action == "switch_tod_plan":
            plan = params.get("plan_name", "")
            try:
                self.tod.force(plan)
            except ValueError as exc:
                return {"ok": False, "message": str(exc)}
            self._recalculate()
            return {"ok": True, "period": plan}
        if action == "release_force":
            self.tod.release_force()
            self._recalculate()
            return {"ok": True, "period": self.tod.get_period(
                int(self.ctx.engine.get_sim_time()))}
        if action == "recalculate":
            self._recalculate()
            return {"ok": True, "count": self._recalc_count}
        if action == "enable_green_wave":
            enabled = bool(params.get("enabled", True))
            self.ctx.config["green_wave"] = enabled
            self._recalculate()
            return {"ok": True, "enabled": enabled}
        if action == "add_corridor":
            corr = self.maxband.add_corridor(
                params.get("corridor_id", "manual"),
                params.get("direction", "EW"),
                params.get("tls_ids", []),
                params.get("positions"),
                params.get("travel"))
            self._recalculate()
            return {"ok": True, "corridor": corr["id"]}
        if action == "get_status":
            return self._get_status()
        if action == "get_params":
            return {"ok": True, "params": {
                "green_wave": bool(self.ctx.config.get("green_wave", True)),
                "recalc_interval": self._recalc_interval,
            }}
        if action == "set_params":
            if "green_wave" in params:
                self.ctx.config["green_wave"] = bool(params["green_wave"])
            if "recalc_interval" in params:
                self._recalc_interval = float(params["recalc_interval"])
            self._recalculate()
            return {"ok": True}
        return {"ok": False, "message": f"未知动作: {action}"}

    def get_internal_metrics(self) -> dict:
        st = self._get_status()
        return {"transitions": st["stats"]["transitions"],
                "recalculations": st["stats"]["recalculations"],
                "coordinated_corridors": st["timing_stats"]["coordinated_corridors"],
                "avg_cycle": st["timing_stats"]["avg_cycle"]}

    # ── 状态查询 ────────────────────────────────────────────

    def _get_status(self) -> dict:
        sim_time = int(self.ctx.engine.get_sim_time())
        period = self.tod.get_period(sim_time)
        default_count = sum(1 for p in self._timing.values() if p.get("using_default"))
        avg_cycle = (sum(p["cycle"] for p in self._timing.values()) / len(self._timing)
                     if self._timing else 0)
        return {
            "period": period,
            "forced": self.tod.is_forced(),
            "corridors": [{"id": c["id"], "direction": c["direction"],
                           "tls_ids": c["tls_ids"]} for c in self.maxband.corridors],
            "timing_stats": {
                "intersection_count": len(self._timing),
                "default_count": default_count,
                "avg_cycle": round(avg_cycle, 1),
                "coordinated_corridors": len(self.maxband.corridors),
            },
            "offset_plans": {tid: p.get("offset", 0) for tid, p in self._timing.items()},
            "stats": {
                "transitions": self._transition_count,
                "recalculations": self._recalc_count,
                "last_action": self._last_action,
            },
        }
