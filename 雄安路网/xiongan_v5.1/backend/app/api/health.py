from fastapi import APIRouter, Request

from app.api.common import ok

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    return ok(request.app.state.runtime.health())
