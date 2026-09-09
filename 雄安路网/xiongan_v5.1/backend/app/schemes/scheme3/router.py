"""路线规划器：Dijkstra 最短路径 + K-短路 + 负载均衡。"""

import heapq
from collections import defaultdict

DEFAULT_PARAMS = {"k": 3, "load_balance_threshold": 3, "max_route_len": 100}


class RoutePlanner:
    def __init__(self, ctx, network, weights, params: dict | None = None):
        self.ctx = ctx
        self.engine = ctx.engine
        self.network = network
        self.weights = weights
        self.p = {**DEFAULT_PARAMS, **(params or {})}
        self._assignments: dict[tuple[str, str], dict[tuple, int]] = defaultdict(
            lambda: defaultdict(int))
        self._plan_count = 0
        self._reroute_count = 0
        self._fail_count = 0
        self._balance_count = 0

    # ── 最短路径（Dijkstra） ────────────────────────────────

    def shortest_path(self, from_edge: str, to_edge: str,
                      avoid: set[str] | None = None) -> list[str] | None:
        avoid = avoid or set()
        if self.network.is_restricted(from_edge) or self.network.is_restricted(to_edge):
            return None
        dist = {from_edge: 0.0}
        prev: dict[str, str] = {}
        pq = [(0.0, from_edge)]
        visited: set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == to_edge:
                break
            for v in self.engine.get_edge_successors(u):
                if v in avoid or self.network.is_restricted(v):
                    continue
                nd = d + self.weights.weight(v)
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if to_edge not in dist:
            return None
        path: list[str] = []
        u = to_edge
        while True:
            path.append(u)
            if u == from_edge:
                break
            if u not in prev:
                return None
            u = prev[u]
        path.reverse()
        if len(path) > self.p["max_route_len"]:
            return None
        return path

    # ── K-短路（简化 Yen） ─────────────────────────────────

    def k_shortest(self, from_edge: str, to_edge: str, k: int | None = None) -> list[list[str]]:
        """简化 Yen：对最短路径上每条 spur 边，阻塞其下一跳后重算，去重取前 K 条。"""
        k = k or self.p["k"]
        first = self.shortest_path(from_edge, to_edge)
        if first is None:
            return []
        results = [first]
        seen = {tuple(first)}
        for i in range(len(first) - 1):
            spur = first[i]
            root = first[:i + 1]
            avoid = set(root[:-1]) | {first[i + 1]}  # 阻止回走 + 删除 spur 边
            spur_path = self.shortest_path(spur, to_edge, avoid=avoid)
            if spur_path is None:
                continue
            total = root[:-1] + spur_path
            key = tuple(total)
            if key in seen or not self.network.verify_route(total):
                continue
            seen.add(key)
            results.append(total)
        results.sort(key=self._route_weight)
        return results[:k]

    def _route_weight(self, route: list[str]) -> float:
        return sum(self.weights.weight(e) for e in route)

    # ── 负载均衡 ────────────────────────────────────────────

    def plan_batch(self, requests: list[dict]) -> list[dict]:
        results = []
        for req in requests:
            vid = req["vehicle_id"]
            frm = req["from"]
            to = req["to"]
            route = self._choose_route(frm, to)
            if route is None:
                self._fail_count += 1
                results.append({"vehicle_id": vid, "ok": False,
                                "route": None, "message": "无可行路线"})
                continue
            ok = self._apply_route(vid, route)
            results.append({"vehicle_id": vid, "ok": ok,
                            "route": route, "method": "load_balanced" if self._last_balanced else "shortest"})
            if ok:
                self._assignments[(frm, to)][tuple(route)] += 1
        self._plan_count += len(requests)
        return results

    def _choose_route(self, frm: str, to: str) -> list[str] | None:
        key = (frm, to)
        assigned = self._assignments[key]
        total = sum(assigned.values())
        if total < self.p["load_balance_threshold"] or not assigned:
            self._last_balanced = False
            return self.shortest_path(frm, to)
        # 选已分配最少的候选路线
        routes = self.k_shortest(frm, to, k=self.p["k"])
        if not routes:
            return None
        best = min(routes, key=lambda r: assigned.get(tuple(r), 0))
        if assigned.get(tuple(best), 0) < total:
            self._last_balanced = True
            self._balance_count += 1
        else:
            self._last_balanced = False
        return best

    def _apply_route(self, veh_id: str, route: list[str]) -> bool:
        if self.network.verify_route(route):
            try:
                self.engine.set_vehicle_route(veh_id, route)
                return True
            except Exception:  # noqa: BLE001
                self._fail_count += 1
                return False
        try:
            self.engine.reroute_vehicle(veh_id)
            self._fail_count += 1
            return False
        except Exception:  # noqa: BLE001
            return False

    # ── 路线推荐 ────────────────────────────────────────────

    def recommend(self, from_edge: str, to_edge: str,
                  k: int | None = None) -> list[dict]:
        k = k or self.p["k"]
        out = []
        for route in self.k_shortest(from_edge, to_edge, k):
            total = self._route_weight(route)
            length = sum(self.engine.get_edge_length(e) for e in route)
            est = sum(self.engine.get_edge_length(e) / max(0.1, self.engine.get_edge_speed_limit(e))
                      for e in route)
            out.append({
                "route": route, "total_weight": round(total, 2),
                "length": round(length, 1), "est_time": round(est, 1),
                "edge_count": len(route),
                "restricted": any(self.network.is_restricted(e) for e in route),
                "method": "k_shortest",
            })
        return out

    def status(self) -> dict:
        return {"plans": self._plan_count, "reroutes": self._reroute_count,
                "failures": self._fail_count, "load_balanced": self._balance_count,
                "active_assignments": sum(sum(d.values()) for d in self._assignments.values()),
                "od_pairs": len(self._assignments), "params": self.p}
