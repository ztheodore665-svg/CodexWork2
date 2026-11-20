"""calculate_route：算路线（阶段 3 W3-2）。

## 为什么入参是 WGS84 而高德要 GCJ-02

库里存的是 WGS84（PROJECT.md §二、[2]），高德收的是 GCJ-02。
转换必须发生，且**只能发生在 service 层**（`MapService.convert_batch`）——
工具不自己算坐标，否则同一个转换逻辑会在三个工具里各写一遍、各自漂移。

## 为什么路线只缓存 1 天而 POI 缓存 7 天

路况会变。同一个起终点的"最佳路线"在一周内可能因为施工/限行完全不同，
缓存 7 天就会给出过期的建议。POI 的名称地址则稳定得多。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.cache import TTL_ROUTE_SEC
from app.services.map_service import MapService
from app.tools.base import BaseTool, ToolContext
from app.utils.coords import LngLat


class CalculateRouteInput(BaseModel):
    """起终点。**单位与坐标系写进字段名**：`lng_wgs84` 而不是 `lng`。

    坐标 bug 的一个大类是"传进来的到底是哪个坐标系"；把坐标系写进字段名，
    调用方在填参数时就不得不确认一次。
    """

    model_config = ConfigDict(frozen=True)

    origin_lng_wgs84: float = Field(description="起点经度（WGS84）")
    origin_lat_wgs84: float = Field(description="起点纬度（WGS84）")
    dest_lng_wgs84: float = Field(description="终点经度（WGS84）")
    dest_lat_wgs84: float = Field(description="终点纬度（WGS84）")
    mode: str = Field(default="driving", description="driving / walking / transit")


class CalculateRouteTool(BaseTool[CalculateRouteInput]):
    """算路线。返回距离、预计时长与简化的步骤文字。"""

    name = "calculate_route"
    description = "计算两地之间的驾车/步行路线，返回距离与预计时长"
    input_model = CalculateRouteInput
    cache_namespace = "route"
    cache_ttl_sec = TTL_ROUTE_SEC

    def __init__(self, service: MapService, context: ToolContext | None = None) -> None:
        super().__init__(context)
        self.service = service

    async def _run(self, validated: CalculateRouteInput) -> dict:
        origin_wgs84 = LngLat(validated.origin_lng_wgs84, validated.origin_lat_wgs84)
        dest_wgs84 = LngLat(validated.dest_lng_wgs84, validated.dest_lat_wgs84)

        # 两个点一批：走的是同一条转换通道，也就共享同一份缓存
        converted = await self.service.convert_batch([origin_wgs84, dest_wgs84], to_gcj02=True)
        if len(converted) != 2:  # pragma: no cover - 防御：转换结果不该变长变短
            raise ValueError("坐标转换返回数量不符")

        route = await self.service.calculate_route(converted[0], converted[1])
        distance_km = int(route.get("distance_m") or 0) / 1000
        minutes = max(1, int(route.get("duration_sec") or 0) // 60)
        steps = [str(step) for step in route.get("steps") or [] if step]

        return {
            "text": (
                f"从起点到终点驾车约 {distance_km:.1f} 公里，"
                f"预计 {minutes} 分钟"
                f"（{validated.mode}）。" + (f"主要途经：{steps[0]}。" if steps else "")
            ),
            "distance_m": int(route.get("distance_m") or 0),
            "duration_sec": int(route.get("duration_sec") or 0),
            "mode": validated.mode,
            "steps": steps,
            "cache_hit_coords": self.service.last_cache_hit,
        }
