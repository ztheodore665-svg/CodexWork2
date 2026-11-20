"""环节节点集合。

阶段 0~2 是六个环节；阶段 3 插入 `ToolCallNode`（位置在 retrieve 之后、
build_context 之前），链路变成七个。
"""

from app.agent.nodes.pipeline.build_context import BuildContextNode
from app.agent.nodes.pipeline.generate import GenerateNode
from app.agent.nodes.pipeline.query_parse import QueryParseNode
from app.agent.nodes.pipeline.retrieve import RetrieveNode
from app.agent.nodes.pipeline.rewrite import RewriteNode
from app.agent.nodes.pipeline.self_check import SelfCheckNode
from app.agent.nodes.pipeline.tool_call import ToolCallNode

__all__ = [
    "BuildContextNode",
    "GenerateNode",
    "QueryParseNode",
    "RetrieveNode",
    "RewriteNode",
    "SelfCheckNode",
    "ToolCallNode",
]
