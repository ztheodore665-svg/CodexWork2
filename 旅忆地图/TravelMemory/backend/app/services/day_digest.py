"""把照片聚合「日文档」（W1-3 的内容构建部分）。

## 为什么按天聚合，而不是一张照片一条文档

一张照片的信息量太小："2025-10-03 09:12 乌镇，一张船的照片"。
单条文档会导致两个问题：

1. **检索粒度太碎**：问"我去年秋天去过哪些古镇"，命中的是某一张船的照片，
   而不是"那趟江南古镇之行"，回答会缺乏上下文
2. **成本与噪声**：文档数量 ×N，embedding 成本 ×N，且大量近似重复的文本挤占召回位

按天聚合后，一条文档 = "2025 年 10 月 3 日在嘉兴：乌镇（3 张）、西塘（2 张）+ 每张的 caption"，
语义完整、数量可控，正好对应"一天一段记忆"这个自然单位。

设计文档写的是「按 Trip / 天聚合」：
  - Trip / TripItem 表由阶段 6 的规则聚类产生（W6-2），所以**阶段 1 先只做"天"**
  - 阶段 6 之后在这里加一个 TripGrouper 即可，Document 的结构不用变
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.models.photo import Photo


@dataclass
class DayDigest:
    """一天的聚合结果，最终写成一条（或几条，超长时切块）Document。"""

    #: YYYY-MM-DD，作为 Document.source_id
    date: str
    content: str
    metadata: dict = field(default_factory=dict)


def group_photos_by_day(photos: list[Photo]) -> dict[str, list[Photo]]:
    """按「当地时间」的日期分组。

    用 taken_at_local 而不是 taken_at：一天的定义应该以**拍摄地**为准，
    否则跨时区旅行时会把两天的照片混到一天里。
    没有当地时间的照片归入 "unknown" 组，不丢弃（避免静默丢数据）。
    """
    grouped: dict[str, list[Photo]] = {}
    for photo in photos:
        key = (
            photo.taken_at_local.date().isoformat()
            if photo.taken_at_local is not None
            else "unknown"
        )
        grouped.setdefault(key, []).append(photo)
    for items in grouped.values():
        items.sort(key=lambda p: p.taken_at_local or datetime.min)
    return grouped


def build_day_digest(date: str, photos: list[Photo]) -> DayDigest:
    """把一天的照片拼成一段自然语言文本 + 结构化元数据。"""
    if not photos:
        raise ValueError("photos 不能为空")

    # 保证按时间正序：调用方（index_job）查出来的顺序取决于数据库，不能依赖
    photos = sorted(photos, key=lambda p: (p.taken_at_local is None, p.taken_at_local))

    places: list[str] = []
    cities: list[str] = []
    themes: list[str] = []
    lines: list[str] = []

    # 当天的经纬度包围盒：阶段 2 的"空间过滤"直接读它
    lats: list[float] = []
    lngs: list[float] = []

    for photo in photos:
        exif = photo.exif_json or {}
        place = str(exif.get("place") or "")
        city = str(exif.get("city") or "")
        if place and place not in places:
            places.append(place)
        if city and city not in cities:
            cities.append(city)
        for tag in photo.ai_tags or []:
            if tag not in themes:
                themes.append(tag)
        if photo.lat is not None and photo.lng is not None:
            lats.append(photo.lat)
            lngs.append(photo.lng)

        time_part = photo.taken_at_local.strftime("%H:%M") if photo.taken_at_local else "时间未知"
        where = f"{city}{place}".strip() or "途中"
        caption = (photo.caption or "").strip() or "（无描述）"
        lines.append(f"- {time_part} 于{where}：{caption}")

    head = f"{date} 的行程记录"
    if cities or places:
        head += f"（{'、'.join(cities + [p for p in places if p not in cities])}）"
    head += f"，当天共 {len(photos)} 张照片："

    content = "\n".join([head, *lines])
    metadata: dict = {
        "date": date,
        "city": cities[0] if cities else None,
        "cities": cities,
        "places": places,
        "themes": themes,
        "photo_count": len(photos),
        "photo_ids": [str(p.id) for p in photos],
        "doc_kind": "day",
    }
    if lats and lngs:
        # 存 bbox 而不只存中心点：过滤语义是"这片区域有没有我的照片"，
        # 用中心点会漏掉"当天跨度很大"的情形（比如环湖一天走了一大圈）
        metadata.update(
            {
                "min_lat": min(lats),
                "max_lat": max(lats),
                "min_lng": min(lngs),
                "max_lng": max(lngs),
                "center_lat": sum(lats) / len(lats),
                "center_lng": sum(lngs) / len(lngs),
            }
        )
    return DayDigest(date=date, content=content, metadata=metadata)
