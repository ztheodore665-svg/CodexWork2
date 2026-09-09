"""STGCN 交通流预测器训练脚本（独立运行，建议单独开窗口执行）。

用法：
    python -m app.models.train_stgcn --synthetic --epochs 50
    python -m app.models.train_stgcn --data data/edge_flow.csv --epochs 100

CPU 占用：默认使用约 60% 的 CPU 线程，可用 --cpu-ratio 调整。
输出：--out 指向的权重文件 + loss.csv。
"""

import argparse
import math
import os
import sys

import numpy as np

from app.schemes.scheme2.stgcn import torch_available

if not torch_available():
    print("[train_stgcn] 需要 PyTorch：pip install torch")
    sys.exit(1)

import torch  # noqa: E402


def set_cpu_threads(ratio: float) -> int:
    cores = os.cpu_count() or 4
    n = max(1, int(cores * ratio))
    torch.set_num_threads(n)
    print(f"[train_stgcn] CPU 线程 {n}/{cores}（{int(ratio * 100)}%）")
    return n


def synthetic(n_edges: int = 6, length: int = 400) -> dict[str, np.ndarray]:
    """生成带噪声的正弦流量序列，用于验证训练管线。"""
    series = {}
    t = np.arange(length)
    for i in range(n_edges):
        period = 60 + i * 5
        base = 200 + 60 * np.sin(2 * np.pi * t / period)
        series[f"e{i}"] = base + np.random.RandomState(i).normal(0, 6.0, length)
    return series


def load_series(path: str) -> dict[str, np.ndarray]:
    """读取 CSV（表头 step,edge,flow[,speed,occupancy]），按边分组为时间序列。"""
    series: dict[str, list[float]] = {}
    files = []
    if os.path.isdir(path):
        files = sorted(f for f in os.listdir(path) if f.endswith(".csv"))
        files = [os.path.join(path, f) for f in files]
    else:
        files = [path]
    for f in files:
        with open(f, encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                edge = parts[1]
                try:
                    flow = float(parts[2])
                except ValueError:
                    continue
                series.setdefault(edge, []).append(flow)
    if not series:
        raise SystemExit(f"[train_stgcn] 未从 {path} 读到数据")
    return {k: np.asarray(v) for k, v in series.items()}


def make_samples(series: dict[str, np.ndarray], lookback: int):
    ids = sorted(series)
    length = min(len(v) for v in series.values())
    if length <= lookback:
        raise SystemExit(f"[train_stgcn] 数据过短：{length} <= {lookback}")
    x, y = [], []
    for t in range(lookback, length):
        x.append([[float(series[e][t - lookback + i]) for i in range(lookback)]
                  for e in ids])
        y.append([float(series[e][t]) for e in ids])
    return np.array(x, dtype=np.float32), np.array(y, dtype=np.float32), ids


def train(args) -> None:
    set_cpu_threads(args.cpu_ratio)
    series = load_series(args.data) if args.data else synthetic(args.n_edges, args.length)
    x, y, ids = make_samples(series, args.lookback)
    n_edges = len(ids)
    n_samples = x.shape[0]
    print(f"[train_stgcn] 边数={n_edges} 样本数={n_samples}")

    from app.models.stgcn import SmallSTGCN
    model = SmallSTGCN(n_edges=n_edges, lookback=args.lookback, hidden=args.hidden)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = torch.nn.MSELoss()

    xt = torch.tensor(x)
    yt = torch.tensor(y)
    losses = []
    for epoch in range(args.epochs):
        model.train()
        opt.zero_grad()
        pred = model(xt)
        loss = loss_fn(pred, yt)
        loss.backward()
        opt.step()
        losses.append(float(loss.item()))
        if (epoch + 1) % max(1, args.epochs // 5) == 0:
            print(f"[train_stgcn] epoch {epoch + 1}/{args.epochs} loss={loss.item():.4f}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    torch.save(model.state_dict(), args.out)
    with open(args.loss_csv, "w", encoding="utf-8") as fh:
        fh.write("epoch,loss\n")
        for i, l in enumerate(losses):
            fh.write(f"{i + 1},{l:.6f}\n")
    print(f"[train_stgcn] 完成，权重保存到 {args.out}，loss 曲线保存到 {args.loss_csv}")


def main() -> None:
    p = argparse.ArgumentParser(description="STGCN 交通流预测训练")
    p.add_argument("--data", default=None, help="edge 流量 CSV 文件或目录（缺省用合成数据）")
    p.add_argument("--synthetic", action="store_true", help="强制使用合成数据")
    p.add_argument("--n-edges", type=int, default=6)
    p.add_argument("--length", type=int, default=400)
    p.add_argument("--lookback", type=int, default=12)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--cpu-ratio", type=float, default=0.6,
                   help="使用的 CPU 线程比例（默认 0.6，约六成）")
    p.add_argument("--out", default="models/weights/stgcn.pt")
    p.add_argument("--loss-csv", default="models/weights/stgcn_loss.csv")
    train(p.parse_args())


if __name__ == "__main__":
    main()
