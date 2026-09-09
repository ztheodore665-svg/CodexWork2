"""算法基类：所有调度方案统一生命周期，支持插件化注册。"""

from typing import Any, Callable


class SchemeContext:
    """算法运行时上下文：引擎、信号灯/车辆封装、方案配置、事件推送。"""

    def __init__(self, engine, config: dict | None = None,
                 tls_controller: Any = None, vehicle_tracker: Any = None,
                 push_event: Callable[[str, str, dict], None] | None = None):
        self.engine = engine
        self.config = config or {}
        self.tls_controller = tls_controller
        self.vehicle_tracker = vehicle_tracker
        self.push_event = push_event or (lambda event_type, message, details=None: None)


class BaseScheme:
    """调度方案基类。第三方算法实现 init/on_step/on_metrics/handle_action/cleanup 后注册即可接入。"""

    name: str = "base"

    def __init__(self, ctx: SchemeContext):
        self.ctx = ctx

    def init(self) -> None:
        """仿真启动后、第一步之前调用。"""

    def on_step(self) -> None:
        """每个仿真步调用：采集数据、做决策、应用控制。"""

    def on_metrics(self) -> None:
        """指标快照产生时（每 60 步）调用，记录算法效果。"""

    def handle_action(self, action: str, params: dict) -> dict:
        """处理外部配置动作，返回结果 dict。"""
        return {"ok": False, "message": f"未知动作: {action}"}

    def cleanup(self) -> None:
        """仿真停止时释放资源。"""
