"""案例默认方案：Webster 最优配时执行器（与各路口 algorithm.py 逻辑一致）。

原理：在**路网配套车流文件可提供进口流量**的前提下（demo 案例目录带 *flow.xml），
按经典 Webster 公式求最优周期并分配各相位有效绿：
   关键流量比 y_i = 相位 i 服务进口流量 / 饱和流(1800 veh/h/车道)
   Y = Σy_i，损失 L = 相位切换损失（黄 3s + 启动损失）
   C0 = (1.5L + 5) / (1 - Y)，绿时 g_i = (C0 - L)·y_i / Y
仿真运行时在相位切换瞬间 setPhaseDuration 把该相位时长改为建议值（黄灯相位保留）。

无 *flow.xml 的路网（base_network 等）：无法估流，方案保持路网内嵌程序不变（等价固定配时基线），
因此作为"默认方案"对任何路网都安全。也可用 handle_action("recalculate") 随时重算。
"""

import glob
import os
import xml.etree.ElementTree as ET

from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.schemes.base import BaseScheme
from app.schemes.registry import register_scheme

GREEN = set("Gg")
SAT_FLOW = 1800.0      # veh/h/车道
MIN_GREEN = 8.0
PERIOD_MIN, PERIOD_MAX = 30.0, 120.0


def _read_flow_demands(net_file: str) -> dict[str, float]:
    """从与路网同目录的 *flow.xml 读取各进口(边)流量 veh/h。"""
    d = os.path.dirname(os.path.abspath(net_file))
    flows = sorted(glob.glob(os.path.join(d, "*.flow.xml")))
    if not flows:
        return {}
    demands: dict[str, float] = {}
    root = ET.parse(flows[0]).getroot()
    for fl in root.iter("flow"):
        frm = fl.get("from")
        if not frm:
            continue
        number = float(fl.get("number", 0) or 0)
        dur = (float(fl.get("end", 3600) or 3600)
               - float(fl.get("begin", 0) or 0))
        demands[frm] = demands.get(frm, 0.0) + (number * 3600.0 / dur if dur else 0.0)
    return demands


def plan_webster(net_file: str) -> dict[str, dict[int, float]] | None:
    """对路网计算每信号机的 Webster 配时：tls_id -> {phase_index: duration}。"""
    if not net_file or not os.path.isfile(net_file):
        return None
    demands = _read_flow_demands(net_file)
    if not demands:
        return None
    try:
        root = ET.parse(net_file).getroot()
    except Exception:  # noqa: BLE001
        return None
    lane_n: dict[str, int] = {}
    for e in root.iter("edge"):
        eid = e.get("id", "")
        if eid.startswith(":"):
            continue
        lane_n[eid] = sum(1 for _ in e.findall("lane"))
    plans: dict[str, dict[int, float]] = {}
    for tl in root.iter("tlLogic"):
        tid = tl.get("id")
        conns = sorted(
            [c for c in root.iter("connection") if c.get("tl") == tid],
            key=lambda c: int(c.get("linkIndex", 0)))
        phases = [(p.get("state", ""), float(p.get("duration", 3.0)))
                  for p in tl.findall("phase")]
        green_phases = [i for i, (st, _) in enumerate(phases)
                        if any(c in GREEN for c in st)]
        y, served = [], []
        for i in green_phases:
            st = phases[i][0]
            froms = set()
            for idx, ch in enumerate(st):
                if ch in GREEN and idx < len(conns) and conns[idx].get("from"):
                    froms.add(conns[idx].get("from"))
            s = sum(lane_n.get(f, 1) for f in froms) * SAT_FLOW
            q = sum(demands.get(f, 0.0) for f in froms)
            y.append(q / s if s else 0.0)
            served.append((i, froms))
        Y = sum(y)
        if Y <= 0:
            continue
        L = len(green_phases) * 4.0
        C0 = max(PERIOD_MIN, min(PERIOD_MAX, (1.5 * L + 5.0) / (1.0 - Y)))
        plan: dict[int, float] = {}
        for (i, _f), yy in zip(served, y):
            plan[i] = max(MIN_GREEN, round((C0 - L) * yy / Y, 1)) if Y > 0 else 0.0
        # 黄色等过渡相位沿用原时长（demo 为 3s）
        for i, (_st, du) in enumerate(phases):
            if i not in plan:
                plan[i] = du
        plans[tid] = plan
    return plans or None


@register_scheme
class WebsterController(BaseScheme):
    """案例默认 · Webster 最优配时（固定配时类，含 *flow.xml 时按流量重算）。"""

    name = "webster"

    algorithm_spec = AlgorithmSpec(
        kind="signal",
        description="案例默认：Webster 最优配时（读取路口 *flow.xml 流量算最优周期与绿时，"
                    "相位切换瞬间生效；无流量文件时保持路网原配时=固定配时基线）",
        params=[
            ParamSpec("period_min", "最小周期", type="number", default=PERIOD_MIN,
                      minimum=20, maximum=60, unit="s", desc="Webster 周期下限"),
            ParamSpec("period_max", "最大周期", type="number", default=PERIOD_MAX,
                      minimum=60, maximum=240, unit="s", desc="Webster 周期上限"),
        ],
        observables=["vehicle_count", "avg_queue", "tls_queues"],
        metrics=["plan_tls", "applied_phase_sets", "sources"],
        capabilities=["recalculate"],
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        self.period_min = float(ctx.config.get("period_min", PERIOD_MIN))
        self.period_max = float(ctx.config.get("period_max", PERIOD_MAX))
        self._plans: dict[str, dict[int, float]] = {}
        self._last_phase: dict[str, int] = {}
        self._applied = 0
        self._phase_changes = 0

    def init(self) -> None:
        self._recompute()

    def on_step(self) -> None:
        for tid in list(self._plans):
            try:
                idx = int(self.ctx.engine.get_tls_state(tid)["phase_index"])
            except Exception:  # noqa: BLE001
                continue
            if self._last_phase.get(tid) == idx:
                continue
            self._last_phase[tid] = idx
            dur = self._plans[tid].get(idx)
            if dur is None:
                continue
            try:
                self.ctx.engine.set_tls_phase_duration(tid, float(dur))
                self._applied += 1
            except Exception:  # noqa: BLE001
                pass
            self._phase_changes += 1

    def _recompute(self) -> dict:
        net = self.ctx.engine.net_file()
        self._plans = plan_webster(net) or {}
        return {"tls_planned": len(self._plans),
                "source": "flow.xml 估流" if self._plans else "无流量文件，保持路网原配时"}

    def handle_action(self, action: str, params: dict) -> dict:
        if action == "recalculate":
            return {"ok": True, **self._recompute()}
        if action == "get_status":
            return {"ok": True, "tls_planned": len(self._plans),
                    "plans": {k: v for k, v in self._plans.items()},
                    "source": ("flow.xml 估流" if self._plans
                               else "无流量文件，保持路网原配时"),
                    "applied_phase_sets": self._applied,
                    "phase_changes": self._phase_changes}
        if action == "get_params":
            return {"ok": True, "params": {
                "period_min": self.period_min, "period_max": self.period_max}}
        if action == "set_params":
            if "period_min" in params:
                self.period_min = float(params["period_min"])
            if "period_max" in params:
                self.period_max = float(params["period_max"])
            self._recompute()
            return {"ok": True}
        return {"ok": False, "message": f"未知动作: {action}"}

    def cleanup(self) -> None:
        self._plans = {}
