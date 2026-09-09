from fastapi import APIRouter, Body, Request

from app.api.common import ok

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


@router.get("/score")
async def score_live(request: Request):
    """实时综合评分（老评价平台分层聚合公式，基于当前仿真指标）。"""
    return ok(request.app.state.runtime.evaluate_score())


@router.post("/score")
async def score_custom(request: Request, body: dict = Body(default={})):
    """评分（可传 overall/intersections 覆盖实时数据；不传则用实时指标）。"""
    return ok(request.app.state.runtime.evaluate_score(
        body.get("overall"), body.get("intersections")))
