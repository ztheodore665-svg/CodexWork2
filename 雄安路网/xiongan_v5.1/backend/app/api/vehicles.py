from fastapi import APIRouter, Request

from app.api.common import ok

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("/{veh_id}")
async def vehicle_detail(request: Request, veh_id: str):
    """单辆车实时详情（位置/速度/车道/路线，点击车辆用）。"""
    return ok(request.app.state.runtime.vehicle_detail(veh_id))
