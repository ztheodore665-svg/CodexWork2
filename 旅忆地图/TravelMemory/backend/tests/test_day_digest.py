"""日文档聚合测试（纯函数，不需要数据库）。

重点验证三件事：按当地时间分天、文档正文可读、元数据带上后续过滤需要的字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.photo import AI_DONE, Photo
from app.services.day_digest import build_day_digest, group_photos_by_day


def _photo(hour: int, place: str, city: str = "嘉兴", tags: list[str] | None = None) -> Photo:
    """构造一个只填必要字段的 Photo 对象（不入库，纯内存对象）。"""
    return Photo(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform="pytest",
        asset_id=f"pytest-{hour}-{place}",
        taken_at_local=datetime(2025, 10, 3, hour, 0),
        exif_json={"city": city, "place": place},
        caption=f"{place} 的照片说明",
        ai_tags=tags or ["古镇"],
        ai_status=AI_DONE,
    )


def test_group_by_local_date():
    photos = [_photo(9, "乌镇"), _photo(14, "西塘"), _photo(10, "乌镇")]
    grouped = group_photos_by_day(photos)
    assert list(grouped) == ["2025-10-03"]
    assert len(grouped["2025-10-03"]) == 3


def test_photos_without_time_go_to_unknown_group():
    p = _photo(9, "乌镇")
    p.taken_at_local = None
    grouped = group_photos_by_day([p])
    assert "unknown" in grouped, "没有时间的照片不能被静默丢弃"


def test_digest_content_is_time_ordered():
    digest = build_day_digest("2025-10-03", [_photo(19, "西塘"), _photo(9, "乌镇")])
    pos_morning = digest.content.index("09:00")
    pos_evening = digest.content.index("19:00")
    assert pos_morning < pos_evening, "正文必须按时间正序"


def test_digest_metadata_carries_filter_fields():
    digest = build_day_digest("2025-10-03", [_photo(9, "乌镇"), _photo(14, "乌镇")])
    assert digest.metadata["date"] == "2025-10-03"
    assert digest.metadata["city"] == "嘉兴"
    assert digest.metadata["places"] == ["乌镇"]
    assert digest.metadata["photo_count"] == 2
    assert digest.metadata["doc_kind"] == "day"


def test_digest_requires_photos():
    try:
        build_day_digest("2025-10-03", [])
    except ValueError:
        return
    raise AssertionError("空照片列表应当报错")
