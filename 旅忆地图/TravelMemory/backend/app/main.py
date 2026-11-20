"""FastAPI 应用壳。

阶段 0 只留健康检查（业务入口是 `python -m app.cli`）；
阶段 2（W2-3）挂上 `/api/v1/agent/chat` 的 SSE 流式问答。

**CLI 与 HTTP 共用同一条 `AgentPipeline`**：CLI 用来离线验证与评测，
HTTP 用来给前端用。两条入口走同一份编排代码，评测结论才对得上线上行为。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.prompts.loader import PROMPT_VERSION
from app.api.v1.router import api_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="旅忆地图 TravelMemory",
    description="以个人照片地图记忆为数据源的旅行记忆 Agent（阶段 16：门禁与真相）",
    version="0.16.0",
)

# 前端 dev server 在 5173，后端在 8000：开发期必须允许跨域。
# 生产由 Nginx 同域反代，这条规则不会被用到。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", summary="健康检查", tags=["ops"])
async def health() -> dict:
    """存活探测。刻意不查数据库：容器编排需要一个轻量探针。"""
    return {
        "status": "ok",
        "stage": 16,
        "runtime": settings.agent_runtime,
        "prompt_version": PROMPT_VERSION,
    }
