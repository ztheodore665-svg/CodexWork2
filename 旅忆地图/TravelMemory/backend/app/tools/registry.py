"""工具注册表（阶段 3 W3-1）。

## 为什么要注册表，而不是在节点里 if/elif

工具选择节点需要三样东西：按名字取工具、列出全部工具给 LLM 选、以及"这个名字不存在时
怎么办"。散落在节点里就是三份 if/elif；注册表把它们收成一个地方。

## 为什么重复注册要报错

`register()` 遇到同名工具抛 `ValueError`，而不是覆盖。覆盖的语义是"后注册的赢"，
于是工具的行为取决于**import 顺序** —— 那种 bug 只在某台机器上复现，极难查。
名字冲突应当在启动时炸出来。
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)


class ToolRegistry:
    """工具容器。进程内唯一（由 `build_default_registry` 构造）。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> BaseTool:
        if not tool.name:
            raise ValueError("工具必须有 name")
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册：{tool.name}（同名覆盖会让行为依赖 import 顺序）")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        """注册顺序即展示顺序（prompt 里的顺序会影响 LLM 的选择偏好，保持确定性）。"""
        return list(self._tools)

    def describe(self) -> list[dict]:
        """给 LLM 的工具清单（`{"name","description","parameters"}`）。"""
        return [tool.schema() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


def build_default_registry(context=None) -> ToolRegistry:
    """装配阶段 3 的全部工具。

    集中在一处有两个好处：
      1. 节点不需要 import 具体工具类（少一条依赖边）
      2. 加新工具只改这一个函数，不用动调用方
    """
    from app.core.config import get_settings
    from app.services.map_service import build_map_service
    from app.tools.base import ToolContext
    from app.tools.poi_tool import SearchPoiTool
    from app.tools.route_tool import CalculateRouteTool

    settings = get_settings()
    ctx = context or ToolContext(
        timeout_sec=settings.tool_timeout_sec,
        max_retries=settings.tool_max_retries,
    )
    service = build_map_service(settings)

    registry = ToolRegistry()
    registry.register(SearchPoiTool(service, ctx))
    registry.register(CalculateRouteTool(service, ctx))
    logger.info("已注册 %d 个工具：%s", len(registry), ", ".join(registry.names()))
    return registry
