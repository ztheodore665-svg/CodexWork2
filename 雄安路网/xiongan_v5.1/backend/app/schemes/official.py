"""官方方案（mappo优化）：20 路口路网专属 · 官方三档配时切换。

数据：backend/data/official_phase_programs.json —— 由 scripts/build_official_phase_programs.py
把官方 demo_1..20 的早/平/晚三套配时（相位名/绿时/黄灯/全红，逐相位完全忠于官方）
展开为 base_network 各信号机（tls id '1'..'20'，与官方一一对应）可直接热替换的
完整 SUMO 相位程序。

运行策略（简化 mappo / 自适应选档）：
  - 每个信号路口独立决策，周期 DECISION_STEP（默认 60s）选一次档；
  - 档位动作 = {0:早高峰, 1:平峰, 2:晚高峰}；
  - 决策特征 = 该路口按官方"东西/南北"臂分组的实时排队比 + 该档官方绿时占比；
    （正交四臂路口自动 EW/NS 选档；无法分方向组的 T 型/斜交/无差异路口
      退化为"总绿时最大档"或固定早高峰档，并在 get_status 标注退化原因）
  - 支持加载学习权重（simplified MAPPO / 小策略），mode=mappo 时走权重推理；
    未加载权重时用上面启发式（可解释、立即可演示）。
  - 换档 = 把该信号机整程序热替换为所选官方档相位序列（setProgramLogic），
    即 SUMO 层面完整执行官方配时（直行/左转按最左车道分相、右转并入最右或常绿）。

非 base_network / 无官方库：不接管，保持路网原配时（等价固定配时基线），
get_status 标注原因，方案对所有路网安全。
"""

import json
import os

from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.schemes.base import BaseScheme
from app.schemes.registry import register_scheme

DECISION_STEP = 60          # 决策周期（仿真秒）
MODE_AUTO = "auto"          # 启发式自适应选档（EW/NS 排队比匹配官方绿时占比）
MODE_MAPPO = "mappo"        # 学习策略（权重加载后走 argmax，未加载回退 auto）
PLAN_NAMES = ["早高峰", "平峰", "晚高峰"]
WEIGHTS_DEFAULT = "models/weights/official_plan_policy.json"

# EW/NS 官方方向组判定所需的罗盘集合（近正交网格；斜向臂按最接近主轴折算见 build 脚本）
_EW_SET = {"E", "NE", "SE"}
_NS_SET = {"N", "NW", "S", "SW"}


def _data_path() -> str:
    d = os.path.dirname(os.path.abspath(__file__))          # .../app/schemes
    return os.path.join(os.path.dirname(os.path.dirname(d)),  # .../app → backend/data
                        "data", "official_phase_programs.json")


