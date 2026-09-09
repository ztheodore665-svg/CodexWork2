"""STGCN 交通流预测器：模型推理 / 在线学习 / EWMA 降级三模式。"""

from collections import defaultdict, deque

HISTORY_LEN = 12
EWMA_ALPHA = 0.3


def torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class TrafficPredictor:
    MODES = ("model", "online", "ewma")

    def __init__(self, ctx, adj: dict | None = None, mode: str | None = None,
                 history_len: int = HISTORY_LEN, alpha: float = EWMA_ALPHA):
        self.ctx = ctx
        self.adj = adj or {}
        self.history_len = history_len
        self.alpha = alpha
        self._history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=history_len))
        self._ewma: dict[str, float] = {}
        self._updates = 0
        self._predictions = 0
        self._model = None
        self._mode = self._resolve_mode(mode)

    def _resolve_mode(self, mode: str | None) -> str:
        if mode:
            return mode
        if torch_available():
            weights = self.ctx.config.get("stgcn_weights")
            return "model" if weights else "online"
        return "ewma"

    # ── 数据更新 ────────────────────────────────────────────

    def update(self, edge_features: dict) -> None:
        for eid, f in edge_features.items():
            flow = float(f.get("flow", 0.0))
            self._history[eid].append(flow)
            prev = self._ewma.get(eid, flow)
            self._ewma[eid] = self.alpha * flow + (1 - self.alpha) * prev
        self._updates += 1

    # ── 预测 ────────────────────────────────────────────────

    def predict(self) -> dict:
        if (self._mode == "model" and self._model is not None
                and self._model_ready()):
            return self._model_predict_all()
        out = {eid: self._ewma_predict(eid, ewma, list(self._history[eid]))
               for eid, ewma in self._ewma.items()}
        self._predictions += 1
        return out

    def _ewma_predict(self, eid: str, ewma: float, hist: list[float]) -> float:
        if len(hist) >= 2:
            trend = hist[-1] - hist[-2]
            return max(0.0, ewma + 0.5 * trend)
        return ewma

    def _model_ready(self) -> bool:
        return len(self._ewma) >= 2 and all(
            len(self._history[eid]) >= self.history_len for eid in self._ewma)

    def _model_predict_all(self) -> dict:
        try:
            import torch  # noqa: F401
            ids = list(self._ewma)
            feats = [[list(self._history[eid])[-self.history_len:] for eid in ids]]
            x = torch.tensor(feats, dtype=torch.float32)  # (1, n, lookback)
            with torch.no_grad():
                pred = self._model(x)  # (1, n)
            self._predictions += 1
            return {eid: float(v) for eid, v in zip(ids, pred[0])}
        except Exception:  # noqa: BLE001 推理失败降级 EWMA
            return {eid: self._ewma.get(eid, 0.0) for eid in self._ewma}

    # ── 模型加载（推理/在线） ───────────────────────────────

    def load_model(self, path: str) -> bool:
        if not torch_available():
            self._mode = "ewma"
            return False
        try:
            import torch
            from app.models.stgcn import SmallSTGCN
            state = torch.load(path, map_location="cpu")
            self._model = SmallSTGCN(n_edges=max(1, len(self._ewma) or 1),
                                     lookback=self.history_len)
            self._model.load_state_dict(state)
            self._model.eval()
            self._mode = "model"
            return True
        except Exception:  # noqa: BLE001
            self._mode = "ewma"
            return False

    def mode(self) -> str:
        return self._mode

    def status(self) -> dict:
        return {"mode": self._mode, "model_loaded": self._model is not None,
                "edge_count": len(self._ewma), "history_len": self.history_len,
                "updates": self._updates, "predictions": self._predictions}
