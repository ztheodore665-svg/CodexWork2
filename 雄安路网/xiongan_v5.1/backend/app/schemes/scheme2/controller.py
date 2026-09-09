"""方案二控制器：MAPPO 实时控制 + STGCN 预测，SCOOT 降级。"""

import math
import random
from collections import deque

from app.core.datacollector import edge_from_lane
from app.core.phase_geo import build_phase_serving
from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.schemes.base import BaseScheme
from app.schemes.registry import register_scheme
from app.schemes.scheme2.encoder import StateEncoder
from app.schemes.scheme2.mappo import MAPPOAgent
from app.schemes.scheme2.recorder import DataRecorder
from app.schemes.scheme2.reward import RewardCalculator
from app.schemes.scheme2.scoot import SCOOTController
from app.schemes.scheme2.stgcn import TrafficPredictor, torch_available

DECISION_INTERVAL = 5
PREDICT_INTERVAL = 10
RECORD_EDGE_INTERVAL = 5

# 动作空间：actuated 式"保持/推进"（真实信号机语义）
#   KEEP     = 保持当前绿灯阶段（延长绿灯）
#   ADVANCE  = 推进到下一绿灯阶段（触发变灯倒计时 → 黄灯→红灯 → 下一绿灯）
# 阶段轮转顺序由 SUMO 程序固定，任何方向都不会被永久饿死。
ACTION_KEEP = 0
ACTION_ADVANCE = 1
N_ACTIONS = 2


