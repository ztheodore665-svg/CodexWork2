"""MAPPO 训练工具：GAE 广义优势估计。仅在 torch 可用时被调用。"""

import torch


def compute_gae(rewards: torch.Tensor, values: torch.Tensor,
                dones: torch.Tensor, gamma: float, lam: float):
    """返回 (returns, advantages)。输入均为长度为 T 的一维张量。"""
    t_len = rewards.shape[0]
    returns = torch.zeros_like(rewards)
    advantages = torch.zeros_like(rewards)
    gae = 0.0
    for t in reversed(range(t_len)):
        next_value = 0.0 if t == t_len - 1 else values[t + 1]
        delta = rewards[t] + gamma * next_value * (1 - dones[t]) - values[t]
        gae = delta + gamma * lam * (1 - dones[t]) * gae
        returns[t] = gae + values[t]
        advantages[t] = gae
    return returns, advantages
