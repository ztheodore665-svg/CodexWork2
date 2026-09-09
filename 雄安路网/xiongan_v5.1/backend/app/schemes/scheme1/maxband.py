"""MAXBAND 绿波协调：走廊检测、偏移量计算、统一周期。"""

import math

COLLINEAR_TOL = 20.0       # 共线判定容差（m）
MIN_CORRIDOR_LENGTH = 200.0  # 走廊最小长度（m）
MIN_CORRIDOR_TLS = 2       # 走廊最少路口数
MAX_CORRIDORS = 10


class GreenWaveCoordinator:
    def __init__(self, ctx):
        self.ctx = ctx
        self.engine = ctx.engine
        self.corridors: list[dict] = []

    # ── 走廊自动检测 ────────────────────────────────────────

    def detect_corridors(self) -> list[dict]:
        positions: list[tuple[str, tuple[float, float]]] = []
        for tid in self.engine.get_tls_ids():
            pos = self.engine.get_tls_position(tid)
            if pos is not None:
                positions.append((tid, pos))

        east_west = sorted(positions, key=lambda p: p[1][0])   # 按 x
        north_south = sorted(positions, key=lambda p: p[1][1])  # 按 y

        ew = self._cluster(east_west, horizontal=True)
        ns = self._cluster(north_south, horizontal=False)
        candidates = sorted(ew + ns, key=lambda c: c["length_m"], reverse=True)

        used: set[str] = set()
        corridors: list[dict] = []
        for cand in candidates:
            if len(corridors) >= MAX_CORRIDORS:
                break
            ids = [n["tls_id"] for n in cand["nodes"]]
            if len(ids) < MIN_CORRIDOR_TLS or any(i in used for i in ids):
                continue
            used.update(ids)
            corridors.append(self._to_corridor(ids, cand["nodes"], cand["horizontal"]))
        self.corridors = corridors
        return corridors

    def _cluster(self, pts: list[tuple[str, tuple[float, float]]],
                 horizontal: bool) -> list[dict]:
        """按共线坐标聚类近似直线上的信号灯序列。"""
        groups: list[list[tuple[str, tuple[float, float]]]] = []
        for tid, (x, y) in pts:
            coord = y if horizontal else x
            placed = False
            for g in groups:
                anchor = g[0][1]
                ref = anchor[1] if horizontal else anchor[0]
                if abs(ref - coord) <= COLLINEAR_TOL:
                    g.append((tid, (x, y)))
                    placed = True
                    break
            if not placed:
                groups.append([(tid, (x, y))])
        out = []
        for g in groups:
            ids = [i for i, _ in g]
            coords = [pos for _, pos in g]
            if horizontal:
                xs = [p[0] for p in coords]
                length = max(xs) - min(xs)
            else:
                ys = [p[1] for p in coords]
                length = max(ys) - min(ys)
            if len(g) < MIN_CORRIDOR_TLS or length < MIN_CORRIDOR_LENGTH:
                continue
            out.append({"nodes": [{"tls_id": i, "pos": pos} for i, pos in g],
                        "length_m": length, "horizontal": horizontal})
        return out

    def _to_corridor(self, tls_ids: list[str], nodes: list[dict], horizontal: bool) -> dict:
        travel = {}
        for i in range(len(nodes) - 1):
            a = nodes[i]["tls_id"]
            b = nodes[i + 1]["tls_id"]
            dist = math.hypot(nodes[i + 1]["pos"][0] - nodes[i]["pos"][0],
                              nodes[i + 1]["pos"][1] - nodes[i]["pos"][1])
            speed = 11.1  # 默认约 40 km/h 自由流
            travel[(a, b)] = dist / speed if speed > 0 else 0.0
        return {
            "id": f"corridor_{len(self.corridors) + 1}",
            "name": f"corridor_{len(self.corridors) + 1}",
            "direction": "EW" if horizontal else "NS",
            "tls_ids": tls_ids,
            "travel": travel,
        }

    # ── 手动添加走廊 ────────────────────────────────────────

    def add_corridor(self, name, direction, tls_ids, positions=None,
                     travel=None) -> dict:
        positions = positions or {}
        travel = travel or {}
        nodes = [{"tls_id": tid, "pos": positions.get(tid, (0.0, 0.0))}
                 for tid in tls_ids]
        if not travel:
            for i in range(len(nodes) - 1):
                a, b = nodes[i]["tls_id"], nodes[i + 1]["tls_id"]
                dist = math.hypot(nodes[i + 1]["pos"][0] - nodes[i]["pos"][0],
                                  nodes[i + 1]["pos"][1] - nodes[i]["pos"][1])
                travel[(a, b)] = dist / 11.1
        corr = {"id": name, "name": name, "direction": direction,
                "tls_ids": list(tls_ids), "travel": travel}
        self.corridors.append(corr)
        return corr

    # ── 偏移计算 ────────────────────────────────────────────

    def compute_offsets(self, corridor: dict, cycle: int) -> list[dict]:
        offsets = []
        acc = 0.0
        ids = corridor["tls_ids"]
        travel = corridor.get("travel", {})
        for i, tid in enumerate(ids):
            offsets.append({"tls_id": tid, "offset": int(acc) % cycle})
            if i < len(ids) - 1:
                acc += travel.get((tid, ids[i + 1]), 0.0)
        return offsets

    def apply_offsets(self, timings: dict, cycle: int) -> None:
        for corridor in self.corridors:
            for off in self.compute_offsets(corridor, cycle):
                plan = timings.get(off["tls_id"])
                if plan is not None:
                    plan["offset"] = off["offset"]

    def unified_cycle(self, periods: list[int]) -> int:
        if not periods:
            return 60
        m = max(periods)
        return int(math.ceil(m / 5.0) * 5)
