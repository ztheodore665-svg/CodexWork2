"""SCOOT-like 自适应控制器：MAPPO 不可用时的降级信号控制。"""

SCOOT_PARAMS = {
    "min_cycle": 40, "max_cycle": 180, "min_green": 8, "max_green": 60,
    "yellow": 3.0, "all_red": 2.0, "extend_threshold": 0.3,
    "switch_threshold": 0.15, "adjust_interval": 300, "base_cycle": 90,
    "saturation_high": 0.8, "saturation_low": 0.3,
}


class SCOOTController:
    def __init__(self, ctx, params: dict | None = None):
        self.ctx = ctx
        self.engine = ctx.engine
        self.p = {**SCOOT_PARAMS, **(params or {})}
        self._cycle = self.p["base_cycle"]
        self._elapsed: dict[str, float] = {}
        self._switch_count = 0
        self._extend_count = 0
        self._cycle_adjust_count = 0

    # ── 决策 ────────────────────────────────────────────────

    def decide(self, tls_id: str, phase_state: dict, demands: dict) -> str:
        """返回 keep/switch/extend。demands: {phase_index: 归一化需求}。

        elapsed 优先取引擎返回的真实已持续时长（phase_state["elapsed"]），
        缺省回退到内部累计计数器。
        """
        elapsed = phase_state.get("elapsed",
                                  self._elapsed.get(tls_id, 0.0))
        cur = phase_state.get("phase_index", 0)
        min_g, max_g = self.p["min_green"], self.p["max_green"]
        cur_demand = demands.get(cur, 0.0)
        other_demand = sum(d for k, d in demands.items() if k != cur)

        if elapsed < min_g:
            return "keep"
        if elapsed >= max_g:
            self._switch_count += 1
            return "switch"
        if cur_demand <= self.p["switch_threshold"] \
                and other_demand > self.p["extend_threshold"]:
            self._switch_count += 1
            return "switch"
        if cur_demand > self.p["extend_threshold"] and elapsed < max_g:
            self._extend_count += 1
            return "extend"
        return "keep"

    def track_elapsed(self, tls_id: str, phase_state: dict) -> None:
        """按已分配相位时长近似推进已持续时间（无引擎 elapsed 时的回退）。"""
        self._elapsed[tls_id] = self._elapsed.get(tls_id, 0.0) + phase_state.get("phase_duration", 0.0)

    # ── 周期自适应 ──────────────────────────────────────────

    def adjust_cycle(self, saturation: float) -> int:
        """基于饱和度增减周期（纯函数，基于当前 self._cycle，不在此提交）。"""
        if saturation > self.p["saturation_high"]:
            return max(self.p["min_cycle"], int(self._cycle * 1.1))
        if saturation < self.p["saturation_low"]:
            return min(self.p["max_cycle"], int(self._cycle * 0.9))
        return self._cycle

    def commit_cycle(self, new_cycle: int) -> None:
        new_cycle = max(self.p["min_cycle"], min(self.p["max_cycle"], new_cycle))
        if new_cycle != self._cycle:
            self._cycle = new_cycle
            self._cycle_adjust_count += 1

    def maybe_adjust_cycle(self, saturation: float) -> int:
        """每 adjust_interval 调用一次，直接提交调整。"""
        new = self.adjust_cycle(saturation)
        self.commit_cycle(new)
        return self._cycle

    # ── 绿信比分发 ──────────────────────────────────────────

    def distribute_greens(self, demands: dict, cycle: int | None = None) -> dict:
        cycle = cycle or self._cycle
        usable = cycle - self.p["yellow"] - self.p["all_red"]
        total = sum(demands.values())
        greens: dict = {}
        if total <= 0:
            share = usable / max(1, len(demands))
            for k in demands:
                greens[k] = max(self.p["min_green"], round(share, 1))
            return greens
        for k, d in demands.items():
            g = usable * (d / total)
            greens[k] = max(self.p["min_green"], round(g, 1))
        return greens

    def status(self) -> dict:
        return {"active_intersections": len(self._elapsed),
                "switch_count": self._switch_count,
                "extend_count": self._extend_count,
                "cycle_adjust_count": self._cycle_adjust_count,
                "cycle": self._cycle, "params": self.p}
