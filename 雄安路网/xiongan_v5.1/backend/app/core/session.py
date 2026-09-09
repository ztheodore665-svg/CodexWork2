"""仿真会话管理：状态机 + 独立线程推进，供 API 层调用。"""

import threading
import time
import uuid

from app.core.engine import Engine, EngineError


class SessionError(Exception):
    """会话异常。code 对应错误码：1003 状态不允许、1004 配置缺失、1005 启动失败。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Session:
    """管理单个仿真会话的生命周期。仿真在独立线程推进，step_handler 由应用注入。

    状态机：idle→running→paused/stopped/error；paused→running/stopped；stopped/error→idle。
    """

    def __init__(self, engine_factory=None, step_handler=None):
        self._engine_factory = engine_factory or Engine
        self._step_handler = step_handler
        self._lock = threading.Lock()
        self._state = "idle"
        self._session_id: str | None = None
        self._engine = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._speed = 1.0
        self._step = 0
        self._sim_params: dict = {}
        self.scheme = "none"

    # ── 生命周期 ────────────────────────────────────────────

    def start(self, sim_params: dict) -> str:
        with self._lock:
            if self._state != "idle":
                raise SessionError(1003, f"当前状态 {self._state} 不允许启动")
            net_path = sim_params.get("net_path")
            if not net_path:
                raise SessionError(1004, "缺少 net_path")
            engine = self._engine_factory()
            try:
                engine.connect(
                    net_path,
                    sim_params.get("route_files") or [],
                    sim_params.get("add_files") or [],
                    sim_params.get("begin", 0),
                    sim_params.get("end", 86400),
                    sim_params.get("step_length", 1.0),
                )
            except EngineError as exc:
                # 连接失败要关闭可能半开的 traci 连接，避免残留
                # "Connection 'default' is already active" 阻塞后续启动
                try:
                    engine.close()
                except Exception:  # noqa: BLE001 关闭失败不掩盖原始错误
                    pass
                raise SessionError(1006, f"仿真连接失败: {exc.message}") from exc
            # 右转常绿：在仿真线程启动前覆写信号程序（无竞态，跨机器/跨方案通用）
            if sim_params.get("right_turn_green"):
                try:
                    engine.apply_right_turn_always_green()
                except Exception:  # noqa: BLE001 覆写失败不阻塞启动
                    pass
            self._engine = engine
            self._sim_params = sim_params
            self._session_id = uuid.uuid4().hex[:8]
            self._step = 0
            self.scheme = sim_params.get("scheme", "none")
            self._speed = float(sim_params.get("speed", 1.0))
            self._stop_evt.clear()
            self._pause_evt.set() if self._speed == 0 else self._pause_evt.clear()
            self._state = "running"
            # 启动预热（场景一次性投放用）：无头高倍速直接步进——不调用 step_handler、
            # 不向前端推送、不休眠，让车辆散开/形成排队后再启动线程，实现
            # "投放时不渲染、投放完再渲染"。预热失败不阻塞启动（车辆已投放）。
            warmup = int(sim_params.get("warmup") or 0)
            if warmup > 0:
                try:
                    for _ in range(warmup):
                        self._step = engine.step()
                except Exception:  # noqa: BLE001
                    pass
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return self._session_id

    def stop(self) -> None:
        with self._lock:
            if self._state not in ("running", "paused", "error"):
                raise SessionError(1003, f"当前状态 {self._state} 不允许停止")
            self._state = "stopped"
        self._stop_evt.set()
        self._pause_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.close()
                except Exception:  # noqa: BLE001 关闭失败不阻塞
                    pass
            self._engine = None
            self._thread = None
            self._state = "idle"

    def pause(self) -> None:
        with self._lock:
            if self._state != "running":
                raise SessionError(1003, f"当前状态 {self._state} 不允许暂停")
            self._state = "paused"
        self._pause_evt.set()

    def resume(self) -> None:
        with self._lock:
            if self._state != "paused":
                raise SessionError(1003, f"当前状态 {self._state} 不允许恢复")
            self._state = "running"
        self._pause_evt.clear()

    def step_n(self, n: int) -> None:
        with self._lock:
            if self._state != "paused":
                raise SessionError(1003, "仅暂停状态可手动步进")
            if self._engine is None:
                raise SessionError(1004, "仿真未初始化")
            engine = self._engine
        for _ in range(max(1, int(n))):
            self._step = engine.step()
            if self._step_handler is not None:
                self._step_handler(engine, self._step)

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._speed = max(0.0, float(speed))
            if self._speed == 0:
                self._pause_evt.set()
            else:
                self._pause_evt.clear()

    # ── 查询 ────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            return {
                "session_id": self._session_id,
                "state": self._state,
                "step": self._step,
                "sim_time": self._step,
                "scheme": self.scheme,
                "vehicle_count": self._vehicle_count_locked(),
            }

    @property
    def engine(self):
        with self._lock:
            return self._engine

    # ── 内部 ────────────────────────────────────────────────

    def _vehicle_count_locked(self) -> int:
        try:
            if self._engine is not None:
                return len(self._engine.get_vehicle_ids())
        except Exception:  # noqa: BLE001
            pass
        return 0

    def _run(self) -> None:
        try:
            step_length = float(self._sim_params.get("step_length", 1.0))
            while not self._stop_evt.is_set():
                if self._speed <= 0 or self._pause_evt.is_set():
                    self._interruptible_wait(0.05)
                    continue
                t0 = time.perf_counter()
                self._step = self._engine.step()
                if self._step_handler is not None:
                    self._step_handler(self._engine, self._step)
                cost = time.perf_counter() - t0
                sleep = max(0.0, step_length / self._speed - cost)
                self._interruptible_wait(sleep)
        except Exception:  # noqa: BLE001
            with self._lock:
                if self._state in ("running", "paused"):
                    self._state = "error"

    def _interruptible_wait(self, dt: float) -> None:
        deadline = time.perf_counter() + dt
        while time.perf_counter() < deadline:
            if self._stop_evt.is_set():
                return
            time.sleep(0.02)
