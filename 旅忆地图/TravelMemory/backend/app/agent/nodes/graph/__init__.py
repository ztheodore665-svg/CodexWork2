"""阶段 4 W4-2：LangGraph 图运行时。

| 文件 | 职责 |
| --- | --- |
| `state.py` | `AgentState` 定义（含 messages 滑动窗口的 reducer） |
| `graph.py` | `LangGraphAgent`：节点复用 + 条件边 + 与管道一致的事件序列 |

节点**刻意复用** `nodes/pipeline/*` 的同一批对象，而不是另写一份 ——
详见 `graph.py` 的模块文档。
"""

from app.agent.nodes.graph.graph import MAX_GENERATE_RETRIES, LangGraphAgent
from app.agent.nodes.graph.state import SLIDING_WINDOW_TURNS, AgentState, initial_state

__all__ = [
    "MAX_GENERATE_RETRIES",
    "SLIDING_WINDOW_TURNS",
    "AgentState",
    "LangGraphAgent",
    "initial_state",
]
