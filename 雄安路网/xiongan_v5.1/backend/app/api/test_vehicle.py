from fastapi import APIRouter, Body, Request

from app.api.common import ok

router = APIRouter(prefix="/test-vehicle", tags=["test-vehicle"])


@router.post("/start")
async def test_vehicle_start(request: Request, body: dict = Body(...)):
    """加入一辆测试车：{from_edge, to_edge, via?: [途径边]}，返回路线。"""
    return ok(request.app.state.runtime.start_test_vehicle(
        body.get("from_edge", ""), body.get("to_edge", ""),
        body.get("via") or []))


@router.get("/status")
async def test_vehicle_status(request: Request):
    """测试车辆状态（含逐边等待统计，到达后展示）。"""
    return ok(request.app.state.runtime.test_vehicle_status())
