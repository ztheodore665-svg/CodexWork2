"""阶段 4 W4-1：三层记忆。

模块划分（依赖方向 `agent → tools → services → models`，本包只在 agent 层内）：

| 文件 | 职责 |
| --- | --- |
| `utils/decay.py` | 衰减**纯函数**（`w0 · exp(-λ·Δt)`、半衰期换算、归档判定） |
| `extract.py` | 从用户原话里规则抽取偏好 + 会话摘要时机判定 |
| `store.py` | `MemoryStorePort` 的内存实现（无数据库时也能跑通管道） |
| `service.py` | `MemoryService`：读（组装注入区块）/ 写（三条触发时机） |
"""

from app.agent.memory.extract import extract_preference, should_summarize_session
from app.agent.memory.service import MemoryService
from app.agent.memory.store import InMemoryMemoryStore
from app.utils.decay import (
    ARCHIVE_THRESHOLD,
    DEFAULT_HALF_LIFE_DAYS,
    PREFERENCE_HALF_LIFE_DAYS,
    current_weight,
    days_since,
    decay_weight,
    half_life_to_lambda,
    is_archived,
    lambda_to_half_life,
)

__all__ = [
    "ARCHIVE_THRESHOLD",
    "DEFAULT_HALF_LIFE_DAYS",
    "PREFERENCE_HALF_LIFE_DAYS",
    "InMemoryMemoryStore",
    "MemoryService",
    "current_weight",
    "days_since",
    "decay_weight",
    "extract_preference",
    "half_life_to_lambda",
    "is_archived",
    "lambda_to_half_life",
    "should_summarize_session",
]
