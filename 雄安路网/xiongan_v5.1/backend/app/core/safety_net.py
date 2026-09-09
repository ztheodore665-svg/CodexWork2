"""方案无关安全网：防溢出示绿 + 路口死锁清空（对无内部车道路网亦适用）。

目标：缓解"超饱和下排队溢出→路口堵死→长时间不恢复"。对所有方案生效
（scheme 之后每步执行），不改变方案本身的相位结构。

本路网为 --no-internal-links（无内部车道 :），车辆在进口车道停车线等绿、
过路口时直接由进口车道进入出口车道。故"死锁"表现为：
- 某 link 为绿，但其出口边（to_edge）已满载（occupancy 超阈值）——给绿也是
  浪费，车辆进了路口却出不去，会把路口堵死 → **防溢出示绿**：强制该 link 变红。
- 某路口持续被溢出示绿压制（说明其出口长期堵死）→ **死锁清空**：进入清空态，
  只保留"出口有空间"的方向绿，其余全红，让路口内车辆有机会排出；清空后恢复。

link 结构从 net.xml <connection> 解析（from/to/via/dir，按 linkIndex 与
state_str 对齐），不依赖 getControlledLinks 的元组顺序假设。
"""

from __future__ import annotations

GREEN_CHARS = "Gg"      # 主绿 + 让行绿都视为"可以走"


def n_green_in_phase(chars: list[str]) -> int:
    return sum(1 for c in chars if c in GREEN_CHARS)


class SafetyNet:
    def __init__(self, engine, config: dict | None = None):
        self._engine = engine
        cfg = config or {}
        self._enabled = bool(cfg.get("enabled", False))
        self._spill_occ_threshold = float(cfg.get("spill_occ_threshold", 0.30))
        self._spill_speed_threshold = float(cfg.get("spill_speed_threshold", 0.5))
        self._stuck_seconds = float(cfg.get("stuck_seconds", 12.0))
        self._clear_hold_seconds = float(cfg.get("clear_hold_seconds", 6.0))
        self._max_clear_seconds = float(cfg.get("max_clear_seconds", 60.0))
        self._links: dict[str, list[dict]] = {}
        self._blocked_since: dict[str, dict[int, float]] = {}  # tls -> linkIdx -> 起始步
        self._clear_until: dict[str, float] = {}               # tls -> 清空截止步
        self._clear_started: dict[str, float] = {}
        self._stats = {"spill_suppress": 0, "clear_trigger": 0,
                       "clear_active_steps": 0, "overrides": 0}

    def _exit_blocked(self, to_edge: str) -> bool:
        """出口边是否"真正堵死"：高占有率 + 均速≈0（停住排队），而非慢速蠕动。"""
        if not to_edge:
            return False
        try:
            st = self._engine.get_edge_stats(to_edge)
        except Exception:  # noqa: BLE001
            return False
        return (st["occupancy"] >= self._spill_occ_threshold
                and st["mean_speed"] < self._spill_speed_threshold)

    # ── 静态映射（复用 engine.get_tls_conn_details，与 state_str 天然对齐） ──

    def _load_links(self) -> None:
        if self._links or not self._engine.net_file():
            return
        for tid in self._engine.get_tls_ids():
            self._links[tid] = self._engine.get_tls_conn_details(tid)

    # ── 每步执行 ────────────────────────────────────────────

    def on_step(self, step: int) -> None:
        if not self._enabled:
            return
        import traci
        try:
            self._load_links()
            for tid in self._engine.get_tls_ids():
                try:
                    st = self._engine.get_tls_state(tid)
                except Exception:  # noqa: BLE001
                    continue
                links = self._links.get(tid, [])
                if not links:
                    continue
                new_state = self._override_state(tid, st["state_str"],
                                                 links, step)
                if new_state is not None and new_state != st["state_str"]:
                    try:
                        traci.trafficlight.setRedYellowGreenState(tid, new_state)
                        self._stats["overrides"] += 1
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001 安全网永不崩溃主流程
            pass

    def _override_state(self, tid: str, state: str, links: list[dict],
                        step: int) -> str | None:
        chars = list(state)
        n = min(len(chars), len(links))
        changed = False
        # 1) 防溢出示绿：绿 link 的出口边堵死 → 变红；但路口至少保留一个
        #    排空方向（其出口空间最大），避免整体全红冻结。
        blocked_now: list[int] = []
        suppress_idx: list[int] = []
        for i in range(n):
            if chars[i] not in GREEN_CHARS:
                continue
            if self._exit_blocked(links[i].get("to_edge", "")):
                suppress_idx.append(i)
        kept = None
        if len(suppress_idx) == n_green_in_phase(chars):
            # 所有绿都堵死：保留出口空间最大的那个，其余压红（防全红冻结）
            def _space(i: int) -> float:
                st = self._engine.get_edge_stats(links[i].get("to_edge", ""))
                return st.get("occupancy", 1.0)
            kept = min(suppress_idx, key=_space)
            suppress_idx = [i for i in suppress_idx if i != kept]
        for i in suppress_idx:
            chars[i] = "r"
            changed = True
            self._stats["spill_suppress"] += 1
            blocked_now.append(i)
        # 持续被压制的 link 计数 → 触发死锁清空
        blk = self._blocked_since.setdefault(tid, {})
        for i in blocked_now:
            if i not in blk:
                blk[i] = float(step)
        for i in list(blk):
            if i not in blocked_now:
                del blk[i]
        if blocked_now and tid not in self._clear_until:
            oldest = min(blk.get(i, step) for i in blocked_now)
            if step - oldest >= self._stuck_seconds:
                self._clear_until[tid] = step + self._clear_hold_seconds
                self._clear_started[tid] = step
                self._stats["clear_trigger"] += 1
        # 2) 死锁清空：清空态只保留"出口有空"的方向绿，其余全红，让路口排出
        if tid in self._clear_until:
            if step < self._clear_until[tid] and \
                    step - self._clear_started.get(tid, step) < self._max_clear_seconds:
                for i in range(n):
                    if chars[i] in GREEN_CHARS and \
                            self._exit_blocked(links[i].get("to_edge", "")):
                        chars[i] = "r"
                        changed = True
                self._stats["clear_active_steps"] += 1
                return "".join(chars) if changed else None
            del self._clear_until[tid]
            del self._clear_started[tid]
        return "".join(chars) if changed else None

    # ── 状态/指标 ───────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "spill_occ_threshold": self._spill_occ_threshold,
            "spill_speed_threshold": self._spill_speed_threshold,
            "stuck_seconds": self._stuck_seconds,
            "active_clears": {t: round(self._clear_until[t] - self._clear_started[t], 1)
                              for t in self._clear_until},
        }

    def get_internal_metrics(self) -> dict:
        return dict(self._stats)