@register_scheme
class Scheme2Controller(BaseScheme):
    """方案二：实时观测自适应（MAPPO + STGCN，SCOOT 降级）。

    动作语义：MAPPO 动作 = 该路口"绿灯阶段(stage)"下标（只选绿灯相位，
    黄灯/全红过渡由 SUMO 程序自动处理），彻底避免"选到黄灯相位并 hold"导致的死锁。
    """

    name = "scheme_2"

    # 标准化声明（MCP-like）
    algorithm_spec = AlgorithmSpec(
        kind="signal",
        description="方案二：实时观测自适应（MAPPO 强化学习 + STGCN 预测，SCOOT 降级）",
        params=[
            ParamSpec("mode", "控制模式", type="enum", default="auto",
                      enum=["auto", "mappo", "scoot"],
                      desc="auto=按可用性自动选择；mappo=强化学习；scoot=规则自适应"),
            ParamSpec("min_green", "最短绿灯", type="number", default=5.0,
                      minimum=0, maximum=20, unit="s",
                      desc="绿灯阶段最小保持时长"),
            ParamSpec("max_green", "最长绿灯", type="number", default=45.0,
                      minimum=15, maximum=90, unit="s",
                      desc="绿灯阶段最大保持时长（超时强制推进）"),
            ParamSpec("switch_clearance", "变灯倒计时", type="number", default=12.0,
                      minimum=5, maximum=20, unit="s",
                      desc="变灯前的黄灯/全红倒计时"),
        ],
        observables=["vehicle_count", "avg_waiting", "avg_queue",
                     "tls_queues", "tls_phases", "congestion"],
        metrics=["mode", "explore_epsilon", "decision_count",
                 "mappo_actions", "scoot_actions", "downgrades"],
        capabilities=["switch_mode", "train_step", "save_model",
                      "load_model", "get_predictions"],
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        torch_ok = ctx.config.get("torch_available")
        self.torch_ok = torch_available() if torch_ok is None else bool(torch_ok)
        self.encoder = StateEncoder(ctx, obs_mode=ctx.config.get("obs_mode", "legacy"))
        self.reward = RewardCalculator(ctx)
        self.reward.configure(obs_off=self.encoder.max_stages + 1,
                              max_edges=self.encoder.max_edges,
                              max_stages=self.encoder.max_stages,
                              obs_mode=self.encoder.obs_mode)
        self.predictor = TrafficPredictor(
            ctx, mode=ctx.config.get("stgcn_mode"))
        # 支持多场景训练共享同一个 agent（config 传入 mappo_agent）
        self.mappo = ctx.config.get("mappo_agent") or MAPPOAgent(
            obs_dim=self.encoder.dim(),
            n_actions=N_ACTIONS,
            mode="train" if self.torch_ok else "heuristic",
            torch_ok=self.torch_ok,
            global_dim=self.encoder.dim() * max(1, len(self.encoder.tls_ids)))
        self.scoot = SCOOTController(ctx)
        # 奖励所需的绿灯上限（空放持有惩罚用）
        self.reward.w["max_green"] = self.mappo.max_green
        self.mode = self._resolve_mode(ctx.config.get("mode"))
        self.explore_epsilon = float(ctx.config.get("explore_epsilon", 0.0))
        self._explore_min = float(ctx.config.get("explore_min", 0.02))
        self._explore_decay = float(ctx.config.get("explore_decay", 0.9995))
        self.recorder: DataRecorder | None = None
        self.phase_serving: dict | None = None
        self._step = 0
        self._decision_count = 0
        self._mappo_actions = 0
        self._scoot_actions = 0
        self._downgrades = 0
        # Δ排队奖励：逐路口记录上一次决策的 approach 排队均值（归一化）
        self._prev_queues: dict[str, float] = {}
        # 切换频率：逐路口滑动窗口（近 switch_window 次决策内的切换次数）
        self._switch_history: dict[str, deque] = {}
        # 推理期动作轨迹：(tid, action) 列表，供验证脚本检查贪心动作分布是否非退化
        self._action_log: list[tuple[str, int]] = []
        # 绿灯保持计时（秒）：最短绿灯内强制保持，最长绿灯强制推进
        self._hold_time: dict[str, float] = {}
        # 变灯倒计时（真实信号机语义：决定切换后当前绿灯再保持 clearance 秒，
        # 然后黄灯→红灯→目标绿灯；不允许绿灯瞬间变红）
        self._clearance = float(ctx.config.get("switch_clearance", 12.0))
        # 变灯状态机：tid -> {"phase": "countdown"|"transitioning",
        #                     "target": 目标阶段, "from_stage": 原阶段, "ready_at": 倒计时截止}
        self._pending_switch: dict[str, dict] = {}

    # ── 模式解析 ────────────────────────────────────────────

    def _resolve_mode(self, mode: str | None) -> str:
        mode = mode or "auto"
        if mode == "fixed":
            # 纯固定配时基线：控制器不干预信号灯，由 SUMO 静态程序自动轮转
            return "fixed"
        if mode == "auto":
            # 默认 SCOOT（本地自适应为主）；仅显式提供训练好的 MAPPO 权重才启用 MAPPO
            if self.torch_ok:
                weights = self.ctx.config.get("mappo_weights")
                if weights and self.mappo.load(weights, critic_required=False):
                    self.mappo.mode = "infer"   # 部署：加载后切贪心推理
                    return "mappo"
            return "scoot"
        if mode == "mappo":
            if not self.torch_ok:
                self._downgrades += 1
                return "scoot"
            return "mappo"
        return "scoot"

    # ── 生命周期 ────────────────────────────────────────────

    def init(self) -> None:
        record_dir = self.ctx.config.get("record_dir")
        if record_dir:
            self.recorder = DataRecorder(record_dir, encoder=self.encoder,
                                         edge_interval=RECORD_EDGE_INTERVAL)
            self.recorder.flush_meta(n_actions=N_ACTIONS,
                                     note=f"mode={self.mode}")
        stgcn_weights = self.ctx.config.get("stgcn_weights")
        if stgcn_weights:
            self.predictor.load_model(stgcn_weights)
        try:
            self.phase_serving = build_phase_serving(self.ctx.engine)
        except Exception:  # noqa: BLE001
            self.phase_serving = {}
        self.ctx.push_event("scheme2_init",
                            f"方案二启动，模式={self.mode}，torch={self.torch_ok}，"
                            f"录制={'on' if self.recorder else 'off'}")

    def on_step(self) -> None:
        self._step += 1
        if self.recorder is not None:
            self.recorder.record_edge_features(self.ctx.engine, self._step)
        if self._step % PREDICT_INTERVAL == 0:
            self._update_predictor()
        if self._step % DECISION_INTERVAL == 0:
            self._decide()
        if (self.mode == "mappo" and self.torch_ok
                and self.mappo.mode == "train"      # 仅训练模式自动更新，评估(infer)不污染模型
                and self._step % self.mappo.hp["update_interval"] == 0):
            self.mappo.update()

    def cleanup(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
        if self.mode == "mappo" and self.torch_ok and self._mappo_actions:
            self.mappo.save(self.ctx.config.get("save_dir", "models/weights/checkpoint"))

    # ── 决策 ────────────────────────────────────────────────

    def _decide(self) -> None:
        if self.mode == "fixed":
            self._decision_count += 1
            return
        gs, gd, global_metrics = self._global_features()
        # 一次计算全部路口观测，全局状态 = 观测拼接（避免重复 TraCI 往返）
        obs_list = [self.encoder.encode(tid, gs, gd) for tid in self.encoder.tls_ids]
        gobs = [o for obs in obs_list for o in obs]
        for tid, obs in zip(self.encoder.tls_ids, obs_list):
            if self.mode == "mappo" and self.torch_ok:
                n_stages = len(self.encoder.green_phases.get(tid, [0]))
                mask = [i < N_ACTIONS for i in range(N_ACTIONS)]
                greens = self.encoder.green_phases.get(tid, [0])
                cur = self.ctx.engine.get_tls_state(tid)["phase_index"]
                cur_stage = self.encoder.stage_of.get(tid, {}).get(cur, 0)
                now = self.ctx.engine.get_sim_time()
                pend = self._pending_switch.get(tid)

                # ── 变灯两阶段状态机（真实信号机语义：不允许绿灯瞬间变红） ──
                #   countdown:     已决定推进，当前绿灯再保持 clearance 秒
                #   transitioning: 倒计时结束，黄灯(3s)→红灯(1s) 由 SUMO 程序执行
                if pend is not None:
                    if pend.get("phase") == "countdown" and now < pend["ready_at"]:
                        # 倒计时中：保持当前绿灯（延长时长防止超时），不产生新决策/经验
                        self.ctx.engine.set_tls_phase(
                            tid, cur, duration=max(5.0, pend["ready_at"] - now + 5.0))
                        self._decision_count += 1
                        continue
                    if pend.get("phase") == "countdown":
                        # 倒计时结束 → 过渡相位（黄灯 3s→红灯 1s）由 SUMO 程序自动执行
                        raw_cur_fb = greens[cur_stage % max(1, n_stages)]
                        trans = self.encoder.transition_phases.get(tid, {}).get(
                            raw_cur_fb, raw_cur_fb + 1)
                        self.ctx.engine.set_tls_phase(tid, trans, duration=3.0)
                        pend["phase"] = "transitioning"
                        self._decision_count += 1
                        continue
                    # transitioning 完成（程序已进入下一绿灯）→ 切到目标绿灯（静默，无经验）
                    self._apply_phase(tid, int(pend["target"]))
                    self._pending_switch[tid] = None
                    self._hold_time[tid] = 0.0
                    self._decision_count += 1
                    continue

                # ── 正常决策：actuated"保持/推进" ──
                action, logp = self._select_action(obs, mask)
                hold = self._hold_time.get(tid, 0.0)
                raw_cur = greens[cur_stage % max(1, n_stages)]
                if action == ACTION_ADVANCE and hold >= self.mappo.min_green:
                    # 推进：触发变灯倒计时（当前绿灯再保持 clearance 秒），
                    # 倒计时结束才黄灯→红灯→下一绿灯阶段
                    nxt_stage = (cur_stage + 1) % max(1, n_stages)
                    self._pending_switch[tid] = {
                        "phase": "countdown", "target": nxt_stage,
                        "from_stage": cur_stage, "ready_at": now + self._clearance}
                    self.ctx.engine.set_tls_phase(tid, cur,
                                                  duration=self._clearance + 5.0)
                    self._hold_time[tid] = hold + DECISION_INTERVAL
                    raw_next = greens[nxt_stage % max(1, n_stages)]
                    value = self.mappo.value_of(gobs)
                    pressure = self._phase_pressure(tid, raw_next)
                    pressure_cur = self._phase_pressure(tid, raw_cur)
                    served_q, starve = self._local_queues(tid, obs, raw_cur)
                    reward = self.reward.compute(
                        tid, obs, global_metrics, True, self._switch_freq(tid, True),
                        prev_queue=self._prev_queues.get(tid),
                        pressure=pressure, pressure_cur=pressure_cur,
                        starve=starve, served_q=served_q, hold=hold)
                    self._prev_queues[tid] = self.reward.queue_from_obs(obs)
                    self.mappo.store(obs, gobs, action, reward, logp, value, False)
                    if self.recorder is not None:
                        self.recorder.record_experience(obs, gobs, action, reward, False)
                    if self.mappo.mode != "train":
                        self._action_log.append((tid, action))
                    self._mappo_actions += 1
                    self._decision_count += 1
                    continue
                if hold >= self.mappo.max_green:
                    # 最长绿灯兜底：系统强制推进（过渡相位→下一阶段），不记策略经验
                    self._pending_switch[tid] = {
                        "phase": "transitioning",
                        "target": (cur_stage + 1) % max(1, n_stages),
                        "from_stage": cur_stage}
                    trans = self.encoder.transition_phases.get(tid, {}).get(raw_cur, raw_cur + 1)
                    self.ctx.engine.set_tls_phase(tid, trans, duration=3.0)
                    self._hold_time[tid] = 0.0
                    self._decision_count += 1
                    continue
                # 保持当前阶段（含最短绿灯内的强制保持）
                if hold < self.mappo.min_green and action != ACTION_KEEP:
                    action = ACTION_KEEP
                    logp = 0.0
                self._hold_time[tid] = hold + DECISION_INTERVAL
                self._apply_phase(tid, cur_stage)
                value = self.mappo.value_of(gobs)
                pressure_cur = self._phase_pressure(tid, raw_cur)
                served_q, starve = self._local_queues(tid, obs, raw_cur)
                reward = self.reward.compute(
                    tid, obs, global_metrics, False, self._switch_freq(tid, False),
                    prev_queue=self._prev_queues.get(tid),
                    pressure=pressure_cur, pressure_cur=pressure_cur,
                    starve=starve, served_q=served_q, hold=hold)
                self._prev_queues[tid] = self.reward.queue_from_obs(obs)
                self.mappo.store(obs, gobs, action, reward, logp, value, False)
                if self.recorder is not None:
                    self.recorder.record_experience(obs, gobs, action, reward, False)
                if self.mappo.mode != "train":
                    # 推理(贪心)动作轨迹，供验证脚本统计动作分布
                    self._action_log.append((tid, action))
                self._mappo_actions += 1
            else:
                st = self.ctx.engine.get_tls_state(tid)
                action = self.scoot.decide(tid, st, self._demands(tid))
                self._apply_scoot(tid, action, st)
                self._scoot_actions += 1
        self._decay_exploration()
        self._decision_count += 1

    def _select_action(self, obs: list[float], mask: list) -> tuple[int, float]:
        """ε-贪心：训练时行为策略 = (1−ε)·π + ε·Uniform(合法动作)；推理贪心 argmax。

        返回 (动作, 行为策略 log 概率)——importance ratio 用行为策略密度。
        """
        eps = self.explore_epsilon
        if eps > 0 and random.random() < eps:
            valid = [i for i, m in enumerate(mask) if m]
            a = random.choice(valid)
        else:
            greedy = self.mappo.mode != "train"
            a, _ = self.mappo.act_logp(obs, greedy=greedy, mask=mask)
        logp = self._behavior_logp(obs, a, mask, eps)
        return a, logp

    def _behavior_logp(self, obs: list[float], action: int,
                       mask: list, eps: float) -> float:
        """行为策略对动作的 log 概率：ε=0 时为策略本身，ε>0 时为 (1−ε)π + ε·Uniform。"""
        import torch
        logits = self.mappo._actor(torch.tensor([obs], dtype=torch.float32))[0]
        bias = torch.tensor([0.0 if m else -1e9 for m in mask],
                            dtype=torch.float32)
        probs = torch.softmax(logits + bias, dim=0)
        pa = float(probs[action].item())
        if eps > 0:
            n_valid = max(1, sum(1 for m in mask if m))
            mix = (1 - eps) * pa + eps / n_valid
            return math.log(max(mix, 1e-12))
        return math.log(max(pa, 1e-12))

    def _decay_exploration(self) -> None:
        """探索率随决策轮数衰减（训练时可从 0.3 逐步降到 0.02）。"""
        self.explore_epsilon = max(
            self._explore_min,
            self.explore_epsilon * self._explore_decay)

    def _apply_phase(self, tid: str, action: int) -> None:
        try:
            # 动作 = 绿灯阶段下标 → 映射回原始相位；过渡相位由 SUMO 程序自动处理
            greens = self.encoder.green_phases.get(tid, [0])
            raw = greens[action % max(1, len(greens))]
            # 响应式绿灯时长：服务压力越大绿灯越久（15-45s）
            pressure = abs(self._phase_pressure(tid, raw))
            duration = max(15.0, min(45.0, 15.0 + pressure * 15.0))
            self.ctx.engine.set_tls_phase(tid, raw, duration=duration)
        except Exception:  # noqa: BLE001
            self._downgrades += 1

    def _phase_pressure(self, tid: str, phase_index: int) -> float:
        """max-pressure：服务该相位 link 的 (上游排队−下游排队) 之和。"""
        serving = (self.phase_serving or {}).get(tid, {})
        pressure = 0.0
        for src, dst in serving.get(phase_index, []):
            q_up = self.ctx.engine.get_edge_queue(src) / 50.0
            q_dn = self.ctx.engine.get_edge_queue(dst) / 50.0
            pressure += q_up - q_dn
        return pressure

    def _starve_penalty(self, tid: str, obs: list[float], phase_index: int) -> float:
        """所选阶段未服务进口道的排队均值（归一化）——饿死其他方向的即时惩罚。

        从观测向量直接取各 approach 的排队归一化值（布局：obs_off + i*3），
        仅统计该阶段未服务的进口道。
        """
        _, starve = self._served_unserved(tid, obs, phase_index)
        return starve

    def _local_queues(self, tid: str, obs: list[float], raw_cur: int) -> tuple[float, float]:
        """返回 (服务方向排队均值, 未服务方向排队均值)。

        agnostic 模式：编码器在观测构建时已计算（当前阶段），直接取缓存；
        legacy 模式：按观测布局从 obs 解析。
        """
        if self.encoder.obs_mode == "agnostic":
            feat = self.encoder._feat.get(tid, {})
            return feat.get("served_q", 0.0), feat.get("starve", 0.0)
        return self._served_unserved(tid, obs, raw_cur)

    def _served_unserved(self, tid: str, obs: list[float],
                         phase_index: int) -> tuple[float, float]:
        """返回 (服务方向排队均值, 未服务方向排队均值)（均为归一化）。"""
        serving = (self.phase_serving or {}).get(tid, {})
        served_src = {src for src, _ in serving.get(phase_index, [])}
        edges = self.encoder._approach_edges.get(tid, [])[:self.encoder.max_edges]
        if not edges:
            return 0.0, 0.0
        start = self.encoder.max_stages + 1
        served, unserved = [], []
        for i, e in enumerate(edges):
            q = obs[start + i * 3]
            (served if e in served_src else unserved).append(q)
        served_q = sum(served) / len(served) if served else 0.0
        unserved_q = sum(unserved) / len(unserved) if unserved else 0.0
        return served_q, unserved_q

    def _switch_freq(self, tid: str, switched: bool) -> int:
        """近 switch_window 次决策内的切换次数（真实切换频率，用于惩罚阈值）。"""
        window = int(self.reward.w.get("switch_window", 60))
        hist = self._switch_history.setdefault(tid, deque(maxlen=window))
        hist.append(1 if switched else 0)
        return sum(hist)

    def _apply_scoot(self, tid: str, action: str, st: dict) -> None:
        if action == "switch":
            nxt = (st["phase_index"] + 1) % max(1, st["num_phases"])
            greens = self.scoot.distribute_greens(self._demands(tid))
            dur = greens.get(st["phase_index"], 15.0) + self.scoot.p["yellow"] + self.scoot.p["all_red"]
            self.ctx.engine.set_tls_phase(tid, nxt, dur)
        elif action == "extend":
            self.ctx.engine.set_tls_phase(tid, st["phase_index"],
                                          st["phase_duration"] + 5.0)

    def _demands(self, tid: str) -> dict:
        """各相位归一化需求。对唯一车道先算一次占有率，避免跨相位重复 TraCI 调用。"""
        try:
            conns = self.ctx.engine.get_tls_connections(tid)
        except Exception:  # noqa: BLE001
            return {}
        unique = set()
        for lanes in conns.values():
            unique.update(lanes)
        occ = {l: self.ctx.engine.get_edge_stats(edge_from_lane(l))["occupancy"]
               for l in unique}
        demands: dict = {}
        for phase, lanes in conns.items():
            demands[phase] = max((occ[l] for l in lanes), default=0.0)
        return demands

    def _global_features(self) -> tuple[float, float, dict]:
        """单次车辆遍历同时计算全局速度/密度与全局指标（减少 TraCI 往返）。"""
        total_speed, n = 0.0, 0
        waits: list[float] = []
        queues = 0
        total_len = sum(self.ctx.engine.get_edge_length(e)
                        for e in self.ctx.engine.get_edge_ids())
        for vid in self.ctx.engine.get_vehicle_ids():
            try:
                st = self.ctx.engine.get_vehicle_state(vid)
            except Exception:  # noqa: BLE001
                continue
            total_speed += st["speed"]
            waits.append(st["waiting_time"])
            if st["speed"] < 0.1:
                queues += 1
            n += 1
        gs = total_speed / n if n else 0.0
        gd = n / total_len if total_len > 0 else 0.0
        metrics = {"avg_speed": gs,
                   "avg_delay": sum(waits) / n if n else 0.0,
                   "avg_queue": queues}
        return gs, gd, metrics

    # ── 预测更新 ────────────────────────────────────────────

    def _update_predictor(self) -> None:
        feats = {}
        for eid in self.ctx.engine.get_edge_ids():
            st = self.ctx.engine.get_edge_stats(eid)
            feats[eid] = {"flow": st["occupancy"] * 1800.0,
                          "speed": st["mean_speed"], "occupancy": st["occupancy"]}
        self.predictor.update(feats)

    # ── API 动作 ────────────────────────────────────────────

    def handle_action(self, action: str, params: dict) -> dict:
        # 标准化能力名 switch_mode → 内部 switch_to_*（mode 忽略大小写）
        if action == "switch_mode":
            mode = str(params.get("mode", "auto") or "auto").strip().lower()
            if mode == "mappo":
                return self.handle_action("switch_to_mappo", {})
            if mode == "scoot":
                return self.handle_action("switch_to_scoot", {})
            return self.handle_action("switch_to_auto", {})
        if action == "switch_to_mappo":
            if not self.torch_ok:
                return {"ok": False, "message": "无 PyTorch，无法切换 MAPPO"}
            self.mode = "mappo"
            return {"ok": True, "mode": self.mode}
        if action == "switch_to_scoot":
            self.mode = "scoot"
            return {"ok": True, "mode": self.mode}
        if action == "switch_to_auto":
            self.mode = self._resolve_mode("auto")
            return {"ok": True, "mode": self.mode}
        if action == "train_step":
            out = self.mappo.update()
            return {"ok": True, **out}
        if action == "save_model":
            path = params.get("path", "models/weights/checkpoint")
            ok = self.mappo.save(path)
            return {"ok": ok, "path": path}
        if action == "load_model":
            path = params.get("path", "models/weights/checkpoint")
            ok = self.mappo.load(path)
            self.mode = "mappo" if ok else self.mode
            return {"ok": ok, "path": path}
        if action == "set_params":
            self.mappo.min_green = float(params.get("min_green", self.mappo.min_green))
            self.mappo.max_green = float(params.get("max_green", self.mappo.max_green))
            if "switch_clearance" in params:
                self._clearance = float(params["switch_clearance"])
            if "mode" in params:
                self.mode = self._resolve_mode(params["mode"])
            return {"ok": True}
        if action == "get_params":
            return {"ok": True, "params": {
                "mode": self.mode,
                "min_green": self.mappo.min_green,
                "max_green": self.mappo.max_green,
                "switch_clearance": self._clearance,
            }}
        if action == "get_predictions":
            return {"ok": True, "predictions": self.predictor.predict()}
        if action == "get_status":
            return self._get_status()
        return {"ok": False, "message": f"未知动作: {action}"}

    def get_internal_metrics(self) -> dict:
        st = self._get_status()
        d = st.get("decision", {})
        return {"mode": st.get("mode"),
                "explore_epsilon": st.get("explore_epsilon", 0.0),
                "decision_count": d.get("count", 0),
                "mappo_actions": d.get("mappo_actions", 0),
                "scoot_actions": d.get("scoot_actions", 0),
                "downgrades": d.get("downgrades", 0)}

    def _get_status(self) -> dict:
        return {
            "mode": self.mode,
            "explore_epsilon": self.explore_epsilon,
            "recorder": self.recorder.status() if self.recorder else None,
            "mappo": self.mappo.status(),
            "stgcn": self.predictor.status(),
            "scoot": self.scoot.status(),
            "encoder": {"obs_dim": self.encoder.dim(),
                        "tls_count": len(self.encoder.tls_ids),
                        "max_stages": self.encoder.max_stages,
                        "green_phases": {t: len(v) for t, v in self.encoder.green_phases.items()},
                        "neighbor_edges": sum(len(v) for v in self.encoder._neighbors.values())},
            "reward": {"weights": self.reward.w},
            "decision": {"count": self._decision_count,
                         "mappo_actions": self._mappo_actions,
                         "scoot_actions": self._scoot_actions,
                         "downgrades": self._downgrades},
        }
