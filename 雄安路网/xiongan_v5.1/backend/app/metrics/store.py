"""历史指标存储：环形缓冲 + 按时间范围/间隔查询。"""

from collections import defaultdict, deque


class MetricsStore:
    SUPPORTED = {"avg_delay", "avg_speed", "throughput", "queue_length",
                 "fuel", "co2"}

    def __init__(self, capacity: int = 10000):
        self._capacity = capacity
        # scope -> metric -> deque[{"step","value"}]
        self._series: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=capacity)))

    def append(self, step: int, metric: str, value: float, scope: str = "overall") -> None:
        if metric not in self.SUPPORTED:
            raise ValueError(f"不支持的指标: {metric}")
        self._series[scope][metric].append({"step": step, "value": value})

    def query(self, metric: str, start_step: int, end_step: int,
              interval: int = 1, scope: str = "overall") -> list[dict]:
        if metric not in self.SUPPORTED:
            raise ValueError(f"不支持的指标: {metric}")
        rows = list(self._series[scope].get(metric, []))
        rows = [r for r in rows if r["step"] >= start_step]
        # end_step <= 0 表示不设上界（取全部历史；前端默认传 end=0 表示"到最新"）
        if end_step > 0:
            rows = [r for r in rows if r["step"] <= end_step]
        if interval > 1:
            rows = [r for i, r in enumerate(rows) if i % interval == 0]
        return rows

    def latest(self, metric: str, scope: str = "overall") -> float | None:
        rows = self._series[scope].get(metric)
        if rows:
            return rows[-1]["value"]
        return None
