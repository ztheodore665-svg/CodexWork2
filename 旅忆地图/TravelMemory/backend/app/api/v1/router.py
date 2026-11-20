"""API v1 路由汇总。

阶段 2 只有 Agent 一个业务接口；阶段 3 起会陆续挂上 photos / trips / share，
阶段 6 挂上了 privacy，阶段 7 挂上了 recommend（群体语义推荐）。
阶段 6 本轮挂上了 photos（导入）与 trips（行程列表）。
到时候只改这个文件，不用动 `main.py`。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.photos import router as photos_router
from app.api.v1.privacy import router as privacy_router
from app.api.v1.recommend import router as recommend_router
from app.api.v1.trips import router as trips_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(agent_router)
api_router.include_router(photos_router)
api_router.include_router(trips_router)
api_router.include_router(privacy_router)
api_router.include_router(recommend_router)
