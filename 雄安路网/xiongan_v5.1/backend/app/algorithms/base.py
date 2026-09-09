"""标准化算法接口（MCP-like 契约层）。

设计目标：让"红绿灯调度算法"像 MCP 工具一样可被发现、可被配置、可被观测。

- 算法侧（provider）：算法实现只需声明 参数(ParamSpec) / 观测(observables) / 指标(metrics) /
  能力(capabilities)，并实现标准动作 handle_algorithm_action，其余由适配器提供。
- 前端侧（client）：统一 REST 端点（见 app/api/algorithms.py）：
    GET    /algorithms          工具清单（tools/list）
    GET    /algorithms/{id}     schema（tools/get：参数/观测/指标/能力声明）
    GET    /algorithms/{id}/state   运行状态（当前参数值 + 观测快照 + 内部指标）
    POST   /algorithms/{id}/config  设置参数（校验后应用）
    POST   /algorithms/{id}/action  通用动作调用（tools/call）

观测键为框架统一采集口径，任何算法声明需要哪些即注入哪些，保证可比性。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamSpec:
    """算法参数声明（框架据此自动生成前端表单与校验护栏）。"""
    key: str
    label: str                       # 中文名
    type: str = "number"             # number | string | enum | bool
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    enum: list | None = None
    unit: str = ""
    desc: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "type": self.type,
            "default": self.default, "minimum": self.minimum,
            "maximum": self.maximum, "enum": self.enum,
            "unit": self.unit, "desc": self.desc,
        }


@dataclass
class AlgorithmSpec:
    """算法的标准声明（挂在 BaseScheme 子类的 algorithm_spec 上）。"""
    kind: str                        # signal | vehicle | baseline
    description: str
    params: list[ParamSpec] = field(default_factory=list)
    observables: list[str] = field(default_factory=list)   # 框架统一采集的观测键
    metrics: list[str] = field(default_factory=list)       # 算法内部指标键
    capabilities: list[str] = field(default_factory=list)  # 支持的通用动作

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "description": self.description,
            "params": [p.to_dict() for p in self.params],
            "observables": self.observables,
            "metrics": self.metrics,
            "capabilities": self.capabilities,
        }


# ── 框架统一观测键（口径固定，跨算法可比） ──────────────────

# 全局标量
OBS_GLOBAL = [
    "vehicle_count",      # 在网车辆数
    "avg_speed",          # 平均速度 m/s
    "avg_waiting",        # 平均等待 s
    "avg_queue",          # 平均排队（辆/有车边）
    "total_throughput",   # 累计到达
    "completion_rate",    # 完成率（到达/出发）
    "departed",           # 累计出发
]

# 路网级结构数据
OBS_STRUCTURED = [
    "edge_flows",         # {edge_id: 当前车辆数}
    "edge_queues",        # {edge_id: 排队车辆数（低速）}
    "tls_queues",         # {tls_id: 进口排队数}
    "tls_waiting",        # {tls_id: 平均等待 s}
    "tls_phases",         # {tls_id: 当前相位索引}
    "congestion",         # 全局拥堵度 0~1（排队/在网）
]
