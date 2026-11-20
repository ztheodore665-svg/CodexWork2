"""模型包。

集中导出有两个作用：
  1. Alembic env.py 只需 `import app.models` 就能让所有表注册进 metadata
  2. 业务代码统一 `from app.models import Document`，不需要关心文件划分
"""

from app.models.base import Base
from app.models.document import Document
from app.models.eval import EvalCase, EvalResult
from app.models.jobs import IngestJob
from app.models.memory import MemoryItem, MemorySource
from app.models.photo import Photo
from app.models.traces import AgentRun, AgentStep, ToolCallLog
from app.models.trip import Trip, TripItem
from app.models.user import User

__all__ = [
    "AgentRun",
    "AgentStep",
    "Base",
    "Document",
    "EvalCase",
    "EvalResult",
    "IngestJob",
    "MemoryItem",
    "MemorySource",
    "Photo",
    "ToolCallLog",
    "Trip",
    "TripItem",
    "User",
]
