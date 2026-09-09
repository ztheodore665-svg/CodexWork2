"""轻量 STGCN 模型：逐边 MLP（可扩展图卷积），供训练脚本与推理复用。

仅在 PyTorch 可用时导入（本模块顶层依赖 torch）。
"""

import torch
import torch.nn as nn


class SmallSTGCN(nn.Module):
    """输入 (B, n_edges, lookback) 流量历史 → 输出 (B, n_edges) 下一时步预测流量。"""

    def __init__(self, n_edges: int, lookback: int = 12, hidden: int = 64):
        super().__init__()
        self.n_edges = n_edges
        self.lookback = lookback
        self.fc1 = nn.Linear(lookback, hidden)
        self.fc2 = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = torch.relu(self.fc1(x))
        return self.fc2(h).squeeze(-1)
