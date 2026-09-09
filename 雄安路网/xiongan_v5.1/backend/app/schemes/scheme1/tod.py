"""TOD 时段调度器：按时段切换配时，支持跨午夜与手动强制。"""

from typing import Optional

DEFAULT_PERIODS = [
    {"name": "morning_peak", "start": "07:00", "end": "09:00",
     "cycle_range": (60, 120), "green_wave": True},
    {"name": "off_peak", "start": "09:00", "end": "17:00",
     "cycle_range": (80, 150), "green_wave": False},
    {"name": "evening_peak", "start": "17:00", "end": "19:00",
     "cycle_range": (60, 120), "green_wave": True},
    {"name": "night", "start": "19:00", "end": "07:00",
     "cycle_range": (90, 180), "green_wave": False},
]


def _to_seconds(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m)


class TODSchedule:
    def __init__(self, periods: Optional[list[dict]] = None):
        self._periods = periods or DEFAULT_PERIODS
        self._forced: Optional[str] = None

    # ── 查询 ────────────────────────────────────────────────

    def get_period(self, sim_time_seconds: int) -> str:
        if self._forced:
            return self._forced
        t = int(sim_time_seconds) % 86400
        for p in self._periods:
            s, e = _to_seconds(p["start"]), _to_seconds(p["end"])
            if s <= e:
                if s <= t < e:
                    return p["name"]
            elif t >= s or t < e:  # 跨午夜（如 19:00-07:00）
                return p["name"]
        return self._periods[0]["name"]

    def get_period_info(self, name: str) -> dict:
        for p in self._periods:
            if p["name"] == name:
                return p
        return self._periods[0]

    def get_cycle_range(self, name: str) -> tuple[int, int]:
        return self.get_period_info(name)["cycle_range"]

    def green_wave_enabled(self, name: str) -> bool:
        return bool(self.get_period_info(name)["green_wave"])

    # ── 过渡检测 ────────────────────────────────────────────

    def transition_detected(self, sim_time_seconds: int) -> bool:
        cur = self.get_period(sim_time_seconds)
        prev = getattr(self, "_last_period", None)
        self._last_period = cur
        return prev is not None and prev != cur

    # ── 强制切换 ────────────────────────────────────────────

    def force(self, plan_name: str) -> None:
        if plan_name not in {p["name"] for p in self._periods}:
            raise ValueError(f"未知时段: {plan_name}")
        self._forced = plan_name

    def release_force(self) -> None:
        self._forced = None

    def is_forced(self) -> bool:
        return self._forced is not None
