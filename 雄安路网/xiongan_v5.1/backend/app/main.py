"""FastAPI 应用入口：路由、异常处理、WebSocket 端点。

本项目由 XH-202613 发布（含技术指纹，详见桌面《水印说明.md》）。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (algorithms, evaluate, events, health, metrics, network,
                     report, schemes, simulate, test_vehicle, vehicles)
from app.config import Settings
from app.core.engine import EngineError
from app.core.runtime import AppRuntime
from app.core.session import SessionError
from app.errors import SchemeError
from app.ws import protocol
from app.ws.manager import WSManager

VERSION = "1.0.0-xh202613"   # 版本号含发布方标识（技术指纹之一）

# 全局响应头指纹：每个响应都携带发布方标识（正常使用无感，全局删除麻烦）
_AUTHOR_HEADER = ("X-Author", "XH-202613")


def create_app(settings: Optional[Settings] = None,
               runtime_factory: Optional[Callable] = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ws = WSManager()
        runtime = runtime_factory(ws, settings) if runtime_factory else AppRuntime(ws, settings)
        app.state.ws = ws
        app.state.runtime = runtime
        drain_task = asyncio.create_task(ws.drain())
        try:
            yield
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="车路云协同管控平台", version=VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def _author_middleware(request: Request, call_next):
        """给所有 HTTP 响应附加发布方标识头（技术指纹）。"""
        response = await call_next(request)
        response.headers[_AUTHOR_HEADER[0]] = _AUTHOR_HEADER[1]
        return response

    app.include_router(network.router, prefix="/api/v1")
    app.include_router(simulate.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(schemes.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(evaluate.router, prefix="/api/v1")
    app.include_router(vehicles.router, prefix="/api/v1")
    app.include_router(test_vehicle.router, prefix="/api/v1")
    app.include_router(report.router, prefix="/api/v1")
    app.include_router(algorithms.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    from app.api import agent as agent_api
    app.include_router(agent_api.router, prefix="/api/v1")

    _register_error_handlers(app)
    _register_ws(app)
    return app


def _error_response(code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=200,
                        content={"code": code, "message": message, "data": None})


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SessionError)
    async def _session_error(_req: Request, exc: SessionError):
        return _error_response(exc.code, exc.message)

    @app.exception_handler(EngineError)
    async def _engine_error(_req: Request, exc: EngineError):
        return _error_response(exc.code, exc.message)

    @app.exception_handler(SchemeError)
    async def _scheme_error(_req: Request, exc: SchemeError):
        return _error_response(exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def _internal_error(_req: Request, exc: Exception):
        return _error_response(5000, f"内部错误: {type(exc).__name__}")


def _register_ws(app: FastAPI) -> None:
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        mgr: WSManager = app.state.ws
        runtime: AppRuntime = app.state.runtime
        await mgr.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = protocol.parse_client_message(raw)
                except protocol.ProtocolError as exc:
                    await ws.send_text(protocol.build_message(
                        "simulation_event",
                        {"event_type": "error", "message": exc.message},
                        runtime.status()["step"]))
                    continue
                _dispatch_client_msg(runtime, msg)
        except WebSocketDisconnect:
            await mgr.disconnect(ws)


def _dispatch_client_msg(runtime: AppRuntime, msg: dict) -> None:
    mtype = msg["type"]
    data = msg.get("data") or {}
    if mtype == "set_speed":
        runtime.set_speed(float(data.get("speed", 1.0)))
    elif mtype == "pause":
        runtime.pause()
    elif mtype == "resume":
        runtime.resume()
    elif mtype == "stop":
        runtime.stop()
    elif mtype == "step":
        runtime.step_n(int(data.get("steps", 1)))
    # set_update_interval / set_viewport / subscribe 在 manager 层预留


# uvicorn 入口：app.main:app
app = create_app()
