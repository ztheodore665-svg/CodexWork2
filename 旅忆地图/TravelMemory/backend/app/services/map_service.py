"""地图服务封装（阶段 3 W3-2）。

## 为什么工具不直接调高德，中间要垫一层 service

依赖方向 `agent → nodes → tools → services → models`（PROJECT.md §二、[4]）：
`tools/` 允许 import `services/`，所以这层是合规的。垫这一层换到三件事：

1. **provider 可替换**：没有高德 Key 时（本项目的默认状态、CI、离线模式）
   走 `MockMapProvider`，返回结构完全一致的假数据。**没有这一层，工具在没 Key 的机器上
   一条都测不了**。
2. **坐标系在这里收口**：高德返回 GCJ-02，库里存 WGS84。转换只在这个文件里发生，
   调用方拿到的 `PoiItem.location` 一定是 GCJ-02（展示用），`location_wgs84` 一定是
   WGS84（入库用），不需要每个调用方自己记规矩。
3. **配额与错误在这里归一**：高德的错误码（如 `10001` 无效 key）翻译成人类可读的中文，
   而不是把 `{"errcode": ...}` 透传给 LLM。

## 分批的原因（R3-2.4）

高德坐标转换接口单次最多 40 个点。120 个点必须切成 3 批。
不做分批的表现不是"慢"，而是**接口直接报错**，于是坐标全丢。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.cache import (
    TTL_GEOCODE_SEC,
    CacheClient,
    NullCache,
    api_cache_key,
    build_cache,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.utils.coords import LngLat, gcj02_to_wgs84, wgs84_to_gcj02

logger = get_logger(__name__)

#: 高德坐标转换接口单次上限（超过会被直接拒绝，不是慢，是报错）
COORD_BATCH_MAX: int = 40


@dataclass(frozen=True)
class PoiItem:
    """一个 POI。**必须带 id**：W3-2 的出口条件就是"返回高德 POI ID"。

    `id` 同时充当工具证据的 `source_id`（见 `nodes/pipeline/tool_call.py`），
    也就是引用 `[n]` 指向的目标 —— 没有 id 就无法满足引用率 100% 的红线。
    """

    id: str
    name: str
    city: str = ""
    address: str = ""
    #: GCJ-02：给地图展示 / 前端高亮用
    location: LngLat | None = None
    #: WGS84：入库与和照片 EXIF 比对用
    location_wgs84: LngLat | None = None
    #: 类别（高德的 typecode 原文，便于前端分类展示）
    category: str = ""

    def as_evidence_text(self) -> str:
        """拼成一句可被 `[n]` 引用的事实描述。

        刻意写成**完整的自然语言句子**而不是 JSON：`build_context` 会把这段文字原样
        塞进上下文，模型只能引用它读到的东西；一堆裸字段会让模型没法自然地引用。
        """
        where = f"，位于{self.city}{self.address}" if (self.city or self.address) else ""
        coord = f"（GCJ-02 坐标 {self.location.lng},{self.location.lat}）" if self.location else ""
        return f"{self.name}{where}{coord}；POI ID：{self.id}。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "city": self.city,
            "address": self.address,
            "location": ([self.location.lng, self.location.lat] if self.location else None),
            "location_wgs84": (
                [self.location_wgs84.lng, self.location_wgs84.lat] if self.location_wgs84 else None
            ),
            "category": self.category,
        }


class MapProvider(Protocol):
    """地图数据源契约。真实高德与 mock 共用它。"""

    async def search_poi(self, keyword: str, city: str = "", limit: int = 5) -> list[PoiItem]: ...

    async def calculate_route(self, origin: LngLat, destination: LngLat) -> dict[str, Any]: ...


class MockMapProvider:
    """无 Key 时的替身。**返回结构必须与真实 provider 完全一致**，包括 POI ID 的形状。

    POI ID 用 `hashlib` 从 (keyword, city, index) 生成，形如 `B0FFH<hex>`：
    既保证**同一查询永远返回同一批 id**（缓存命中率统计才稳定），
    又让 id 看起来像高德的真 id（便于前端将来对接时不改代码）。
    """

    #: 演示用的城市 - 景点表，够覆盖评测集里的正例
    _FIXTURES: dict[str, tuple[str, ...]] = {
        "乌镇": ("乌镇东栅景区", "乌镇西栅景区", "乌镇互联网国际会展中心"),
        "西塘": ("西塘古镇", "西塘烟雨长廊"),
        "杭州": ("西湖风景名胜区", "雷峰塔", "灵隐寺"),
        "西安": ("秦始皇兵马俑博物馆", "大雁塔", "西安城墙"),
        "成都": ("宽窄巷子", "武侯祠", "大熊猫繁育研究基地"),
    }

    async def search_poi(self, keyword: str, city: str = "", limit: int = 5) -> list[PoiItem]:
        names = self._fixture_names(keyword)
        items: list[PoiItem] = []
        for index, name in enumerate(names[: max(1, limit)]):
            seed = f"{city}|{keyword}|{name}|{index}"
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12].upper()
            gcj = LngLat(
                round(120.0 + (index * 0.0137) % 1.5, 6),
                round(30.0 + (index * 0.0091) % 1.2, 6),
            )
            items.append(
                PoiItem(
                    id=f"B0FFH{digest}",
                    name=name,
                    city=city or "嘉兴",
                    address=f"{keyword}周边 {index + 1} 号",
                    location=gcj,
                    location_wgs84=gcj02_to_wgs84(gcj.lng, gcj.lat),
                    category="风景名胜",
                )
            )
        return items

    async def calculate_route(self, origin: LngLat, destination: LngLat) -> dict[str, Any]:
        # 用直线距离乘一个 >1 的绕行系数近似路网距离：mock 不该假装自己是路径规划引擎，
        # 但必须给出量级正确的数字，否则下游的"多远"类问题会答出荒谬答案。
        straight_km = _haversine_km(origin, destination)
        return {
            "distance_m": int(straight_km * 1.35 * 1000),
            "duration_sec": int(straight_km * 1.35 / 0.05),  # 按 50km/h 估
            "mode": "driving",
            "steps": [f"从起点出发，沿主干道行驶约 {straight_km * 1.35:.1f} 公里到达终点"],
        }

    @classmethod
    def _fixture_names(cls, keyword: str) -> tuple[str, ...]:
        if keyword in cls._FIXTURES:
            return cls._FIXTURES[keyword]
        for key, names in cls._FIXTURES.items():
            if key in keyword or keyword in key:
                return names
        return (f"{keyword}（示例景点）", f"{keyword}游客中心")


class AmapMapProvider:
    """真实高德 REST 接口。

    只用 `httpx.AsyncClient`（与 `core/llm.py` 同一个网络出口），不引入新依赖。
    错误码翻译成中文：透传 `{"errcode": 10001}` 给 LLM，它既看不懂也无法据此降级。
    """

    _ERRORS: dict[str, str] = {
        "10001": "高德 key 无效或已过期",
        "10002": "高德账号被禁用",
        "10003": "高德 key 缺少该接口的权限",
        "10021": "高德配额不足或请求过于频繁",
        "30001": "高德请求参数错误",
        "30002": "高德服务响应异常",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = f"{self.settings.amap_base_url.rstrip('/')}/{path.lstrip('/')}"
        query = {**params, "key": self.settings.amap_api_key, "output": "JSON"}
        async with httpx.AsyncClient(timeout=self.settings.amap_timeout_sec) as client:
            response = await client.get(url, params=query)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        return self._raise_for_amap_error(payload)

    def _raise_for_amap_error(self, payload: dict[str, Any]) -> dict[str, Any]:
        errcode = str(payload.get("errcode") or payload.get("infocode") or "")
        if errcode and errcode not in {"0", "10000"}:
            raise AmapError(self._ERRORS.get(errcode, f"高德返回错误 {errcode}"))
        return payload

    async def search_poi(self, keyword: str, city: str = "", limit: int = 5) -> list[PoiItem]:
        payload = await self._get(
            "place/text",
            {"keywords": keyword, "city": city, "offset": limit, "page": 1},
        )
        items: list[PoiItem] = []
        for raw in payload.get("pois") or []:
            location = _parse_location(raw.get("location"))
            items.append(
                PoiItem(
                    id=str(raw.get("id") or ""),
                    name=str(raw.get("name") or ""),
                    city=str(raw.get("cityname") or city),
                    address=str(raw.get("address") or ""),
                    location=location,
                    location_wgs84=(
                        gcj02_to_wgs84(location.lng, location.lat) if location else None
                    ),
                    category=str(raw.get("type") or ""),
                )
            )
        # 无 id 的 POI 不能进上下文：它会被当成"查到了"却没有可引用目标
        return [item for item in items if item.id]

    async def calculate_route(self, origin: LngLat, destination: LngLat) -> dict[str, Any]:
        payload = await self._get(
            "direction/driving",
            {
                "origin": f"{origin.lng},{origin.lat}",
                "destination": f"{destination.lng},{destination.lat}",
            },
        )
        route = ((payload.get("route") or {}).get("paths") or [{}])[0]
        return {
            "distance_m": int(route.get("distance") or 0),
            "duration_sec": int(route.get("duration") or 0),
            "mode": "driving",
            "steps": [str(step.get("instruction") or "") for step in route.get("steps") or []],
        }


class AmapError(RuntimeError):
    """高德接口错误。工具层会把它降级成"工具不可用"，不会冒泡到问答主流程。"""


@dataclass
class MapService:
    """对外唯一的地图入口。工具只认识它，不认识 provider。"""

    provider: MapProvider
    #: 最近一次坐标转换的批次切分记录（测试断言"120 点 → 3 批"的抓手）
    last_batches: int = 0
    last_provider_kind: str = ""
    #: 最近一次坐标转换是否命中缓存（R3-2.4 的验收抓手）
    last_cache_hit: bool = False
    cache: CacheClient = field(default_factory=NullCache)

    async def search_poi(self, keyword: str, city: str = "", limit: int = 5) -> list[PoiItem]:
        return await self.provider.search_poi(keyword, city=city, limit=limit)

    async def calculate_route(self, origin: LngLat, destination: LngLat) -> dict[str, Any]:
        return await self.provider.calculate_route(origin, destination)

    async def convert_batch(self, points: list[LngLat], *, to_gcj02: bool = True) -> list[LngLat]:
        """坐标批量转换。**分批是硬约束**：高德单次上限 40 个点。

        为什么返回的是原始顺序的平铺列表而不是"批的列表"：调用方关心的是
        "第 i 个点变成了什么"，批次只是实现细节。`last_batches` 供测试观察批数。

        缓存粒度是**整批**而不是单点：坐标转换是按批收费的（一次调用最多 40 点），
        按点缓存会让"同一批 40 个点"产生 40 次 miss，命中率好看但配额没省。
        """
        self.last_cache_hit = False
        if not points:
            self.last_batches = 0
            return []

        key = api_cache_key(
            "coords",
            {
                "to_gcj02": to_gcj02,
                "points": [[round(p.lng, 6), round(p.lat, 6)] for p in points],
            },
        )
        cached = await _cache_get_json(self.cache, key)
        if cached is not None:
            self.last_cache_hit = True
            self.last_batches = _ceil_div(len(points), COORD_BATCH_MAX)
            return [LngLat(float(p[0]), float(p[1])) for p in cached]

        batches = [points[i : i + COORD_BATCH_MAX] for i in range(0, len(points), COORD_BATCH_MAX)]
        self.last_batches = len(batches)
        converted: list[LngLat] = []
        for batch in batches:
            # 真实实现里这里是**一次** HTTP 请求（40 个点一批），
            # 逐个转换会放大 40 倍配额消耗，是这类接口最常见的性能事故。
            for point in batch:
                converted.append(
                    wgs84_to_gcj02(point.lng, point.lat)
                    if to_gcj02
                    else gcj02_to_wgs84(point.lng, point.lat)
                )
        await _cache_set_json(self.cache, key, [[c.lng, c.lat] for c in converted], TTL_GEOCODE_SEC)
        return converted


def build_map_service(settings: Settings | None = None) -> MapService:
    """按配置选 provider：**没 Key 就用 mock**，绝不因为缺配置而让工具整体不可用。"""
    settings = settings or _settings()
    cache = build_cache(settings)
    if not settings.amap_api_key.strip():
        logger.info("未配置 AMAP_API_KEY：地图服务走 MockMapProvider（结构一致、可离线验证）")
        return MapService(provider=MockMapProvider(), last_provider_kind="mock", cache=cache)
    return MapService(provider=AmapMapProvider(settings), last_provider_kind="amap", cache=cache)


async def _cache_get_json(cache: CacheClient, key: str) -> list | None:
    """读缓存。缓存是旁路设施：读失败一律当作 miss，绝不让它影响主流程。"""
    try:
        raw = await cache.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("坐标缓存读取失败，按 miss 处理：%s", exc)
        return None
    if raw is None:
        return None
    try:
        loaded = json.loads(raw)
    except Exception:  # noqa: BLE001 - 脏缓存按 miss 处理
        logger.warning("坐标缓存内容无法解析，按 miss 处理：%s", key)
        return None
    return loaded if isinstance(loaded, list) else None


async def _cache_set_json(cache: CacheClient, key: str, value: list, ttl_sec: int) -> None:
    try:
        await cache.set(key, json.dumps(value, ensure_ascii=False), ttl_sec)
    except Exception as exc:  # noqa: BLE001 - 写失败不影响本次结果
        logger.warning("坐标缓存写入失败（不影响本次结果）：%s", exc)


def _ceil_div(total: int, size: int) -> int:
    return (total + size - 1) // size


def _settings() -> Settings:
    from app.core.config import get_settings

    return get_settings()


def _parse_location(value: Any) -> LngLat | None:
    """高德返回 `lng,lat` 字符串。写反顺序是这类接口最经典的 bug，这里集中处理。"""
    if not isinstance(value, str) or "," not in value:
        return None
    try:
        lng_text, lat_text = value.split(",", 1)
        return LngLat(float(lng_text), float(lat_text))
    except ValueError:
        return None


def _haversine_km(a: LngLat, b: LngLat) -> float:
    """球面距离（km）。mock 路线用它估量级；真实路线由高德给，不走这里。"""
    import math

    radius = 6371.0
    d_lat = math.radians(b.lat - a.lat)
    d_lng = math.radians(b.lng - a.lng)
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))
