"""语义抽象（W7-1）的用例。

这里没有一处 skip：本模块是**纯函数层**，不碰数据库、不碰网络、不碰时钟，
没有任何"环境不具备就该跳过"的理由（PROJECT.md §二、[7]）。

用例分四组：
1. **映射准确性** —— 设计文档 §9.1 示例行程里的每个地点，以及高德 POI 大类
2. **边界与确定性** —— 无命中 / 平票 / 单输入 / 大小写 / `matched` 可审计
3. **feature flag** —— 关着时必须"不存在"，不是报错
4. **两端词汇一致性** —— 后端 `SEMANTIC_TAGS` 与前端 `ALL_TAGS` 逐项相等
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.feature_flags import clean_settings
from app.services.semantic_service import (
    VERSION,
    SemanticTagResult,
    classify_photo,
    classify_place,
    semantic_sequence_for_trip,
    semantic_tag_for_place,
    semantic_tag_for_place_object,
)
from app.services.semantic_trajectory import extract_sequences
from app.utils.semantic_tags import SEMANTIC_TAG_LABELS, SEMANTIC_TAG_VERSION, SEMANTIC_TAGS


class _FakePlace:
    """最小地点替身：`semantic_sequence_for_trip` 靠 duck typing 取字段，不需要 ORM。"""

    def __init__(
        self,
        *,
        name: str | None = None,
        tag: str | None = None,
        poi_type: str | None = None,
        ai_tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.tag = tag
        self.poi_type = poi_type
        self.ai_tags = ai_tags

#: 仓库根（本文件在 backend/tests/ 下）
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARE_TS = REPO_ROOT / "frontend" / "src" / "types" / "share.ts"


# ---------------------------------------------------------------------------
# 1. 映射准确性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("place", "expected"),
    [
        # 设计文档 §9.1 洛阳线：龙门石窟(人文·高) → 洛邑古城(人文·中) → 老君山(自然·极高)
        # → 洛阳博物馆(人文·高)
        ("龙门石窟", "heritage"),
        ("洛邑古城", "heritage"),
        ("老君山", "nature"),
        ("洛阳博物馆", "heritage"),
        # 自然类补充
        ("云台山", "nature"),
        ("云海", "nature"),
        # 设计文档 §9.1 苏州—上海线：拙政园(水乡·高) → 平江路(水乡·中)
        # → 外滩(都市·高) → 陆家嘴(都市·高)
        ("拙政园", "watertown"),
        ("平江路", "watertown"),
        ("外滩", "metropolis"),
        ("陆家嘴", "metropolis"),
        # 水乡类补充
        ("乌镇", "watertown"),
        ("西塘", "watertown"),
        # 其余两类
        ("夜市", "food"),
        ("小吃街", "food"),
        ("温泉", "leisure"),
        ("度假村", "leisure"),
    ],
)
def test_design_doc_itinerary_places(place: str, expected: str):
    """设计文档 §9.1 示例行程 + 同类补充，一个都不能分错。"""
    assert classify_place(name=place).tag == expected


def test_longmen_by_ai_tag():
    """同一个地点用 `ai_tags=["石窟"]` 也要落到 heritage（导入链路常常只有 ai_tags）。"""
    assert classify_place(ai_tags=["石窟"]).tag == "heritage"


@pytest.mark.parametrize(
    ("poi_type", "expected"),
    [
        ("科教文化服务", "heritage"),
        ("餐饮服务", "food"),
        ("体育休闲服务", "leisure"),
        ("购物服务", "metropolis"),
    ],
)
def test_amap_poi_types(poi_type: str, expected: str):
    """高德 POI 大类 → 语义标签。"""
    assert classify_place(poi_type=poi_type).tag == expected


def test_result_carries_label_and_version():
    result = classify_place(name="龙门石窟")
    assert result.tag == "heritage"
    assert result.label == "人文历史"
    assert result.version == SEMANTIC_TAG_VERSION


@pytest.mark.parametrize("tag", SEMANTIC_TAGS)
def test_every_tag_has_a_label(tag: str):
    """六类都要有中文名 —— 前端语义条直接显示 `label`，缺一个就是空白条。"""
    assert tag in SEMANTIC_TAG_LABELS


# ---------------------------------------------------------------------------
# 2. 边界与确定性
# ---------------------------------------------------------------------------


def test_no_hit_returns_empty_result():
    result = classify_place(name="未知地点", ai_tags=["xyz"])
    assert result.tag is None
    assert result.label is None
    assert result.confidence == 0.0
    assert result.matched == ()


def test_empty_input_is_not_a_hit():
    """空串 / 空列表不参与投票（否则 `poi_type=""` 会凭空多出一个分母）。"""
    result = classify_place(poi_type="", ai_tags=[], name=None)
    assert result.tag is None
    assert result.confidence == 0.0


def test_tie_falls_back_to_semantic_tags_order():
    """平票且命中词长度相同时，按 `SEMANTIC_TAGS` 顺序取靠前的。

    "塔"(heritage) 与 "山"(nature) 各 1 票、命中词都是 1 字 → heritage 在前。
    """
    result = classify_place(ai_tags=["塔", "山"])
    assert result.tag == "heritage"


def test_tie_prefers_the_more_specific_keyword():
    """平票时命中词更具体的一类胜出 —— 这是 `平江路` 不被单字"江"抢走的原因。

    词表层的通用平票规则（按 `SEMANTIC_TAGS` 顺序）会让 `平江路` 落到 nature，
    与设计文档 §9.1 的"平江路 = 水乡"矛盾；`semantic_service` 只在真的平票时
    追加"更具体的命中词优先"这条更细的规则。
    """
    result = classify_place(name="平江路")
    assert result.tag == "watertown"
    assert result.matched == ("平江路",)


def test_each_input_kind_can_hit_alone():
    """`poi_type` / `ai_tags` / `name` 三类输入单独给，都要能命中。"""
    assert classify_place(poi_type="科教文化服务").tag == "heritage"
    assert classify_place(ai_tags=["石窟"]).tag == "heritage"
    assert classify_place(name="龙门石窟").tag == "heritage"


def test_ai_tags_vote_item_by_item():
    """`ai_tags` 逐项投票：同类多项的证据比一项强（`confidence` 更高）。

    分母是"参与投票的输入段数"，两份证据都对上 heritage 时分子也更大，
    所以下面的严格大于成立 —— 它同时也是"逐项投票"而非"整串只投一票"的证据。
    """
    two_hits = classify_place(ai_tags=["博物馆", "石窟"], name="云台山")
    one_hit = classify_place(ai_tags=["博物馆"], name="云台山")

    assert two_hits.tag == "heritage"
    assert one_hit.tag == "heritage"
    assert two_hits.confidence > one_hit.confidence
    assert two_hits.matched == ("博物馆", "石窟")


def test_confidence_is_capped_at_one():
    """`confidence` 上限 1.0（票数不会超过段数，这条是防止将来分母改小后越界）。"""
    result = classify_place(poi_type="餐饮服务", name="夜市", ai_tags=["小吃"])
    assert result.tag == "food"
    assert result.confidence == 1.0


def test_confidence_reflects_disagreeing_evidence():
    """两段证据只有一段指向胜出类 → 0.5，调用方据此可以决定要不要落库。"""
    result = classify_place(poi_type="科教文化服务", name="云台山")
    assert result.tag == "heritage"
    assert result.confidence == 0.5


def test_deterministic_across_calls():
    """同一输入重复调用必须完全一致（跨设备的语义序列要对得上）。"""
    first = classify_place(poi_type="餐饮服务", name="夜市", ai_tags=["小吃", "烧烤"])
    second = classify_place(poi_type="餐饮服务", name="夜市", ai_tags=["小吃", "烧烤"])
    assert first == second
    assert isinstance(first, SemanticTagResult)


@pytest.mark.parametrize(
    ("ai_tags", "expected"),
    [
        (["CBD"], "metropolis"),
        (["Cbd"], "metropolis"),
        (["cbd"], "metropolis"),
        (["SPA"], "leisure"),
        (["spa"], "leisure"),
    ],
)
def test_case_insensitive(ai_tags: list[str], expected: str):
    """中英混排的 POI 名里英文很常见，匹配必须大小写不敏感。"""
    assert classify_place(ai_tags=ai_tags).tag == expected


def test_matched_keeps_original_casing():
    """审计要看的是"我传进来的哪个字符串触发了它"，所以 `matched` 不做小写化。"""
    result = classify_place(ai_tags=["CBD"], name="外滩")
    assert result.tag == "metropolis"
    assert set(result.matched) == {"CBD", "外滩"}


def test_result_is_frozen():
    """结果会被塞进语义序列参与哈希聚合，可变对象会带来"同一条记录两个哈希"。"""
    result = classify_place(name="龙门石窟")
    with pytest.raises(FrozenInstanceError):
        result.tag = "nature"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# classify_photo
# ---------------------------------------------------------------------------


def test_classify_photo_uses_ai_tags():
    photo = SimpleNamespace(ai_tags=["石窟"], caption=None)
    assert classify_photo(photo).tag == "heritage"


def test_classify_photo_uses_caption():
    photo = SimpleNamespace(ai_tags=None, caption="云海日出")
    assert classify_photo(photo).tag == "nature"


def test_classify_photo_without_evidence():
    photo = SimpleNamespace(ai_tags=[], caption=None)
    result = classify_photo(photo)
    assert result.tag is None
    assert result.confidence == 0.0


def test_classify_photo_accepts_orm_photo():
    """真实的 `app.models.photo.Photo` 满足 duck typing（字段名改了这里会红）。"""
    from app.models.photo import Photo

    photo = Photo()
    photo.ai_tags = ["温泉"]
    photo.caption = "泡汤"
    result = classify_photo(photo)
    assert result.tag == "leisure"
    assert "温泉" in result.matched


# ---------------------------------------------------------------------------
# 3. feature flag
# ---------------------------------------------------------------------------


def test_semantic_flag_defaults_off():
    """出厂默认关闭（PROJECT.md §二、[8]：扩展模块默认全关）。"""
    assert clean_settings().semantic_enabled is False


def test_flag_off_means_no_semantics(monkeypatch):
    """开关关着 → `None`，语义能力"不存在"，绝不报错。"""
    monkeypatch.delenv("SEMANTIC_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        assert semantic_tag_for_place(name="龙门石窟") is None
        assert semantic_tag_for_place(poi_type="餐饮服务") is None
    finally:
        get_settings.cache_clear()


def test_flag_on_returns_real_tag(monkeypatch):
    """开关打开 → 与 `classify_place()` 一致的真实标签。"""
    monkeypatch.setenv("SEMANTIC_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert semantic_tag_for_place(name="龙门石窟") == "heritage"
        assert semantic_tag_for_place(name="乌镇") == "watertown"
        assert semantic_tag_for_place(name="未知地点") is None
    finally:
        monkeypatch.delenv("SEMANTIC_ENABLED", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 4. 版本与两端词汇一致性
# ---------------------------------------------------------------------------


def test_version_is_reexported():
    assert VERSION == SEMANTIC_TAG_VERSION
    assert VERSION == "sem-2026.2"


def test_backend_and_frontend_vocabulary_match():
    """`SEMANTIC_TAGS` 与前端 `ALL_TAGS` **逐项**一致（含顺序）。

    守的是"两端语义口径分叉"这个最难查的 bug：单看后端全绿、单看前端也全绿，
    只有把 A 机器抽的语义序列拿到 B 机器的前端去渲染/推荐时才发现对不上，
    而那时已经有一批落库数据是按错误口径写的。
    """
    assert SHARE_TS.is_file(), f"前端类型文件缺失：{SHARE_TS}"

    source = SHARE_TS.read_text(encoding="utf-8")
    block = re.search(r"export const ALL_TAGS[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert block is not None, "share.ts 里找不到 `ALL_TAGS` 数组"

    frontend_tags = re.findall(r"'([^']+)'", block.group(1))
    assert frontend_tags == list(SEMANTIC_TAGS)


# ---------------------------------------------------------------------------
# 6. Trip → 语义轨迹序列
# ---------------------------------------------------------------------------


def test_luoyang_itinerary_sequence() -> None:
    """设计文档 §9.1 洛阳线：每个地点一个标签，**不合并相邻的相同标签**。"""
    places = [
        _FakePlace(name="龙门石窟"),
        _FakePlace(name="洛邑古城"),
        _FakePlace(name="老君山"),
    ]
    assert semantic_sequence_for_trip(places) == ["heritage", "heritage", "nature"]


def test_suzhou_shanghai_itinerary_sequence() -> None:
    """§9.1 苏州—上海线。"""
    places = [
        _FakePlace(name="拙政园"),
        _FakePlace(name="平江路"),
        _FakePlace(name="外滩"),
        _FakePlace(name="陆家嘴"),
    ]
    assert semantic_sequence_for_trip(places) == [
        "watertown", "watertown", "metropolis", "metropolis",
    ]


def test_sequence_skips_unclassifiable_places() -> None:
    """抽不出标签的地点**跳过**，不塞 `None` / `unknown` 占位符。

    占位符会被下游 `validate_sequence()` 静默丢掉，于是"序列长度"和"地点数"
    对不上，而这种错位不报错 —— 直接跳过至少让两者都诚实。
    """
    places = [_FakePlace(name="龙门石窟"), _FakePlace(name="完全不知名的地方"), _FakePlace(name="老君山")]
    assert semantic_sequence_for_trip(places) == ["heritage", "nature"]


def test_sequence_of_empty_trip_is_empty() -> None:
    assert semantic_sequence_for_trip([]) == []


def test_existing_tag_is_preferred_over_reclassification() -> None:
    """库里已有的 `tag` 直接用，不重新抽 —— 保证同一趟行程的序列可复现。"""
    place = _FakePlace(name="老君山", tag="heritage")
    assert semantic_sequence_for_trip([place]) == ["heritage"]


def test_invalid_stored_tag_is_reclassified() -> None:
    """存了个不是六类的 `tag`（旧词表 / 空串）→ 按"没有标签"重抽，而不是照抄。"""
    place = _FakePlace(name="老君山", tag="NOT_A_TAG")
    assert semantic_sequence_for_trip([place]) == ["nature"]


def test_sequence_uses_ai_tags_when_no_name() -> None:
    """导入链路常常只有 `ai_tags`（还没有 caption）。"""
    assert semantic_sequence_for_trip([_FakePlace(ai_tags=["石窟"])]) == ["heritage"]


def test_min_confidence_filters_weak_evidence() -> None:
    """`min_confidence` 调高 → 证据互相矛盾的地点被剔除。"""
    # 「科教文化服务」投 heritage、「云台山」投 nature：两票里只有一票指向胜出类
    place = _FakePlace(name="云台山", poi_type="科教文化服务")
    assert semantic_sequence_for_trip([place], min_confidence=0.0) != []
    assert semantic_sequence_for_trip([place], min_confidence=1.0) == []


def test_sequence_differs_from_pattern_mining_sequence() -> None:
    """**两个"序列"不是一回事**：展示用不合并，挖掘用必须合并相邻重复。

    这条断言守的是"拿错方向"：用展示序列去 `submit()` 会碎掉模式库，
    用挖掘序列去展示会让用户以为自己少去了几站。
    """
    places = [
        _FakePlace(name="龙门石窟"),
        _FakePlace(name="洛邑古城"),
        _FakePlace(name="老君山"),
    ]
    assert semantic_sequence_for_trip(places) == ["heritage", "heritage", "nature"]

    # 挖掘侧：合并相邻相同标签 → 一条二元序列
    tagged = [_FakePlace(tag=tag) for tag in semantic_sequence_for_trip(places)]
    assert extract_sequences(tagged) == [("heritage", "nature")]


def test_place_object_tag_lookup_ignores_non_string() -> None:
    """`tag` 是 None / 非字符串时按"没有标签"处理，不抛异常。"""
    assert semantic_tag_for_place_object(_FakePlace(name="老君山", tag=None)) == "nature"
    assert semantic_tag_for_place_object(object()) is None
