"""MAPPO Actor 模型转换与轻量化部署验证（赛道 C）。

功能：
  1. 将 PyTorch Actor（31 维观测 → 2 动作 logits）导出为 ONNX；
  2. 用 ONNX Runtime 推理，与 PyTorch 输出做一致性对比（最大误差）；
  3. 测量推理延迟（单次 ms，中位数）与模型体积；
  4. 动态 int8 量化对比：体积/延迟/精度损失。

用法：
    cd backend
    python scripts/export_mappo_onnx.py [--prefix models/weights/mappo_act_full] [--samples 1000]
输出：
    models/weights/mappo_act_full_actor.onnx        （FP32 导出）
    models/weights/mappo_act_full_actor_int8.onnx   （int8 动态量化）
    控制台汇总：一致性误差 / 延迟 / 体积对比表
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np


def load_actor(prefix: str):
    """加载 ActorNet（31 → 128 → 128 → 2，Tanh）。"""
    import torch
    from app.models.mappo import ActorNet
    actor = ActorNet(obs_dim=31, n_actions=2, hidden=128)
    state = torch.load(f"{prefix}_actor.pt", map_location="cpu")
    actor.load_state_dict(state)
    actor.eval()
    return actor


def export_onnx(actor, obs_dim: int, out_path: str) -> None:
    import torch
    import onnx
    dummy = torch.randn(1, obs_dim, dtype=torch.float32)
    # 优先用经典 TorchScript 导出（dynamo=False）：生成的图与 onnxruntime
    # 的 int8 动态量化兼容性最好；失败再回退到新导出器（dynamo=True）
    try:
        torch.onnx.export(
            actor, dummy, out_path,
            input_names=["obs"], output_names=["logits"],
            dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=13, dynamo=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[onnx] 经典导出失败（{type(exc).__name__}），回退新导出器")
        torch.onnx.export(
            actor, dummy, out_path,
            input_names=["obs"], output_names=["logits"],
            dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=13,
        )
    # 形状推断修复 + 权重强制内嵌（单文件自包含，便于拷贝部署）
    model = onnx.load(out_path)
    model = onnx.shape_inference.infer_shapes(model)
    onnx.save(model, out_path, save_as_external_data=False)
    print(f"[onnx] 已导出: {out_path}")


def latency_ms(fn, samples, n_repeat=200):
    """单次推理延迟中位数（ms）。"""
    ts = []
    for _ in range(n_repeat):
        t0 = time.perf_counter()
        fn(samples)
        ts.append((time.perf_counter() - t0) * 1000.0)
    return float(np.median(ts))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", default="models/weights/mappo_act_full",
                   help="模型前缀（_actor.pt / _critic.pt）")
    p.add_argument("--samples", type=int, default=1000, help="一致性测试样本数")
    p.add_argument("--latency-repeats", type=int, default=300, help="延迟测试重复次数")
    args = p.parse_args()

    import torch
    import onnxruntime as ort

    obs_dim = 31
    actor = load_actor(args.prefix)
    onnx_path = f"{args.prefix}_actor.onnx"
    export_onnx(actor, obs_dim, onnx_path)

    # ── 一致性测试：PyTorch vs ONNX Runtime ──
    rng = np.random.RandomState(42)
    X = rng.randn(args.samples, obs_dim).astype(np.float32)
    Xt = torch.tensor(X)
    with torch.no_grad():
        ref = actor(Xt).numpy()                      # (N, 2) logits

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    pred = sess.run(["logits"], {"obs": X})[0]
    max_err = float(np.abs(ref - pred).max())
    mean_err = float(np.abs(ref - pred).mean())
    same_argmax = float((ref.argmax(1) == pred.argmax(1)).mean())
    print(f"[一致] 样本={args.samples}  最大误差={max_err:.2e}  平均误差={mean_err:.2e}  "
          f"argmax 一致率={same_argmax * 100:.2f}%")

    # ── 延迟对比 ──
    Xb = rng.randn(64, obs_dim).astype(np.float32)
    Xbt = torch.tensor(Xb)
    with torch.no_grad():
        def torch_fn(x):
            actor(x)
    lat_torch = latency_ms(lambda _: torch_fn(Xbt), Xb, args.latency_repeats)
    lat_onnx = latency_ms(lambda x: sess.run(None, {"obs": x}), Xb, args.latency_repeats)

    # ── int8 动态量化 ──
    int8_path = f"{args.prefix}_actor_int8.onnx"
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        quantize_dynamic(onnx_path, int8_path, weight_type=QuantType.QUInt8)
        sess8 = ort.InferenceSession(int8_path, providers=["CPUExecutionProvider"])
        pred8 = sess8.run(["logits"], {"obs": X})[0]
        max_err8 = float(np.abs(ref - pred8).max())
        same_argmax8 = float((ref.argmax(1) == pred8.argmax(1)).mean())
        lat_int8 = latency_ms(lambda x: sess8.run(None, {"obs": x}), Xb,
                              args.latency_repeats)
        quant_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"[int8] 量化失败（跳过）: {exc}")
        max_err8 = same_argmax8 = lat_int8 = float("nan")
        quant_ok = False

    # ── 体积 ──
    def size_mb(path):
        return os.path.getsize(path) / 1024.0 / 1024.0

    print("\n================ 轻量化部署验证汇总 ================")
    print(f"{'模型':<28}{'体积(KB)':<12}{'延迟(ms/批64)':<16}{'最大误差':<12}{'argmax一致'}")
    print(f"{'PyTorch fp32':<28}{size_mb(f'{args.prefix}_actor.pt') * 1024:<12.1f}"
          f"{lat_torch:<16.3f}{'—':<12}{'—'}")
    print(f"{'ONNX fp32':<28}{size_mb(onnx_path) * 1024:<12.1f}"
          f"{lat_onnx:<16.3f}{max_err:<12.2e}{same_argmax * 100:<12.1f}%")
    if quant_ok:
        print(f"{'ONNX int8':<28}{size_mb(int8_path) * 1024:<12.1f}"
              f"{lat_int8:<16.3f}{max_err8:<12.2e}{same_argmax8 * 100:<12.1f}%")
    print("=====================================================")
    print(f"单次决策延迟(1 观测, ONNX fp32): "
          f"{latency_ms(lambda x: sess.run(None, {'obs': x[:1]}), Xb[:1], 500):.4f} ms")
    print(f"输出文件: {onnx_path}")
    if quant_ok:
        print(f"          {int8_path}")


if __name__ == "__main__":
    main()
