from fastapi import APIRouter, Query, Request

from app.api.common import ok

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/realtime")
async def realtime(request: Request):
    return ok(request.app.state.runtime.realtime_metrics())


@router.get("/history")
async def history(request: Request,
                  metric: str = Query(...),
                  start: int = Query(0),
                  end: int = Query(...),
                  interval: int = Query(1),
                  scope: str = Query("overall")):
    return ok(request.app.state.runtime.history_metrics(
        metric, start, end, interval, scope))


@router.get("/edge/{edge_id}")
async def edge_metrics(request: Request, edge_id: str):
    """单条边实时指标（点击道路详情）。"""
    return ok(request.app.state.runtime.edge_stats(edge_id))


@router.get("/spotlight")
async def spotlight(request: Request):
    """自定义指标聚合：完成率、最久行驶车辆、最堵塞道路/路口（实时指标栏轮询）。"""
    return ok(request.app.state.runtime.spotlight())
