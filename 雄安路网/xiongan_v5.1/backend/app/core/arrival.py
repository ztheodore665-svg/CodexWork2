"""峰期持续到达器：一次性投放后路网会逐渐放空，本模块按峰期强度持续随机补车。

设计：
- 仅"密度生成场景"（sparse/normal/peak/extreme/hotspot）启用；
  ""（渐入·路网自带车流）与无场景不启用，保持原有行为。
- 每 ARRIVE_INTERVAL 仿真秒决策一批：期望新增辆数 = 该场景强度（每 10s 全网
  期望）→ 随机取整；从路网**入口边**（无前驱边；无入口时退化为全部有后继边）
  随机选起点，随机续走生成一条可出网路由，动态 add_vehicle_route 投入。
- 在网车辆数达到该场景上限时暂停补充（防极端场景把路网塞爆），随车辆到达
  终点离场后自动恢复补充，形成"始终有新车流、又不过饱和"的峰期流。
- 每步由 runtime._on_step 调用 decide(step)。
"""

import random

# 每 10 仿真秒全网期望新增辆数（峰期强度，可按评审观感调参）
ARRIVAL_PER_10S: dict[str, float] = {
    "sparse": 3.0,    # 深夜 · 低流量
    "normal": 7.0,    # 平峰 · 中流量
    "peak": 12.0,     # 高峰 · 高流量
    "extreme": 18.0,  # 极高峰 · 拥堵
    "hotspot": 10.0,  # 区域热点（含外围补充）
}
# 在网车辆数软上限（超过则本批不补，等离场后恢复）
MAX_ACTIVE: dict[str, int] = {
    "sparse": 120,
    "normal": 260,
    "peak": 360,
    "extreme": 480,
    "hotspot": 400,
}

ARRIVE_INTERVAL = 10      # 决策间隔（仿真秒）
ROUTE_MAX_LEN = 12        # 随机续走路由最大边数（保证能较快出网/离场）


class ContinuousArrival:
    """按峰期强度持续随机补车（附在网软上限）。"""

    def __init__(self, engine, scenario: str):
        self.engine = engine
        self.scenario = scenario if scenario in ARRIVAL_PER_10S else "normal"
        self.rate = float(ARRIVAL_PER_10S[self.scenario])
        self.cap = int(MAX_ACTIVE[self.scenario])
        self._entries: list[str] | None = None
        self._seq = 0
        self._next_at = 0
        self._spawned = 0        # 持续到达累计补充辆数（供状态/日志）
        self._skipped_cap = 0    # 因达到在网上限跳过的批次数

    # ── 路网入口边 ─────────────────────────────────────────

    def _entry_edges(self) -> list[str]:
        """入口边：没有前驱的边（车流从这些边进入路网）。"""
        if self._entries is not None:
            return self._entries
        try:
            all_e = self.engine.get_edge_ids()
            succ_set: set[str] = set()
            for e in all_e:
                succ_set.update(self.engine.get_edge_successors(e))
            entries = [e for e in all_e if e not in succ_set]
            if not entries:
                # 闭合路网无边界：退化为"有后继"的边里随机起点
                entries = [e for e in all_e
                           if self.engine.get_edge_successors(e)]
        except Exception:  # noqa: BLE001 获取失败则退化为空（不补车不崩溃）
            entries = []
        self._entries = entries or []
        return self._entries

    def _random_route(self) -> list[str] | None:
        """从随机入口随机续走一条路由（可出网/到终点）。"""
        entries = self._entry_edges()
        if not entries:
            return None
        route = [random.choice(entries)]
        cur = route[0]
        for _ in range(ROUTE_MAX_LEN):
            succ = self.engine.get_edge_successors(cur)
            cands = [s for s in succ if s not in route]
            if not cands:
                break
            nxt = random.choice(cands)
            route.append(nxt)
            cur = nxt
            # 到达无后继边（出口/边界）即可结束，车辆能正常离场
            if not self.engine.get_edge_successors(cur):
                break
        return route if len(route) >= 2 else None

    # ── 每步决策 ───────────────────────────────────────────

    def decide(self, step: int) -> dict:
        """到点则按强度随机补一批车。返回本批统计（供日志/状态）。"""
        if step < self._next_at:
            return {"decided": False}
        self._next_at = step + ARRIVE_INTERVAL
        entries = self._entry_edges()
        if not entries:
            return {"decided": False, "reason": "no_entries"}
        # 在网软上限：满则跳过（等离场后自然恢复）
        try:
            active = len(self.engine.get_vehicle_ids())
        except Exception:  # noqa: BLE001
            active = 0
        if active >= self.cap:
            self._skipped_cap += 1
            return {"decided": True, "skipped_cap": True, "active": active}

        # 期望辆数随机取整（≥0）
        expect = self.rate * (ARRIVE_INTERVAL / 10.0)
        k = int(expect)
        if random.random() < expect - k:
            k += 1
        added = 0
        for _ in range(k):
            route = self._random_route()
            if not route:
                continue
            self._seq += 1
            vid = f"flow_{self._seq}"
            try:
                self.engine.add_vehicle_route(vid, route,
                                              depart=float(step))
                added += 1
                self._spawned += 1
            except Exception:  # noqa: BLE001 单辆失败跳过（重复/拥堵等）
                continue
        return {"decided": True, "expected": round(expect, 2),
                "added": added, "active": active}

    def status(self) -> dict:
        return {"enabled": True, "scenario": self.scenario,
                "rate_per_10s": self.rate, "cap": self.cap,
                "spawned": self._spawned, "skipped_cap": self._skipped_cap}
