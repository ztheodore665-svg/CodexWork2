"""MAPPO 网络：参数共享 Actor + 中心化 Critic。仅在 PyTorch 可用时导入。"""

import torch
import torch.nn as nn


class ActorNet(nn.Module):
    """策略网络：观测 → 各相位动作概率。所有交叉口共享参数。"""

    def __init__(self, obs_dim: int, n_actions: int, hidden: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.Tanh(),
            nn.Linear(hidden, hidden), nn.Tanh(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, obs):
        return self.net(obs)


class CriticNet(nn.Module):
    """价值网络：全局状态（所有 agent 观测拼接）→ 状态价值。"""

    def __init__(self, global_dim: int, hidden: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(global_dim, hidden), nn.Tanh(),
            nn.Linear(hidden, hidden), nn.Tanh(),
            nn.Linear(hidden, 1),
        )

    def forward(self, state):
        return self.net(state).squeeze(-1)
