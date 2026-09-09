"""状态编码器：为每个交叉口构建标准化观测向量（含邻居关系）。

双观测模式：
- legacy（默认，雄安路网版）：观测含"绿灯阶段 one-hot + 邻居相位"，维度与路网
  的相位阶段数/邻居数绑定，权重按路网特定；
- agnostic（路网无关版）：固定 18 维，特征全部按路口自身归一化，
  策略可跨任意符合 SUMO 标准的路网直接使用（多路网域随机化联合训练）。

动作空间语义：actuated"保持/推进"。推进由控制器触发变灯倒计时 → 过渡相位
（从程序定义动态解析，兼容任意符合 SUMO 标准的路网）→ 下一绿灯阶段。
"""

from app.core.datacollector import edge_from_lane

HALT_SPEED = 0.1
FREE_SPEED_NORM = 13.89   # 50 km/h
DENSITY_NORM = 0.3
MAX_GREEN_NORM = 45.0     # 最长绿灯（与 min/max_green 上限一致，固定尺度）
STAGES_NORM = 12.0        # 阶段数归一化分母（固定，不随路网变化）
AGNOSTIC_DIM = 18


class StateEncoder:
    def __init__(self, ctx, max_edges: int = 4, max_neighbors: int = 4,
                 obs_mode: str = "legacy"):
        self.ctx = ctx
        self.engine = ctx.engine
        self.max_edges = max_edges
        self.max_neighbors = max_neighbors
        self.obs_mode = obs_mode
        self.tls_ids = self.engine.get_tls_ids()
        # 绿灯阶段(stage)结构：{tid: [原始相位索引, ...]}，动作执行依赖
        self.green_phases = self._build_green_phases()
        # 每个路口的 stage 数；共享 Actor 输出维度 = 全局最大值（仅 legacy 用）
        self.max_stages = max((len(v) for v in self.green_phases.values()), default=1)
        # 绿灯相位 -> 过渡相位（黄灯）映射：推进时的清空过渡相位，程序动态解析
        self.transition_phases = self._build_transition_phases()
        # 原始相位索引 -> stage 下标（过渡相位归入其前一个绿灯阶段）
        self.stage_of = self._build_stage_of()
        # 原始相位总数（信息用途：meta.json 等）
        self.max_phases = max(
            (self.engine.get_tls_state(t)["num_phases"] for t in self.tls_ids),
            default=1)
        self._approach_edges = self._build_approach_edges()
        self._neighbors = self._build_neighbors()
        # agnostic 模式：相位->服务边映射（压力/饿死特征）+ 每步特征缓存
        self._serving: dict = {}
        self._feat: dict[str, dict] = {}
        if self.obs_mode == "agnostic":
            try:
                from app.core.phase_geo import build_phase_serving
                self._serving = build_phase_serving(self.engine)
            except Exception:  # noqa: BLE001 失败则压力特征恒 0
                self._serving = {}

    # ── 结构构建 ────────────────────────────────────────────

    def _build_green_phases(self) -> dict[str, list[int]]:
        """活动程序中所有含 G/g 的相位索引（= 可服务的绿灯阶段）。"""
        import traci
        mapping: dict[str, list[int]] = {}
        for tid in self.tls_ids:
            greens: list[int] = []
            try:
                logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tid)
                active = traci.trafficlight.getProgram(tid)
                logic = next((lg for lg in logics if lg.programID == active), logics[0])
                greens = [i for i, ph in enumerate(logic.phases)
                          if "G" in ph.state or "g" in ph.state]
            except Exception:  # noqa: BLE001 失败回退：相位 0 视为唯一绿灯
                greens = [0]
            mapping[tid] = greens or [0]
        return mapping

    def _build_transition_phases(self) -> dict[str, dict[int, int]]:
        """每个绿灯相位 -> 其后的过渡相位（含 y/Y 的黄灯清空相位）。

        不假设"下一相位必是黄灯"：从活动程序按状态字动态查找，
        兼容任意符合 SUMO 标准的信号程序（含迟启绿等混合状态）。
        """
        import traci
        out: dict[str, dict[int, int]] = {}
        for tid in self.tls_ids:
            m: dict[int, int] = {}
            try:
                logics = traci.trafficlight.getCompleteRedYellowGreenDefinition(tid)
                active = traci.trafficlight.getProgram(tid)
                logic = next((lg for lg in logics if lg.programID == active), logics[0])
                phases = logic.phases
                for g in self.green_phases.get(tid, []):
                    t = g + 1
                    while (t < len(phases) and "y" not in phases[t].state
                           and "Y" not in phases[t].state):
                        t += 1
                    m[g] = t if t < len(phases) else g + 1
            except Exception:  # noqa: BLE001 失败回退：下一相位
                pass
            out[tid] = m
        return out

    def _build_stage_of(self) -> dict[str, dict[int, int]]:
        """原始相位 -> stage 下标。过渡相位(黄/红)归入前一个绿灯阶段。"""
        out: dict[str, dict[int, int]] = {}
        for tid, greens in self.green_phases.items():
            n = self.engine.get_tls_state(tid)["num_phases"]
            m: dict[int, int] = {}
            stage = 0
            for p in range(n):
                while stage < len(greens) - 1 and greens[stage + 1] <= p:
                    stage += 1
                m[p] = stage
            out[tid] = m
        return out

    def _build_approach_edges(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for tid in self.tls_ids:
            edges: list[str] = []
            try:
                conns = self.engine.get_tls_connections(tid)
            except Exception:  # noqa: BLE001
                mapping[tid] = []
                continue
            seen: set[str] = set()
            for lanes in conns.values():
                for lane in lanes:
                    e = edge_from_lane(lane)
                    if e not in seen:
                        seen.add(e)
                        edges.append(e)
            mapping[tid] = edges[:self.max_edges]
        return mapping

    def _build_neighbors(self) -> dict[str, list[str]]:
        """邻居：共享某条进口边的其他信号灯（密网近似），上限 max_neighbors。"""
        neigh: dict[str, list[str]] = {t: [] for t in self.tls_ids}
        for a in self.tls_ids:
            edges_a = set(self._approach_edges[a])
            for b in self.tls_ids:
                if a == b or len(neigh[a]) >= self.max_neighbors:
                    continue
                if edges_a & set(self._approach_edges[b]):
                    neigh[a].append(b)
        return neigh

    # ── 观测构建 ────────────────────────────────────────────

    def dim(self) -> int:
        if self.obs_mode == "agnostic":
            return AGNOSTIC_DIM
        return (self.max_stages + 1 + 3 * self.max_edges
                + self.max_neighbors + 2)

    def encode(self, tls_id: str, global_speed: float, global_density: float) -> list[float]:
        if self.obs_mode == "agnostic":
            return self._encode_agnostic(tls_id, global_speed, global_density)
        return self._encode_legacy(tls_id, global_speed, global_density)

    def _encode_legacy(self, tls_id: str, global_speed: float,
                       global_density: float) -> list[float]:
        st = self.engine.get_tls_state(tls_id)
        phase = st["phase_index"]
        obs: list[float] = []
        # 1) 当前绿灯阶段 one-hot（过渡相位归入前一个绿灯阶段）
        stage = self.stage_of.get(tls_id, {}).get(phase, 0)
        one_hot = [0.0] * self.max_stages
        if stage < self.max_stages:
            one_hot[stage] = 1.0
        obs += one_hot
        # 2) 相位真实已持续时间（elapsed，归一化 /60）
        obs.append(min(1.0, st["elapsed"] / 60.0))
        # 3) 各 approach edge：排队/车辆数/速度
        edges = self._approach_edges.get(tls_id, [])
        for i in range(self.max_edges):
            if i < len(edges):
                stats = self.engine.get_edge_stats(edges[i])
                queue = stats["vehicle_count"] if stats["mean_speed"] < HALT_SPEED else 0
                obs += [min(1.0, queue / 50.0),
                        min(1.0, stats["vehicle_count"] / 100.0),
                        min(1.0, stats["mean_speed"] / FREE_SPEED_NORM)]
            else:
                obs += [0.0, 0.0, 0.0]
        # 4) 邻居相位（归一化 stage 下标）
        for nb in self._neighbors.get(tls_id, []):
            nb_phase = self.engine.get_tls_state(nb)["phase_index"]
            nb_stage = self.stage_of.get(nb, {}).get(nb_phase, 0)
            obs.append(min(1.0, nb_stage / max(1, self.max_stages)))
        while len(obs) < self.dim() - 2:
            obs.append(0.0)
        # 5) 全局速度 / 全局密度
        obs.append(min(1.0, global_speed / FREE_SPEED_NORM))
        obs.append(min(1.0, global_density / DENSITY_NORM))
        return obs

    def _encode_agnostic(self, tls_id: str, global_speed: float,
                         global_density: float) -> list[float]:
        """路网无关观测：固定 18 维，特征全部按路口自身归一化。"""
        st = self.engine.get_tls_state(tls_id)
        phase = st["phase_index"]
        n_stages = len(self.green_phases.get(tls_id, [1]))
        cur_stage = self.stage_of.get(tls_id, {}).get(phase, 0)
        cur_stage = min(cur_stage, max(0, n_stages - 1))

        # 进口道排队/速度（按排队降序，排序保证跨路网语义一致："最堵的方向"）
        items: list[tuple[str, float, float]] = []
        for e in self._approach_edges.get(tls_id, []):
            s = self.engine.get_edge_stats(e)
            q = (s["vehicle_count"] if s["mean_speed"] < HALT_SPEED else 0) / 50.0
            items.append((e, min(1.0, q), min(1.0, s["mean_speed"] / FREE_SPEED_NORM)))
        items.sort(key=lambda x: -x[1])

        # 服务/未服务排队与压力（相位->服务边映射）
        served_src: set[str] = set()
        pressure = 0.0
        serving = self._serving.get(tls_id, {})
        greens = self.green_phases.get(tls_id, [0])
        raw = greens[cur_stage % max(1, n_stages)]
        for src, dst in serving.get(raw, []):
            served_src.add(src)
            q_up = self.engine.get_edge_queue(src) / 50.0
            q_dn = self.engine.get_edge_queue(dst) / 50.0
            pressure += q_up - q_dn
        served_qs = [q for e, q, _ in items if e in served_src]
        unserved_qs = [q for e, q, _ in items if e not in served_src]
        served_q = sum(served_qs) / len(served_qs) if served_qs else 0.0
        starve = sum(unserved_qs) / len(unserved_qs) if unserved_qs else 0.0
        total_queue = sum(q for _, q, _ in items)
        speeds = [sp for _, _, sp in items]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

        # 邻居相对相位对齐度（0~1）
        my_rel = cur_stage / max(1, n_stages)
        aligns = []
        for nbid in self._neighbors.get(tls_id, []):
            nb_st = self.engine.get_tls_state(nbid)
            nb_stage = self.stage_of.get(nbid, {}).get(nb_st["phase_index"], 0)
            nb_rel = nb_stage / max(1, len(self.green_phases.get(nbid, [1])))
            aligns.append(1.0 - abs(my_rel - nb_rel))
        align = sum(aligns) / len(aligns) if aligns else 0.0

        obs: list[float] = [
            min(1.0, cur_stage / max(1, n_stages)),      # 0 相对阶段位置
            min(1.0, st["elapsed"] / MAX_GREEN_NORM),    # 1 已持续时长
            max(0.0, min(1.0, (pressure + 2.0) / 4.0)),  # 2 服务压力(归一化)
            starve,                                       # 3 未服务排队
        ]
        for i in range(4):                               # 4..11 top4 (排队,速度)
            if i < len(items):
                q, sp = items[i][1], items[i][2]
            else:
                q, sp = 0.0, 0.0
            obs += [q, sp]
        obs += [
            min(1.0, total_queue / 200.0),               # 12 总排队
            min(1.0, avg_speed / FREE_SPEED_NORM),       # 13 平均速度
            min(1.0, n_stages / STAGES_NORM),            # 14 阶段数(固定尺度)
            align,                                        # 15 邻居对齐
            min(1.0, global_speed / FREE_SPEED_NORM),    # 16 全局速度
            min(1.0, global_density / DENSITY_NORM),     # 17 全局密度
        ]
        assert len(obs) == AGNOSTIC_DIM, f"agnostic obs 维度错误: {len(obs)}"
        self._feat[tls_id] = {"pressure": pressure, "starve": starve,
                              "served_q": served_q, "total_queue": total_queue}
        return obs

    def global_state(self) -> list[float]:
        """所有 agent 观测拼接（中心化 Critic 输入）。"""
        gs, gd = self._global_stats()
        out: list[float] = []
        for tid in self.tls_ids:
            out += self.encode(tid, gs, gd)
        return out

    def _global_stats(self) -> tuple[float, float]:
        total_speed, n = 0.0, 0
        total_len = 0.0
        for eid in self.engine.get_edge_ids():
            total_len += self.engine.get_edge_length(eid)
        for vid in self.engine.get_vehicle_ids():
            try:
                total_speed += self.engine.get_vehicle_state(vid)["speed"]
                n += 1
            except Exception:  # noqa: BLE001
                continue
        gs = total_speed / n if n else 0.0
        vcount = len(self.engine.get_vehicle_ids())
        gd = vcount / total_len if total_len > 0 else 0.0
        return gs, gd