@register_scheme
class OfficialPlansController(BaseScheme):
    """官方方案（mappo优化）：官方三档配时切换（20 路口路网专属）。"""

    name = "official"

    algorithm_spec = AlgorithmSpec(
        kind="signal",
        description="官方方案（mappo优化）：将官方 20 路口早/平/晚三套配时按相位"
                    "完整重建到信号机程序，运行中按实时排队自适应选档（简化 mappo）",
        params=[
            ParamSpec("mode", "决策模式", type="enum", default="auto",
                      enum=["auto", "mappo"],
                      desc="auto=启发式 EW/NS 排队比选档；mappo=学习策略权重选档"),
            ParamSpec("decision_step", "决策周期", type="number", default=DECISION_STEP,
                      minimum=20, maximum=300, unit="s",
                      desc="每路口重新选档的间隔"),
            ParamSpec("cooldown", "换档冷却", type="number", default=120.0,
                      minimum=20, maximum=600, unit="s",
                      desc="换档后的冷却期（防抖，避免整程序替换频繁打断周期）"),
        ],
        observables=["vehicle_count", "avg_queue", "tls_queues"],
        metrics=["mode", "tls_switches", "plan_tls"],
        capabilities=["switch_mode", "switch_plan"],
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        self.mode = str(ctx.config.get("mode", MODE_AUTO)).strip().lower()
        if self.mode not in (MODE_AUTO, MODE_MAPPO):
            self.mode = MODE_AUTO
        self.weights = str(ctx.config.get("weights",
                                           WEIGHTS_DEFAULT)).strip() or WEIGHTS_DEFAULT
        self.decision_step = float(ctx.config.get("decision_step", DECISION_STEP))
        self._cooldown = float(ctx.config.get("cooldown", 120.0))  # 换档后冷却（防抖）
        # 简化 mappo 小策略（numpy MLP，权重 JSON 与 scripts/train_official_mappo.py 同构）
        self._policy_w = None
        self._policy_obs = 3
        if self.mode == MODE_MAPPO:
            self._load_policy()
        # 加载官方相位程序库
        self._prog = self._load_programs()
        self._active_tls: list[str] = []      # 受管信号机（有官方库的 tls）
        self._schedules: dict[str, list] = {}  # tid -> [ [ (dur,state)... ] x3 档 ]
        self._arms: dict[str, dict] = {}       # tid -> {edge: compass}
        self._conns: dict[str, list] = {}      # tid -> [{from,lane,dir}...]
        self._hungry: dict[str, set[int]] = {}  # tid -> 官方三档从未放行的槽位（补让行绿）
        self._plan: dict[str, int] = {}        # tid -> 当前档 0..2
        self._next_at: dict[str, int] = {}     # tid -> 下次决策步
        self._last_switch: dict[str, int] = {}  # tid -> 上次换档步（冷却防抖）
        self._switches = 0
        self._last_step = 0
        self._reason = ""

    # ── 数据加载 ────────────────────────────────────────────

    def _load_programs(self):
        p = _data_path()
        if not os.path.isfile(p):
            self._reason = f"缺官方相位库: {p}"
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            self._reason = f"官方相位库解析失败: {exc}"
            return {}

    def _load_policy(self) -> None:
        """加载简化 mappo 策略权重（numpy MLP，flat-w JSON）。失败仅降级为 auto。"""
        try:
            import numpy as np
            p = self.weights
            if not os.path.isabs(p):
                base = os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))))    # backend/
                cand = os.path.join(base, p)
                if os.path.isfile(cand):
                    p = cand
            if not os.path.isfile(p):
                self.mode = MODE_AUTO
                return
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            w = np.asarray(d["w"], dtype=np.float64)
            hidden = int(d.get("hidden", 16))
            obs_dim = int(d.get("obs_dim", 3))
            n1 = (obs_dim + 1) * hidden
            self._policy_w = (w[:n1].reshape(obs_dim + 1, hidden),
                              w[n1:].reshape(hidden + 1, int(d.get("n_act", 3))))
            self._policy_obs = obs_dim
        except Exception:  # noqa: BLE001 权重损坏则退回启发式，不阻塞方案运行
            self._policy_w = None
            self.mode = MODE_AUTO

    def _policy_argmax(self, ew: float, ns: float, tot: float) -> int:
        """mappo 策略 argmax：obs=[ew/40, ns/40, tot/80]（与训练脚本归一一致）。"""
        try:
            import numpy as np
            w1, w2 = self._policy_w
            obs = np.asarray([ew / 40.0, ns / 40.0, tot / 80.0,
                              *([0.0] * (self._policy_obs - 3))],
                             dtype=np.float64)[:self._policy_obs]
            h = np.tanh(np.concatenate([obs, [1.0]]) @ w1)
            logits = np.concatenate([h, [1.0]]) @ w2
            return int(np.argmax(logits))
        except Exception:  # noqa: BLE001
            return 0

    def _net_matches(self) -> bool:
        """确认当前路网是官方库对应的 20 路口路网（tls id 为数字 1..20 且都能在库中找到）。"""
        try:
            ids = self.ctx.engine.get_tls_ids()
        except Exception:  # noqa: BLE001
            return False
        if not ids:
            return False
        return all(t in self._prog for t in ids)

    def init(self) -> None:
        self._last_step = 0
        if not self._prog:
            return
        if not self._net_matches():
            self._reason = "当前路网无官方配时库（非 20 路口路网），保持路网原配时=固定基线"
            return
        eng = self.ctx.engine
        self._active_tls = [t for t in eng.get_tls_ids() if t in self._prog]
        for tid in self._active_tls:
            entry = self._prog[tid]
            self._arms[tid] = {e: c for e, c in (entry.get("arms") or {}).items()}
            self._conns[tid] = entry.get("conns") or []
            # 饥饿槽位：官方三档任何相位都从未放行的连接（掉头/左转/个别直行），
            # 统一并入"有绿相位"作让行绿 g，避免最左车道/个别方向永久饿死堵路
            self._hungry[tid] = self._find_hungry_slots(tid, entry)
            self._schedules[tid] = []
            for pi, plan in enumerate(entry.get("plans", [])):
                sched = [(ph["dur"], ph["state"]) for ph in plan.get("phases", [])]
                self._schedules[tid].append(self._with_starved_green(tid, sched))
            # 起始档：默认车流较平缓 → 选周期最短档起步（避免空放高等待），
            # 之后按实时排队自动升降档
            self._plan[tid] = self._lightest_plan(tid)
            self._next_at[tid] = int(self.decision_step)
        if not self._active_tls:
            self._reason = "当前路网无受管信号机"
            return
        self._apply_plan_silent()   # 起步档程序先落地，避免前 60s 仍是内嵌固定配时
        self._reason = f"官方库 {len(self._prog)} 路口 · 受管 {len(self._active_tls)} 信号机 · 简化mappo选档"

    @staticmethod
    def _find_hungry_slots(tid: str, entry: dict) -> set[int]:
        """官方三档全部相位中从未出现 G/g 的槽位（完全饥饿）。

        仅按官方库 state 判定即可：与 SUMO 受控槽位同序（引擎
        set_tls_phase_schedule 整程序替换按 linkIndex 解释字符）。
        """
        conns = entry.get("conns") or []
        n = len(conns)
        ever = [False] * n
        for plan in entry.get("plans", []):
            for ph in plan.get("phases", []):
                st = ph.get("state", "")
                for i in range(min(len(st), n)):
                    if st[i] in "Gg":
                        ever[i] = True
        return {i for i, ok in enumerate(ever) if not ok}

    def _with_starved_green(self, tid: str, sched: list) -> list:
        """饥饿槽位补让行绿：凡"相位内已有其他绿(G/g)"时把饥饿槽位置 g。

        背景：官方 xlsx 配时普遍未给最左车道掉头(t)/左转(l)（个别路口连某些
        直行，如 tls7 E3_7 SE 臂）设计相位 → 这些连接在排队中永远等不到绿，
        会把对应车道堵死。此处不动官方相位结构/周期：视作右转一样的常绿让行
        —— 任意"相位内已有其他绿"时给小写 g（让行，不触发 SUMO unsafe 冲突
        告警）；黄灯/全红清空相保持原样。对每套早/平/晚档统一应用一次。
        """
        starved = self._hungry.get(tid)
        if not starved:
            return sched
        out: list = []
        for dur, state in sched:
            st = list(state)
            # 该相位除饥饿槽外是否已有绿（黄灯/全红清空相保持原样）
            has_green_other = any(
                i not in starved and ch in "Gg" for i, ch in enumerate(st))
            if has_green_other:
                for i in starved:
                    if i < len(st) and st[i] not in "Gg":
                        st[i] = "g"
            out.append((dur, "".join(st)))
        return out

    # ── 每步调度 ────────────────────────────────────────────

    def on_step(self) -> None:
        if not self._active_tls:
            return
        self._last_step += 1
        now = self._last_step
        changed = False
        for tid in self._active_tls:
            if now < self._next_at.get(tid, 1 << 30):
                continue
            # 冷却期内不重复换档（防抖：整程序替换会重置相位进度，避免频繁打断周期）
            if now - self._last_switch.get(tid, -1 << 30) < self._cooldown:
                self._next_at[tid] = now + int(self.decision_step)
                continue
            self._next_at[tid] = now + int(self.decision_step)
            pid = self._decide_plan(tid)
            if pid is not None and pid != self._plan.get(tid):
                self._plan[tid] = pid
                try:
                    sched = self._schedules[tid][pid]
                    self.ctx.engine.set_tls_phase_schedule(tid, sched)
                    self._switches += 1
                    self._last_switch[tid] = now
                    changed = True
                except Exception:  # noqa: BLE001 单路口失败不阻塞
                    pass
        if changed:
            ev = getattr(self.ctx, "push_event", None)
            if callable(ev):
                try:
                    ev("scheme", f"官方方案：自动选档切换（累计 {self._switches} 次）", {})
                except Exception:  # noqa: BLE001
                    pass

    def _apply_plan_silent(self) -> None:
        """启动即应用所选档位的整程序（把官方配时真正落到 SUMO）。"""
        for tid in self._active_tls:
            pid = self._plan.get(tid, 0)
            sched = self._schedules[tid][pid]
            try:
                self.ctx.engine.set_tls_phase_schedule(tid, sched)
            except Exception:  # noqa: BLE001
                pass

    def _lightest_plan(self, tid: str) -> int:
        """周期最短档（平峰期倾向：空放等待小；车流上来后按需升档）。"""
        cyc = [float(p.get("cycle_built") or 0) for p in
               (self._prog.get(tid) or {}).get("plans", [])]
        return min(range(len(cyc)), key=lambda i: cyc[i]) if cyc else 0

    # ── 简化 mappo / 启发式选档 ────────────────────────────

    def _dir_queues(self, tid: str) -> tuple[float, float, float]:
        """返回 (EW 排队, NS 排队, 总排队)。按官方库 arms 的罗盘分组。"""
        eng = self.ctx.engine
        ew = ns = 0.0
        for edge, comp in (self._arms.get(tid) or {}).items():
            try:
                q = float(eng.get_edge_queue(edge))
            except Exception:  # noqa: BLE001
                q = 0.0
            if comp in _EW_SET:
                ew += q
            elif comp in _NS_SET:
                ns += q
        return ew, ns, ew + ns

    def _decide_plan(self, tid: str) -> int | None:
        """返回下一个档位 index；None=保持现状。

        规则（低需求短周期、高需求按官方配时切换，符合交通工程）：
        - 该路口官方档位若方向差异明显(spread>0.08)且排队偏一侧 → 选该侧绿时占比最高的档；
        - 否则按实时排队强度选档：排队很轻 → 最短周期档（减少空放等待），
          排队较重 → 最长周期档（高峰需要更大周期）；中间强度保持当前档避免抖档。
        """
        entry = self._prog[tid]
        plans = entry.get("plans") or []
        if len(plans) < 2:
            return None
        ew_q, ns_q, tot_q = self._dir_queues(tid)
        if self.mode == MODE_MAPPO and self._policy_w is not None:
            pid = self._policy_argmax(ew_q, ns_q, tot_q)
            return pid if 0 <= pid < len(plans) else None
        ew_ratios = [float(p.get("ew_ratio") or 0.5) for p in plans]
        spread = max(ew_ratios) - min(ew_ratios)
        cyc = [float(p.get("cycle_built") or 0) for p in plans]
        ew_q, ns_q, tot_q = self._dir_queues(tid)
        cur = self._plan.get(tid, 0)
        if spread > 0.08 and (ew_q + ns_q) > 0:
            demand_ew = ew_q / (ew_q + ns_q)
            best, best_d = 0, 1e9
            for pi, r in enumerate(ew_ratios):
                d = abs(r - demand_ew)
                if d < best_d:
                    best, best_d = pi, d
            return best
        # 无方向差异或排队均衡 → 按强度选周期
        if tot_q >= 8.0:
            target = max(range(len(cyc)), key=lambda i: cyc[i])   # 高峰长周期
        elif tot_q <= 2.0:
            target = min(range(len(cyc)), key=lambda i: cyc[i])   # 低峰短周期
        else:
            return cur                                          # 中间强度保持稳定
        if target == cur and max(cyc) - min(cyc) >= 1:
            return None
        return target

    # ── API 动作 ────────────────────────────────────────────

    def handle_action(self, action: str, params: dict) -> dict:
        if action == "switch_mode":
            m = str(params.get("mode", "") or "").strip().lower()
            if m == MODE_MAPPO:
                if self._policy_w is None:
                    self._load_policy()          # 切到 mappo 时若权重缺失则尝试加载
                if self._policy_w is None:
                    return {"ok": False, "message": "未找到 mappo 权重，无法切换到学习策略"}
                self.mode = MODE_MAPPO
                return {"ok": True, "mode": self.mode, "note": "已加载学习策略权重"}
            if m == MODE_AUTO:
                self.mode = MODE_AUTO
                return {"ok": True, "mode": self.mode}
            return {"ok": False, "message": f"未知模式: {m}"}
        if action == "switch_plan":
            # 手动选档：plan = 早高峰 / 平峰 / 晚高峰（或 0/1/2）
            want = params.get("plan")
            if isinstance(want, str):
                try:
                    idx = PLAN_NAMES.index(want)
                except ValueError:
                    return {"ok": False, "message": f"档位不存在: {want}"}
            else:
                idx = int(want or 0)
            tid = str(params.get("tls_id", "") or "")
            if not self._active_tls:
                return {"ok": False, "message": "当前路网未启用官方方案"}
            targets = [tid] if tid in self._active_tls else self._active_tls
            for t in targets:
                if 0 <= idx < len(self._schedules.get(t, [])):
                    self._plan[t] = idx
                    try:
                        self.ctx.engine.set_tls_phase_schedule(
                            t, self._schedules[t][idx])
                        self._switches += 1
                    except Exception:  # noqa: BLE001
                        pass
            return {"ok": True, "plan": PLAN_NAMES[idx] if idx < 3 else idx,
                    "tls": targets}
        if action == "set_params":
            if "mode" in params:
                m = str(params["mode"]).strip().lower()
                if m == MODE_MAPPO and self._policy_w is None:
                    self._load_policy()
                self.mode = m if m in (MODE_AUTO, MODE_MAPPO) else self.mode
            if "decision_step" in params:
                self.decision_step = float(params["decision_step"])
            if "cooldown" in params:
                self._cooldown = float(params["cooldown"])
            return {"ok": True}
        if action == "get_params":
            return {"ok": True, "params": {
                "mode": self.mode, "decision_step": self.decision_step,
                "cooldown": self._cooldown}}
        if action == "get_status":
            return self._get_status()
        return {"ok": False, "message": f"未知动作: {action}"}

    def _get_status(self) -> dict:
        return {
            "ok": True,
            "mode": self.mode,
            "tls_switches": self._switches,
            "active_tls": len(self._active_tls),
            "reason": self._reason,
            "plans": {t: (self._plan.get(t, 0), PLAN_NAMES[self._plan.get(t, 0)]
                          if self._plan.get(t, 0) < 3 else self._plan.get(t, 0))
                      for t in self._active_tls},
            "decision_step": self.decision_step,
        }

    def cleanup(self) -> None:
        self._active_tls = []
        self._schedules = {}
        self._plan = {}
