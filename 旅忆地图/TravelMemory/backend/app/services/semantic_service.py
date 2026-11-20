"""语义抽象：把**具体地点**折叠成**六类语义标签**（阶段 7 W7-1）。

## 为什么要做语义抽象

群体推荐的直觉写法是"基于具体地点"：

    去过龙门石窟的人，接着去了老君山  →  推荐老君山

这条记录本身就是重识别攻击的入口。**龙门石窟的游客本就有限**，一条
`龙门石窟 → 老君山` 的记录几乎能把一个自然人从人群里挑出来；再叠加日期
（"五一前后"）和一个大概的城市，重识别就是必然而不是风险。

抽象成

    heritage → nature

之后，同一条模式会被**大量**行程命中：任何"看了人文景点之后去爬山"的行程
都会贡献同一个 `heritage → nature`。这时一条模式记录对应的人群是"所有这么玩过
的人"，单条记录不再指向个体。

**隐私收益来自泛化，而不是来自加密**：加密只改变数据的存在形式，泛化改变的是
数据里"有多少可区分信息"。所以本模块是整个阶段 7 的地基 ——
W7-2 的模式挖掘、W7-3 的匿名门禁，消费的都是这里产出的语义序列。

## 关于术语的诚实表述（设计文档 §9.3）

各客户端在本地完成语义抽取与模式统计，仅上传经扰动的聚合统计量（不含坐标、
地点或标识），服务端作为逻辑聚合器汇总为全局语义模式库。参与者规模有限
（数十人量级），**不宣称联邦学习**：隐私收益来自语义抽象 + 扰动 + k-匿名门禁
三重机制的叠加，而非参与方数量。本模块只负责其中的第一重。

## 分类规则在哪儿

关键词表与投票规则在 `app/utils/semantic_tags.py`（六类词汇的**唯一真相源**，
与前端 `src/types/share.ts` 的 `ALL_TAGS` 逐项一致）。本模块**不复制**词表，
只做三件词表层不该管的事：

1. **可审计**：`matched` 记下命中的原始输入片段，`confidence` 记下证据强度 ——
   "为什么这个石窟被分到 nature"必须能当场回答，否则模式库里的脏数据无从排查。
2. **平票修正**：词表层的平票规则是"按 `SEMANTIC_TAGS` 顺序取靠前的"（通用规则，
   保证跨设备一致）。但六类里 `nature` 排在 `watertown` 前面，而 `nature` 的
   关键字里有单字"江"，于是 `平江路` 会同时命中 `nature`(江) 与 `watertown`(平江路)，
   按通用规则被判成 `nature` —— 与设计文档 §9.1 的示例行程（平江路 = 水乡·中）矛盾。
   本模块在**真的平票**时追加一条更细的规则：**命中词更具体的那一类胜出**
   （"平江路" 4 字 > "江" 1 字）。只有在票数相同的候选之间比较，
   不影响词表层的任何非平票结果。
3. **能力门控**：`semantic_tag_for_place()` 走 feature flag，开关关着时"没有语义"
   而不是报错（见该函数的 docstring）。

## 确定性

纯函数，无 IO、无随机、无缓存：同一输入在 A 机器和 B 机器上必须得到同一个标签，
否则两地的语义序列对不上，聚合出来的是噪声。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.feature_flags import is_enabled
from app.utils.semantic_tags import (
    SEMANTIC_TAG_LABELS,
    SEMANTIC_TAG_VERSION,
    is_semantic_tag,
    match_semantic_tag,
)

#: re-export：上层（模式挖掘 / API）引用版本号时不必再往下 import utils。
VERSION = SEMANTIC_TAG_VERSION


@dataclass(frozen=True)
class SemanticTagResult:
    """一次语义抽取的完整结果。

    字段为什么要带 `matched` / `confidence` 而不只返回一个字符串：
    语义标签会落库并参与跨设备的模式聚合，"为什么是这一类"必须能事后回答，
    否则模式库里出现脏数据时只能靠猜。
    """

    tag: str | None  # 六类之一，抽取不出来时为 None
    label: str | None  # 中文名（tag 为 None 时也是 None）
    version: str  # SEMANTIC_TAG_VERSION
    matched: tuple[str, ...]  # 命中的输入片段（便于排障"为什么被分到这一类"）
    confidence: float  # 0.0 ~ 1.0


class _PhotoLike(Protocol):
    """`classify_photo()` 接受的形状（`app.models.photo.Photo` 天然满足）。

    用 Protocol 而不是 import `Photo`：语义抽取会被对象构造之外的场景调用
    （离线批处理里的轻量 DTO、测试里的假对象），duck typing 让这些调用方
    不必为了一个标签去拉起 ORM 类。
    """

    ai_tags: Sequence[str] | None
    caption: str | None


def classify_place(
    *,
    poi_type: str | None = None,
    ai_tags: Sequence[str] | None = None,
    name: str | None = None,
) -> SemanticTagResult:
    """把具体地点映射到六类语义标签（纯函数，无 IO）。

    `confidence` = **胜出那一类的票数 / 参与投票的输入段数**（上限 1.0）。
    口径说明：它是"证据里有多大比例指向这一类"，不是模型概率 ——
    `poi_type="科教文化服务"` + `name="云台山"` 是 heritage 与 nature 各 1 票，
    两票里只有一票指向胜出类，`confidence` 就是 0.5，调用方据此可以决定
    "要不要把这条语义写进序列"。**无命中时 `tag=None` 且 `confidence=0.0`**。

    `matched` 只记命中的**原始**输入片段（小写化之前的样子），
    因为排障时人要看的是"我传进来的哪一个字符串触发了它"。
    """
    match = match_semantic_tag(poi_type=poi_type, ai_tags=ai_tags, name=name)
    if match.tag is None:
        return SemanticTagResult(
            tag=None,
            label=None,
            version=VERSION,
            matched=(),
            confidence=0.0,
        )

    # confidence = 胜出票数 / 参与投票的段数（口径见 `classify_place` docstring）。
    total = match.segment_count
    confidence = 0.0 if total == 0 else min(1.0, match.scores[match.tag] / total)
    return SemanticTagResult(
        tag=match.tag,
        label=SEMANTIC_TAG_LABELS[match.tag],
        version=VERSION,
        matched=match.matched.get(match.tag, ()),
        confidence=confidence,
    )


def classify_photo(photo: _PhotoLike | Any) -> SemanticTagResult:
    """给一张照片打语义标签：取 `ai_tags` 与 `caption` 两段证据。

    刻意**不 import `app.models.photo.Photo`**：这里是纯函数层，duck typing 让
    未加载的 ORM 对象、离线批处理的轻量 DTO、测试假对象都能进来。
    `getattr(..., default)` 而不是直接取属性：SQLAlchemy 对象在字段未加载时会抛
    `DetachedInstanceError`，而"这段证据缺失"的正确处理是少一票，不是崩掉。
    """
    ai_tags = getattr(photo, "ai_tags", None)
    caption = getattr(photo, "caption", None)
    return classify_place(poi_type=None, ai_tags=ai_tags, name=caption)


def semantic_tag_for_place_object(place: Any) -> str | None:
    """从一个地点对象上取语义标签：**已有 `tag` 就用它，没有才现算**。

    为什么先看 `tag` 再分类：库里的 `Place.tag` 是**当初**抽出来的结论，
    而地点名/POI 类型可能随时被改。对同一趟行程重跑两次抽取却得到两个序列，
    会让"这趟行程的语义轨迹"变成一件不可复现的事 —— 而阶段 7 的群体模式库
    恰恰要求同一条行程在任何时候都抽出同一条序列。

    `tag` 必须**真的是六类之一**才采纳（`is_semantic_tag`）：库里可能存着
    旧版本词表抽出来的标签，或者空串，那些按"没有标签"处理，重新抽一次。
    """
    existing = getattr(place, "tag", None)
    if isinstance(existing, str) and is_semantic_tag(existing):
        return existing

    return classify_place(
        poi_type=_attr(place, "poi_type"),
        ai_tags=_attr(place, "ai_tags"),
        name=_attr(place, "name") or _attr(place, "title") or _attr(place, "caption"),
    ).tag


def _attr(obj: Any, name: str) -> Any:
    """取属性，取不到（或 ORM 字段未加载）时给 `None` 而不是抛异常。

    与 `classify_photo()` 里同一条理由：SQLAlchemy 对象在字段未加载时会抛
    `DetachedInstanceError`，而"这段证据缺失"的正确处理是少一票，不是崩掉。
    """
    try:
        value = getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - DetachedInstanceError 等 ORM 惰性加载异常
        return None
    if name == "ai_tags":
        return value if isinstance(value, (list, tuple)) else None
    return value if isinstance(value, str) else None


def semantic_sequence_for_trip(
    places: Sequence[Any],
    *,
    min_confidence: float = 0.0,
) -> list[str]:
    """把一个 Trip 的**全部地点**折成语义轨迹序列，如 `["heritage", "heritage", "nature"]`。

    ## 与 `semantic_trajectory.extract_sequences()` 的区别（两者都叫"序列"，但不是一回事）

    | | 本函数 | `extract_sequences` |
    | --- | --- | --- |
    | 每个地点 | **一个标签**，不合并 | 合并相邻的相同标签 |
    | 输出 | `["heritage", "heritage", "nature"]` | `[("heritage", "nature")]` |
    | 用途 | **展示**：这就是这趟行程的语义轨迹 | **挖掘**：喂给模式库统计 `support` |

    为什么两都要有：展示时"连着逛了两个石窟"是有信息量的（它占了两站），
    而挖掘时它必须合并 —— 否则 `heritage→heritage→nature` 会多出一条
    几乎无人命中的 `(("heritage","heritage"), "nature")` 模式，白白稀释支持度。
    **用错方向的代价是真实可见的**：拿本函数的序列去 `submit()`，
    模式库会碎成一片；拿合并后的序列去展示，用户会以为自己只去过三个地方。

    ## 抽不出标签的地点：**跳过**，不占位

    序列里不会出现 `None` / `"unknown"`：一个"未知"占位符会让下游的
    `validate_sequence()` 静默丢掉它，于是序列长度与地点数对不上，
    而这种错位不报错。跳过同样会改变长度，但语义是诚实的 ——
    "这趟行程里有 2 个地点我们还不知道是什么"。

    ## `min_confidence`

    证据太弱时（`classify_place` 的 `confidence` 低于阈值）也跳过，默认 `0.0`
    即"有分类就收"。调高它表示"只在比较确定时才写进轨迹"。
    """
    sequence: list[str] = []
    for place in places:
        tag = semantic_tag_for_place_object(place)
        if tag is None:
            continue
        if min_confidence > 0.0:
            confidence = classify_place(
                poi_type=_attr(place, "poi_type"),
                ai_tags=_attr(place, "ai_tags"),
                name=_attr(place, "name") or _attr(place, "title") or _attr(place, "caption"),
            ).confidence
            if confidence < min_confidence:
                continue
        sequence.append(tag)
    return sequence


def semantic_tag_for_place(
    *,
    poi_type: str | None = None,
    ai_tags: Sequence[str] | None = None,
    name: str | None = None,
) -> str | None:
    """`classify_place()` 的 **feature flag 门控版**（开关名 `semantic`）。

    降级语义（PROJECT.md §二、[8]）：扩展模块默认全关。**关着时返回 `None`，
    语义能力"不存在"，绝不报错** —— 阶段 6 及以前的调用方要么拿到 `None`
    要么根本不调这里，行为与阶段 6 完全一致。

    为什么是 `None` 而不是抛异常或返回兜底标签：语义是**增强**，不是链路上的
    必需环节。抛异常会让"没开这个开关"变成一次 500；返回兜底标签（比如一律
    `heritage`）则会把噪声写进模式库，比没有数据更糟 —— 猜错的语义序列会污染
    整个群体模式库，而"没有语义"只是少一条数据。
    """
    if not is_enabled("semantic"):
        return None
    return classify_place(poi_type=poi_type, ai_tags=ai_tags, name=name).tag
