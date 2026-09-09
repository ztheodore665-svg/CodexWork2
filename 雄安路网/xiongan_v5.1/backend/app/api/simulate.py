from fastapi import APIRouter, Body, Request

from app.api.common import ok

router = APIRouter(prefix="/simulate", tags=["simulate"])


@router.post("/start")
async def start(request: Request, params: dict = Body(...)):
    return ok(request.app.state.runtime.start(params))


@router.post("/stop")
async def stop(request: Request):
    return ok(request.app.state.runtime.stop())


@router.post("/pause")
async def pause(request: Request):
    return ok(request.app.state.runtime.pause())


@router.post("/resume")
async def resume(request: Request):
    return ok(request.app.state.runtime.resume())


@router.post("/step")
async def step_n(request: Request, body: dict = Body(default={"steps": 1})):
    return ok(request.app.state.runtime.step_n(int(body.get("steps", 1))))


@router.post("/speed")
async def set_speed(request: Request, body: dict = Body(...)):
    return ok(request.app.state.runtime.set_speed(float(body.get("speed", 1.0))))


@router.post("/right-turn-green")
async def set_right_turn_green(request: Request, body: dict = Body(...)):
    """右转常绿开关（运行中实时生效，不要求重启）。"""
    return ok(request.app.state.runtime.set_right_turn_green(
        bool(body.get("enabled", False))))


@router.get("/scenarios")
async def scenarios(request: Request):
    """可选交通场景清单（前端右上角下拉）。"""
    return ok(request.app.state.runtime.list_scenarios())


@router.get("/status")
async def status(request: Request):
    return ok(request.app.state.runtime.status())
