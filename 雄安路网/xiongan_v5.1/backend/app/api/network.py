from fastapi import APIRouter, File, Request, UploadFile

from app.api.common import ok

router = APIRouter(tags=["network"])


@router.get("/network")
async def network(request: Request):
    return ok(request.app.state.runtime.network())


@router.get("/network/summary")
async def network_summary(request: Request):
    return ok(request.app.state.runtime.network_summary())


@router.get("/networks")
async def list_networks(request: Request):
    """内置路网清单（含配套车流/附加文件），供前端路网选择器。"""
    return ok(request.app.state.runtime.list_networks())


@router.get("/networks/preview")
async def network_preview(request: Request, name: str = ""):
    """路网缩略预览 SVG（前端悬停下拉框显示）。"""
    return ok(request.app.state.runtime.network_preview_svg(name))


@router.get("/networks/preview-data")
async def network_preview_data(request: Request, net_path: str = "", name: str = ""):
    """按指定路网导出 GeoJSON（不启动仿真，供页面初始直接展示路网）。"""
    return ok(request.app.state.runtime.network_geojson(net_path=net_path, name=name))


@router.post("/networks/upload")
async def upload_networks(request: Request, files: list[UploadFile] = File(...)):
    """上传自定义 SUMO 路网（.net.xml + 可选 .rou.xml / .add.xml），保存到 data/networks/custom/。"""
    return ok(request.app.state.runtime.upload_network(files))
