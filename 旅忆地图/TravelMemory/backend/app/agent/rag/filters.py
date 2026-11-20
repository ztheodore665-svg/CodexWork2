"""结构化过滤条件（W2-1 / W2-2 之间的"接头"）。

## 这一层解决什么问题

`query_parse` 节点产出的是**自然语言理解的结果**（"去年秋天"、"古镇"、"杭州"），
`DocumentService` 需要的是**SQL 能执行的条件**（date_start / date_end / 名字列表）。
两者之间必须有明确的契约，否则：

- 过滤逻辑散落在 SQL 里 → 无法单测，改一处要连着改 SQL 字符串
- 无法回答"这次检索到底加了什么过滤条件" → 出了问题没法回溯

所以这里定义一个**纯数据类** `StructuredFilter`，它是"解析结果 → SQL"的唯一中间表示。
纯数据类意味着：可以单测、可以打印、可以原样写进 trace。

## 三个反直觉的设计点

1. **`is_empty()` 比"加了条件"更重要** —— 没有任何条件时必须明确走"不过滤"分支，
   否则 `WHERE 1=1` 之类的兜底会悄悄变成全表扫描。
2. **时间用"区间相交"而不是"落在区间内"** —— 一趟行程是 2025-10-02~10-05，
   用户问"10 月 3 日"，用"落在区间内"会查不到它，必须用相交：
   `doc_start <= 查询end AND doc_end >= 查询start`。
3. **`describe()` 是为了 trace 而生的** —— 过滤是把双刃剑，加错了直接漏召回。
   把实际生效的条件写进 trace，才能事后回答"这条证据为什么没被召回"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class BBox:
    """经纬度包围盒（WGS84）。

    字段顺序是 (min_lng, min_lat, max_lng, max_lat) —— 与 GeoJSON 的 bbox 一致，
    避免和 (lat, lng) 的口语顺序混淆。
    """

    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float


@dataclass
class StructuredFilter:
    """一次检索要套的结构化条件。全部为空 = 不过滤。

    `bbox` 刻意不由 query_parse 产生：从"杭州"这种地名到经纬度需要地理编码
    （阶段 3 的 `search_poi` / 高德 API），阶段 2 只有工具显式传入时才用。
    """

    #: 时间窗（闭区间）。与文档的 date_start/date_end 做**相交**判断
    date_start: date | None = None
    date_end: date | None = None
    #: 地名（城市或景区），命中 metadata 里的 city / cities / places 任一个即可
    places: list[str] = field(default_factory=list)
    #: 主题词，命中 metadata 里的 themes
    themes: list[str] = field(default_factory=list)
    #: 空间包围盒，可选
    bbox: BBox | None = None

    def is_empty(self) -> bool:
        return (
            self.date_start is None
            and self.date_end is None
            and not self.places
            and not self.themes
            and self.bbox is None
        )

    def describe(self) -> dict:
        """给 trace / 日志用的可读摘要（只列生效的条件）。"""
        out: dict = {}
        if self.date_start or self.date_end:
            out["date"] = [
                self.date_start.isoformat() if self.date_start else "",
                self.date_end.isoformat() if self.date_end else "",
            ]
        if self.places:
            out["places"] = self.places
        if self.themes:
            out["themes"] = self.themes
        if self.bbox is not None:
            out["bbox"] = [
                self.bbox.min_lng,
                self.bbox.min_lat,
                self.bbox.max_lng,
                self.bbox.max_lat,
            ]
        return out


def bbox_from_center(lat: float, lng: float, radius_km: float) -> BBox:
    """由中心点 + 半径粗略生成包围盒。

    为什么不用 PostGIS 的 `ST_DWithin`：本阶段 Document 上没有 geometry 列，
    加列要动迁移且暂时用不上真正的空间运算。包围盒是**矩形近似**，
    在城市尺度（几公里）误差可忽略；真需要精确距离是阶段 6 的事。

    纬度 1° ≈ 111km；经度 1° ≈ 111km × cos(lat)，所以高纬度地区经度方向要放大。
    """
    import math

    lat_delta = radius_km / 111.0
    cos_lat = max(math.cos(math.radians(lat)), 0.01)  # 极地附近防止除零
    lng_delta = radius_km / (111.0 * cos_lat)
    return BBox(
        min_lng=lng - lng_delta,
        min_lat=lat - lat_delta,
        max_lng=lng + lng_delta,
        max_lat=lat + lat_delta,
    )
