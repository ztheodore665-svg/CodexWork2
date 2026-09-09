"""标准化算法接口层（MCP-like 契约 + 适配器）。"""

from app.algorithms.base import AlgorithmSpec, ParamSpec
from app.algorithms.adapter import AlgorithmAdapter, get_adapter

__all__ = ["AlgorithmSpec", "ParamSpec", "AlgorithmAdapter", "get_adapter"]
