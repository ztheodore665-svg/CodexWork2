"""结构化过滤契约（W2-1 → W2-2 的接头）。

这组用例的价值在于：过滤条件是"漏召回"的头号嫌疑，
把它做成纯数据类之后，**不用数据库就能断言它的行为**。
"""

from __future__ import annotations

from datetime import date

from app.agent.rag.filters import BBox, StructuredFilter, bbox_from_center
from app.agent.rag.retriever import Retriever
from app.agent.state import ParsedQuery


def test_empty_filter_is_empty() -> None:
    assert StructuredFilter().is_empty()
    assert StructuredFilter(places=["杭州"]).is_empty() is False


def test_describe_only_lists_active_conditions() -> None:
    flt = StructuredFilter(
        date_start=date(2025, 9, 1), date_end=date(2025, 11, 30), themes=["古镇"]
    )
    assert flt.describe() == {
        "date": ["2025-09-01", "2025-11-30"],
        "themes": ["古镇"],
    }


def test_build_filter_uses_rule_time_even_when_degraded() -> None:
    """LLM 解析失败（degraded）时丢弃 places/themes，但保留规则算出的时间。

    理由：规则解析器是确定性计算，它的结果不属于"猜"，
    把它一起丢掉会让离线模式下的时间过滤永远不生效。
    """
    parsed = ParsedQuery(
        keywords=[],
        places=["乌镇"],
        themes=["古镇"],
        degraded=True,
        time_range={"start": "2025-09-01", "end": "2025-11-30"},
        time_source="rule",
    )
    flt = Retriever.build_filter(parsed)
    assert flt is not None
    assert (flt.date_start, flt.date_end) == (date(2025, 9, 1), date(2025, 11, 30))
    assert flt.places == [] and flt.themes == []


def test_build_filter_keeps_llm_places_when_not_degraded() -> None:
    parsed = ParsedQuery(places=["乌镇"], themes=["古镇"], degraded=False)
    flt = Retriever.build_filter(parsed)
    assert flt is not None
    assert flt.places == ["乌镇"] and flt.themes == ["古镇"]


def test_build_filter_ignores_malformed_time_range() -> None:
    """时间范围格式不对 → 宁可不过滤，也不能静默漏召回。"""
    parsed = ParsedQuery(time_range={"start": "去年", "end": "秋天"})
    assert Retriever.build_filter(parsed) is None


def test_build_filter_returns_none_without_any_condition() -> None:
    assert Retriever.build_filter(None) is None
    assert Retriever.build_filter(ParsedQuery(keywords=["古镇"])) is None


def test_bbox_from_center_is_larger_at_high_latitude() -> None:
    """同样的半径，纬度越高经度方向要拉得越开（1° 经度在两极趋近于 0）。"""
    near_equator = bbox_from_center(0.0, 120.0, 10.0)
    near_north = bbox_from_center(60.0, 120.0, 10.0)
    width_eq = near_equator.max_lng - near_equator.min_lng
    width_north = near_north.max_lng - near_north.min_lng
    assert width_north > width_eq


def test_bbox_overlap_semantics() -> None:
    """两个矩形只要在两个方向上都重叠就算命中。"""
    bbox = BBox(min_lng=120.0, min_lat=30.0, max_lng=121.0, max_lat=31.0)
    # 完全在外
    assert not _overlap(bbox, BBox(122.0, 32.0, 123.0, 33.0))
    # 部分重叠
    assert _overlap(bbox, BBox(120.5, 30.5, 122.0, 32.0))
    # 包含
    assert _overlap(bbox, BBox(119.0, 29.0, 125.0, 35.0))


def _overlap(a: BBox, b: BBox) -> bool:
    return not (
        a.min_lat > b.max_lat
        or a.max_lat < b.min_lat
        or a.min_lng > b.max_lng
        or a.max_lng < b.min_lng
    )
