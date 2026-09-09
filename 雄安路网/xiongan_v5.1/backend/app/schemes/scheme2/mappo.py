"""MAPPO 策略网络：参数共享 Actor + 中心化 Critic，含启发式降级。"""

from collections import deque

from app.schemes.scheme2.stgcn import torch_available

HYPERPARAMS = {
    "hidden": 128, "actor_lr": 3e-4, "critic_lr": 1e-3,
    "gamma": 0.99, "lam": 0.95, "clip": 0.2, "entropy": 0.01,
    "vf_coef": 0.5, "max_grad_norm": 0.5, "update_interval": 300,
    "batch_size": 64, "buffer_capacity": 10000, "epochs": 4,
    "min_green": 0.0, "max_green": 45.0,
}


class MAPPOAgent:
    MODES = ("train", "infer", "heuristic")

    def __init__(self, obs_dim: int, n_actions: int, mode: str = "heuristic",
                 torch_ok: bool | None = None, global_dim: int | None = None,
                 **hp):
        self.obs_dim = obs_dim
        self.n_actions = n_actions
        self.hp = {**HYPERPARAMS, **hp}
        self.global_dim = global_dim if global_dim is not None else obs_dim
        self.torch_ok = (torch_available() if torch_ok is None else torch_ok)
        if mode == "heuristic":
            self.mode = "heuristic"
        elif self.torch_ok:
            self.mode = mode
        else:
            self.mode = "heuristic"

        self.current_phase = 0
        self.phase_elapsed = 0.0
        self.min_green = self.hp["min_green"]
        self.max_green = self.hp["max_green"]
        self.buffer: deque = deque(maxlen=self.hp["buffer_capacity"])
        self._updates = 0
        self._actor = None
        self._critic = None
        self._actor_opt = None
        self._critic_opt = None
        self._last_rewards: list[float] = []
        if self.torch_ok and self.mode in ("train", "infer"):
            self._build_networks()

    # ── 网络 ────────────────────────────────────────────────

    def _build_networks(self) -> None:
        import torch
        from app.models.mappo import ActorNet, CriticNet
        self._actor = ActorNet(self.obs_dim, self.n_actions, self.hp["hidden"])
        self._critic = CriticNet(self.global_dim, self.hp["hidden"])
        if self.mode == "train":
            self._actor_opt = torch.optim.Adam(self._actor.parameters(),
                                               lr=self.hp["actor_lr"])
            self._critic_opt = torch.optim.Adam(self._critic.parameters(),
                                                lr=self.hp["critic_lr"])

    # ── 动作选择 ────────────────────────────────────────────

    def act(self, obs: list[float], greedy: bool = False, mask: list | None = None) -> int:
        a, _ = self.act_logp(obs, greedy=greedy, mask=mask)
        return a

    def act_logp(self, obs: list[float], greedy: bool = False,
                 mask: list | None = None) -> tuple[int, float]:
        """返回 (动作索引, 该动作的 log 概率)。

        mask: 每动作是否有效（None=全部有效）。训练模式从策略分布采样（内在探索），
        推理模式取 argmax。启发式/无 torch 返回启发式动作。
        """
        if self.mode == "heuristic" or not self.torch_ok or self._actor is None:
            return self._heuristic_act(), 0.0
        import torch
        with torch.no_grad():
            logits = self._actor(torch.tensor([obs], dtype=torch.float32))
            if mask is not None:
                bias = torch.tensor([0.0 if m else -1e9 for m in mask],
                                    dtype=torch.float32).unsqueeze(0)
                logits = logits + bias
            if greedy:
                a = int(torch.argmax(logits[0]).item())
                probs = torch.softmax(logits[0], dim=0)
                logp = float(torch.log(probs[a] + 1e-8).item())
                return a, logp
            probs = torch.softmax(logits[0], dim=0)
            dist = torch.distributions.Categorical(probs=probs)
            a = int(dist.sample().item())
            logp = float(dist.log_prob(torch.tensor(a)).item())
            return a, logp

    def value_of(self, gobs: list[float]) -> float:
        """中心化 Critic 对全局状态的估值。"""
        if not self.torch_ok or self._critic is None:
            return 0.0
        import torch
        with torch.no_grad():
            return float(self._critic(torch.tensor([gobs], dtype=torch.float32)).item())

    def _heuristic_act(self) -> int:
        if self.phase_elapsed < self.min_green:
            return self.current_phase
        if self.phase_elapsed >= self.max_green:
            return (self.current_phase + 1) % self.n_actions
        return self.current_phase

    # ── 经验与更新 ──────────────────────────────────────────

    def store(self, obs, global_obs, action, reward, logp, value, done) -> None:
        self.buffer.append({"obs": obs, "global": global_obs, "action": action,
                            "reward": reward, "logp": logp, "value": value,
                            "done": done})
        self._last_rewards.append(reward)

    def update(self) -> dict:
        """正统 on-policy PPO：用存储的真实 logp/value 做重要性采样与 GAE。"""
        if not self.torch_ok or self._actor is None or not self.buffer:
            self._updates += 1
            return {"updates": self._updates, "ppo_loss": 0.0, "value_loss": 0.0}
        import torch
        import torch.nn.functional as F
        from app.schemes.scheme2.mappo_utils import compute_gae
        data = list(self.buffer)
        obs = torch.tensor([d["obs"] for d in data], dtype=torch.float32)
        gobs = torch.tensor([d["global"] for d in data], dtype=torch.float32)
        acts = torch.tensor([d["action"] for d in data], dtype=torch.long)
        rewards = torch.tensor([d["reward"] for d in data], dtype=torch.float32)
        old_logps = torch.tensor([d["logp"] for d in data], dtype=torch.float32)
        old_values = torch.tensor([d["value"] for d in data], dtype=torch.float32)
        dones = torch.tensor([float(d["done"]) for d in data], dtype=torch.float32)

        with torch.no_grad():
            returns, advantages = compute_gae(rewards, old_values, dones,
                                              self.hp["gamma"], self.hp["lam"])
        adv = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        ppo_loss, vf_loss = 0.0, 0.0
        for _ in range(self.hp["epochs"]):
            logits = self._actor(obs)
            # 注意：Actor 输出的是 logits（未归一化），必须经 log_softmax 才是 log 概率，
            # 否则与 old_logps（真实 log 概率）不在同一尺度，重要性比失真。
            log_probs = torch.log_softmax(logits, dim=1)
            new_logps = log_probs.gather(1, acts.unsqueeze(1)).squeeze(1)
            ratio = torch.exp(new_logps - old_logps)          # 与行为策略的重要性比
            clip_adv = torch.clamp(ratio, 1 - self.hp["clip"], 1 + self.hp["clip"]) * adv
            ppo_loss = -torch.min(ratio * adv, clip_adv).mean()
            probs = torch.softmax(logits, dim=1)
            ent = -torch.mean(torch.sum(probs * log_probs, dim=1))
            actor_loss = ppo_loss - self.hp["entropy"] * ent
            self._actor_opt.zero_grad()
            actor_loss.backward()
            torch.nn.utils.clip_grad_norm_(self._actor.parameters(),
                                           self.hp["max_grad_norm"])
            self._actor_opt.step()

            vpred = self._critic(gobs)
            vf_loss = F.mse_loss(vpred, returns)
            self._critic_opt.zero_grad()
            vf_loss.backward()
            self._critic_opt.step()
        self.buffer.clear()
        self._updates += 1
        return {"updates": self._updates,
                "ppo_loss": round(float(ppo_loss.detach()), 5),
                "value_loss": round(float(vf_loss.detach()), 5)}

    # ── 模型存取 ────────────────────────────────────────────

    def save(self, path_prefix: str) -> bool:
        if not self.torch_ok or self._actor is None:
            return False
        import torch
        torch.save(self._actor.state_dict(), f"{path_prefix}_actor.pt")
        torch.save(self._critic.state_dict(), f"{path_prefix}_critic.pt")
        return True

    def load(self, path_prefix: str, critic_required: bool = True) -> bool:
        """加载权重。Actor 必须成功；Critic 可容忍失败（部署/路网无关场景）。

        路网无关模型：Critic 维度 = obs_dim × 路口数，随路网变化；
        部署（infer）只用 Actor，跨路网加载时 Critic 维度不匹配可忽略。
        """
        if not self.torch_ok:
            return False
        import torch
        try:
            if self._actor is None:
                self._build_networks()
            self._actor.load_state_dict(torch.load(f"{path_prefix}_actor.pt",
                                                   map_location="cpu"))
            try:
                self._critic.load_state_dict(torch.load(f"{path_prefix}_critic.pt",
                                                        map_location="cpu"))
            except Exception:  # noqa: BLE001
                if critic_required:
                    raise
                print(f"[mappo] Critic 维度不匹配已忽略（部署仅用 Actor）: "
                      f"{path_prefix}_critic.pt")
            self._actor.eval()
            self._critic.eval()
            return True
        except Exception:  # noqa: BLE001 加载失败保持启发式
            return False

    # ── 断点续训 ────────────────────────────────────────────

    def save_checkpoint(self, path: str, extra: dict | None = None) -> bool:
        """保存完整训练状态（权重 + 优化器 + 超参 + 更新计数 + 额外进度）。

        extra 用于携带训练进度（轮次/场景/探索率等），由训练脚本解释。
        """
        if not self.torch_ok or self._actor is None:
            return False
        import torch
        torch.save({
            "actor": self._actor.state_dict(),
            "critic": self._critic.state_dict(),
            "actor_opt": self._actor_opt.state_dict() if self._actor_opt else None,
            "critic_opt": self._critic_opt.state_dict() if self._critic_opt else None,
            "hp": dict(self.hp),
            "updates": self._updates,
            "obs_dim": self.obs_dim,
            "n_actions": self.n_actions,
            "global_dim": self.global_dim,
            "mode": self.mode,
            "extra": extra or {},
        }, path)
        return True

    def load_checkpoint(self, path: str) -> dict | None:
        """加载断点。返回 checkpoint 的 extra 进度 dict（无 torch 或失败返回 None）。"""
        if not self.torch_ok:
            return None
        import torch
        try:
            ck = torch.load(path, map_location="cpu")
            self.obs_dim = int(ck["obs_dim"])
            self.n_actions = int(ck["n_actions"])
            self.global_dim = int(ck["global_dim"])
            self.hp = {**HYPERPARAMS, **ck.get("hp", {})}
            self.mode = ck.get("mode", "train")
            # 同步绿灯上下限属性（与 hp 保持一致）
            self.min_green = float(self.hp.get("min_green", 8.0))
            self.max_green = float(self.hp.get("max_green", 45.0))
            # 占位/旧网络形状可能不匹配，按断点维度强制重建
            self._build_networks()
            self._actor.load_state_dict(ck["actor"])
            self._critic.load_state_dict(ck["critic"])
            if self._actor_opt is not None and ck.get("actor_opt") is not None:
                self._actor_opt.load_state_dict(ck["actor_opt"])
            if self._critic_opt is not None and ck.get("critic_opt") is not None:
                self._critic_opt.load_state_dict(ck["critic_opt"])
            self._updates = int(ck.get("updates", 0))
            return ck.get("extra") or {}
        except Exception:  # noqa: BLE001
            return None

    # ── 状态 ────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "mode": self.mode, "torch_available": self.torch_ok,
            "model_loaded": self._actor is not None,
            "obs_dim": self.obs_dim, "actions": self.n_actions,
            "buffer_size": len(self.buffer), "updates": self._updates,
            "last_reward_mean": round(sum(self._last_rewards[-20:]) / max(1, len(self._last_rewards[-20:])), 4),
            "hyperparams": self.hp,
        }
