"""奖励计算器：max-pressure 压力改进奖励 + Δ排队 + 未服务排队惩罚 + 切换惩罚。

设计要点（针对"奖励过平 → 永不切换最优 → 单相位坍缩"问题）：
1. 压力奖励改为**改进式**：r = w_p·(pressure(所选阶段) − pressure(当前阶段))。
   "保持"时该项恒为 0，只有"切换到压力更高的阶段"才产生正奖励——
   直接破除"永不切换"这一平坦最优解；
2. Δ排队奖励：两次决策间 approach 排队减少 → 正奖励（即时反馈服务是否真正清空排队）；
3. 未服务排队惩罚（starve）：所选阶段未服务的进口道排队均值越高惩罚越大——
   直接打击"饿死其他方向"的行为，是抗死锁的关键项；
4. 切换惩罚仅对"频繁切换"生效（近 switch_window 次决策内切换 ≥ 阈值），
   防止阶段间来回抖动；
5. 协调项默认关闭（原实现归一化不一致，属于噪声），已修正后可自行开启。
"""

DEFAULT_WEIGHTS = {
    "pressure": 1.0,          # 压力改进奖励系数（核心信号）
    "queue_delta": 1.0,       # Δ排队系数
    "starve": 1.0,            # 未服务进口道排队惩罚系数（抗饿死关键）
    "hold_waste": 2.0,        # 空放持有惩罚：排空后持有越久惩罚越大（创造"尽早切换"梯度）
    "coord": 0.5,             # 协调项系数（邻居阶段对齐，促进绿波；修正后开启）
    "switch_penalty": 0.0,    # 已由 hold_waste 取代（最短绿灯约束切换频率）
    "switch_freq_threshold": 8,
    "switch_window": 60,
    "queue_norm": 50.0,
}


class RewardCalculator:
    def __init__(self, ctx, weights: dict | None = None):
        self.ctx = ctx
        self.w = {**DEFAULT_WEIGHTS, **(weights or {})}

    def configure(self, obs_off: int, max_edges: int, max_stages: int,
                  obs_mode: str = "legacy") -> None:
        """记录观测向量布局（观测起点 = max_stages+1，approach 边数，stage 数）。

        obs_mode=agnostic：观测为路网无关固定 18 维，排队特征在 obs[12]（总排队）、
        邻居对齐在 obs[15]。
        """
        self.w["_obs_off"] = obs_off
        self.w["_max_edges"] = max_edges
        self.w["_max_stages"] = max_stages
        self.w["_obs_mode"] = obs_mode

    def compute(self, tls_id: str, obs: list[float], global_metrics: dict,
                switched: bool, switch_freq: int,
                prev_queue: float | None = None, pressure: float = 0.0,
                pressure_cur: float = 0.0, starve: float = 0.0,
                served_q: float = 0.0, hold: float = 0.0) -> float:
        """综合奖励：
        R = w_p·(pressure − pressure_cur)
          + w_qd·(prev_queue − 当前排队)
          − w_starve·starve
          − w_hold_waste·(hold/max_green)·(1−served_q)
          + 协调项 − 切换惩罚。

        pressure：所选阶段的服务压力；pressure_cur：当前阶段的服务压力（改进式差分）；
        starve：所选阶段未服务进口道的排队均值（归一化，由控制器按服务映射计算）；
        served_q：所选阶段已服务进口道的排队均值；hold：当前阶段已持有秒数。
        hold_waste：已服务方向排空(served_q→0)后持有越久惩罚越大——
        在轻车流下所有方向都空时，该惩罚随持有时间增长，迫使策略尽早切换，
        避免"慢循环"导致的绿灯浪费。
        """
        r_pressure = self.w["pressure"] * (pressure - pressure_cur)
        r_qd = 0.0
        if prev_queue is not None:
            q_curr = self.queue_from_obs(obs)
            r_qd = self.w["queue_delta"] * (prev_queue - q_curr)
        r_starve = -self.w["starve"] * starve
        max_green = max(1.0, float(self.w.get("max_green", 45.0)))
        r_hold = -self.w["hold_waste"] * (hold / max_green) * (1.0 - served_q)
        r_coord = self.w["coord"] * self._coordination(obs)

        penalty = 0.0
        if switched:
            penalty = self.w["switch_penalty"]
            if switch_freq >= self.w["switch_freq_threshold"]:
                penalty *= 2.0

        return r_pressure + r_qd + r_starve + r_hold + r_coord - penalty

    def queue_from_obs(self, obs: list[float]) -> float:
        """从观测提取排队（Δ排队奖励用）。

        agnostic 布局：obs[12] 为路口总排队（归一化）；
        legacy 布局：approach 排队均值。
        """
        if self.w.get("_obs_mode") == "agnostic":
            return obs[12] if len(obs) > 12 else 0.0
        q, _ = self._local_from_obs(obs)
        return q

    def periodic_reward(self, cycle_stats: dict) -> float:
        avg_queue = cycle_stats.get("avg_queue", 0.0) / 50.0
        return -self.w["queue_delta"] * avg_queue

    def _local_from_obs(self, obs: list[float]) -> tuple[float, float]:
        """从观测向量抽取 approach edge 排队均值与速度均值。"""
        n = self.w.get("_max_edges", 4)
        start = self.w.get("_obs_off", 0)
        queues, speeds = [], []
        for i in range(n):
            idx = start + i * 3
            if idx + 2 < len(obs):
                queues.append(obs[idx])
                speeds.append(obs[idx + 2])
        return (sum(queues) / len(queues) if queues else 0.0,
                sum(speeds) / len(speeds) if speeds else 0.0)

    def _coordination(self, obs: list[float]) -> float:
        """邻居相位一致性：邻居 stage 越接近当前 stage，奖励越高（观测布局修正版）。

        agnostic 布局：邻居对齐特征已在 obs[15]（编码器计算）。
        """
        if self.w.get("_obs_mode") == "agnostic":
            return obs[15] if len(obs) > 15 else 0.0
        n = self.w.get("_max_edges", 4)
        base = self.w.get("_obs_off", 0)
        max_stages = max(1, self.w.get("_max_stages", 1))
        my_stage = next((i for i, v in enumerate(obs[:max_stages]) if v > 0.5), 0)
        my_norm = my_stage / max_stages
        nb_start = base + 3 * n
        nb = obs[nb_start:-2]
        if not nb:
            return 0.0
        return sum(1.0 - abs(v - my_norm) for v in nb) / len(nb)
