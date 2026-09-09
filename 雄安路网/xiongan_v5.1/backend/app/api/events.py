from fastapi import APIRouter, Body, Request

from app.api.common import ok

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/inject")
async def inject(request: Request, body: dict = Body(...)):
    return ok(request.app.state.runtime.inject_event(
        body.get("event_type", ""), body.get("params") or {}))


@router.get("/list")
async def list_events(request: Request):
    return ok(request.app.state.runtime.list_events())
