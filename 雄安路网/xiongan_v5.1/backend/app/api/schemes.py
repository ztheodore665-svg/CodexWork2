from fastapi import APIRouter, Body, Request

from app.api.common import ok

router = APIRouter(prefix="/schemes", tags=["schemes"])


@router.get("")
async def list_schemes(request: Request):
    return ok(request.app.state.runtime.list_schemes())


@router.post("/{scheme_id}/config")
async def scheme_config(request: Request, scheme_id: str, body: dict = Body(...)):
    return ok(request.app.state.runtime.scheme_config(
        scheme_id, body.get("action", ""), body.get("params") or {}))


@router.get("/{scheme_id}/status")
async def scheme_status(request: Request, scheme_id: str):
    return ok(request.app.state.runtime.scheme_status(scheme_id))
