"""`app/services/semantic_trajectory.py` 的纯函数层测试。

本文件**禁止 skip**：这里全是内存实现，不碰数据库、不碰网络，
任何 skip 都意味着"这条红线今天没人守"。

组织顺序按 §9.2 的流水线走：归一化（`truncate` / `validate` / `extract`）
→ 上报（`submit`）→ **k-匿名门禁**（`recommend`）→ 防刷（unique）→ 评分过滤 → 确定性。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.semantic_trajectory import (
    K_ANONYMITY,
    MAX_SEQUENCE_STEPS,
    MIN_AVG_SCORE,
    SemanticAggregator,
    SemanticPattern,
    build_aggregator,
    extract_sequences,
    truncate_sequence,
    validate_sequence,
)
from app.utils.semantic_tags import SEMANTIC_TAGS

#: 一条设计文档 §9.1 的"洛阳"简化版：人文之后去看自然
LUOYANG_LIKE = ("heritage", "nature")


@dataclass(frozen=True)
class FakePlace:
    """测试替身：只带 `tag`，用来证明 `extract_sequences` 只依赖 `tag` 这一个字段。"""

    tag: str | None


# ---------------------------------------------------------------- 归一化


def test_truncate_keeps_the_last_steps() -> None:
    """超长时保留**最后** `MAX_SEQUENCE_STEPS` 步：最近的行为才预测"下一步"。"""
    long = ("heritage", "nature", "watertown", "metropolis", "food", "leisure", "heritage")
    assert len(long) == MAX_SEQUENCE_STEPS + 1
    assert truncate_sequence(long) == long[-MAX_SEQUENCE_STEPS:]


def test_truncate_keeps_short_sequences_intact() -> None:
    """短于上限的序列原样返回（**同一个顺序**，不做任何重排）。"""
    assert truncate_sequence(("heritage", "nature")) == ("heritage", "nature")
    assert truncate_sequence(("heritage",)) == ("heritage",)


def test_truncate_drops_invalid_tags() -> None:
    """非法标签在截断前就被丢掉：截断的是"有效步数"，不是总长度。"""
    mixed = ("heritage", "not-a-tag", "nature", "", "watertown")
    assert truncate_sequence(mixed) == ("heritage", "nature", "watertown")


def test_truncate_respects_custom_max_steps() -> None:
    """`max_steps` 可覆盖（单测里用小值验证"保留后段"这件事本身）。"""
    assert truncate_sequence(("heritage", "nature", "food"), max_steps=2) == ("nature", "food")


def test_validate_sequence_drops_unknown_tags_instead_of_raising() -> None:
    """非法标签**静默丢弃**：为了一个脏标签拒绝整条上报并不划算。"""
    assert validate_sequence(("heritage", "xxx", "nature", None)) == ("heritage", "nature")


def test_validate_sequence_accepts_every_declared_tag() -> None:
    """六类标签全部合法 —— 这条盯着"词表改了但校验没跟上"的静默分叉。"""
    assert validate_sequence(SEMANTIC_TAGS) == SEMANTIC_TAGS


def test_extract_sequences_merges_adjacent_duplicates() -> None:
    """`heritage → heritage → nature` 抽成 `("heritage", "nature")`（§9.1 的抽象方式）。"""
    places = [FakePlace(tag="heritage"), FakePlace(tag="heritage"), FakePlace(tag="nature")]
    assert extract_sequences(places) == [("heritage", "nature")]


def test_extract_sequences_skips_untagged_places() -> None:
    """没抽出语义的地点（`tag=None`）被跳过，不产生空洞、也不打断序列。"""
    places = [FakePlace(tag="heritage"), FakePlace(tag=None), FakePlace(tag="nature")]
    assert extract_sequences(places) == [("heritage", "nature")]


def test_extract_sequences_returns_empty_list_when_nothing_is_usable() -> None:
    """全程都是未知标签 → 空列表（不是 `None`，也不是抛错）。"""
    assert extract_sequences([FakePlace(tag="xxx"), FakePlace(tag=None)]) == []


def test_extract_sequences_truncates_long_trips() -> None:
    """超过 6 步的行程在抽取阶段就被截断 —— 长历史本身就是指纹。"""
    places = [FakePlace(tag=tag) for tag in SEMANTIC_TAGS * 2]
    (sequence,) = extract_sequences(places)
    assert len(sequence) == MAX_SEQUENCE_STEPS


# ---------------------------------------------------------------- 上报


def test_submit_returns_zero_for_single_step_sequences() -> None:
    """只有一个标签就没有"下一步"可学，直接 0（且不计入上报次数）。"""
    agg = SemanticAggregator()
    assert agg.submit(("heritage",)) == 0
    assert agg.submit(("xxx",)) == 0
    assert agg.stats()["submissions"] == 0


def test_submit_records_every_true_prefix() -> None:
    """`a → b → c` 生成 `(a, b)` 与 `((a, b), c)` 两条模式，没有"以最后一个标签为前缀"的死模式。"""
    agg = SemanticAggregator()
    assert agg.submit(("heritage", "nature", "food")) == 2
    assert agg.recommend(("heritage",)) is None  # support=1，未达标
    assert agg.stats() == {"patterns": 2, "published": 0, "submissions": 1}


# ---------------------------------------------------------------- k-匿名门禁（核心红线）


def test_support_below_k_is_invisible() -> None:
    """**红线**：`support = k-1` 时 `recommend()` 必须返回 `None`。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY - 1):
        agg.submit(LUOYANG_LIKE)
    assert agg.stats()["patterns"] == 1
    assert agg.stats()["published"] == 0
    assert agg.recommend(("heritage",)) is None


def test_support_reaching_k_publishes_the_pattern() -> None:
    """**红线**：补上第 `k` 条后立刻可见，且 `published` 计数同步为 1。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY - 1):
        agg.submit(LUOYANG_LIKE)
    assert agg.recommend(("heritage",)) is None

    agg.submit(LUOYANG_LIKE)
    hit = agg.recommend(("heritage",))
    assert hit is not None, f"support={K_ANONYMITY} 应当放行，实际被拦"
    assert hit.tag == "nature"
    assert agg.stats()["published"] == 1


def test_high_score_does_not_bypass_the_gate() -> None:
    """`support = k-1` 且 `avg_score` 满分 —— 门禁只看 `support`，高分换不来匿名集。

    后半段断言"评分其实一直在累"：证明被拦的确实是 support，而不是"没有评分数据"。
    """
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY - 1):
        agg.submit(LUOYANG_LIKE, score=5.0)
    assert agg.recommend(("heritage",)) is None

    agg.submit(LUOYANG_LIKE, score=5.0)
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.avg_score == pytest.approx(5.0)


def test_gate_is_enforced_per_pattern_not_globally() -> None:
    """门禁是按**模式**判的：同一前缀下未达标的候选不达标，达标的候选照常返回。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "nature"))
    agg.submit(("heritage", "food"))  # support=1，未达标

    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"
    assert agg.stats() == {"patterns": 2, "published": 1, "submissions": K_ANONYMITY + 1}


def test_published_flag_follows_the_threshold() -> None:
    """`SemanticPattern.published` 是门禁的唯一判定点：`support >= K_ANONYMITY`。

    这条把公式钉死 —— 将来有人把 `>` 写成 `>=`（或反过来），这里先红。
    """
    prefix, next_tag = ("heritage",), "nature"
    assert not SemanticPattern(prefix, next_tag, K_ANONYMITY - 1, 0.0, 0).published
    assert SemanticPattern(prefix, next_tag, K_ANONYMITY, 0.0, 0).published
    assert SemanticPattern(prefix, next_tag, K_ANONYMITY + 100, 0.0, 0).published


# ---------------------------------------------------------------- unique 去重（防刷）


def test_same_user_key_counts_only_once() -> None:
    """**防刷**：同一个 `user_key` 对同一模式只贡献一次（§9.2 的 unique 约束）。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, user_key="alice")
    assert agg.recommend(("heritage",)) is None, "刷票不得把 support 刷过门禁"


def test_distinct_user_keys_accumulate() -> None:
    """不同 `user_key` 正常累加 —— 去重不能把真实的不同人也并掉。"""
    agg = SemanticAggregator()
    for index in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, user_key=f"user-{index}")
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"


def test_unique_is_per_pattern_not_per_user() -> None:
    """同一个人走**不同**的后续路线，两条模式都要计（去重键是 `(user, pattern)`）。"""
    agg = SemanticAggregator()
    agg.submit(("heritage", "nature"), user_key="alice")
    assert agg.submit(("heritage", "food"), user_key="alice") == 1
    assert agg.stats()["patterns"] == 2


def test_repeat_submission_reports_zero_new_entries() -> None:
    """去重命中时 `accepted = 0`：客户端能据此判断"这次上报没有推进任何模式"。

    注意它会**照常**计一次上报次数（"收到了"与"采纳了"是两件事）。
    """
    agg = SemanticAggregator()
    assert agg.submit(LUOYANG_LIKE, user_key="alice") == 1
    assert agg.submit(LUOYANG_LIKE, user_key="alice") == 0
    assert agg.stats()["submissions"] == 2


# ---------------------------------------------------------------- 评分过滤


def test_scores_below_threshold_are_not_accumulated() -> None:
    """`score < MIN_AVG_SCORE` 不计入 `score_sum / score_n`（但仍计入 `support`）。"""
    agg = SemanticAggregator()
    low = MIN_AVG_SCORE - 0.1
    for _ in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, score=low)
    hit = agg.recommend(("heritage",))
    assert hit is not None, "低分不影响支持度，该放行还是要放行"
    assert hit.avg_score is None, "没有一条评分够格 → 不编造 avg_score"


def test_scores_at_or_above_threshold_are_accumulated() -> None:
    """`score == MIN_AVG_SCORE` 是**闭区间**下界：阈值本身算合格。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, score=MIN_AVG_SCORE)
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.avg_score == pytest.approx(MIN_AVG_SCORE)


def test_mixed_scores_average_only_the_qualified_ones() -> None:
    """高低分混报时，均值只由够格的那部分构成（低分行程不配推荐给别人）。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, score=5.0)
    agg.submit(LUOYANG_LIKE, score=1.0)  # 计入 support，不计入评分
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.avg_score == pytest.approx(5.0)


# ---------------------------------------------------------------- 排序与确定性


def test_support_is_the_primary_rank_key() -> None:
    """先按 `support` 降序：最常被走的下一步排在最前。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY + 2):
        agg.submit(("heritage", "nature"))
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "food"))
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"


def test_avg_score_breaks_support_ties() -> None:
    """支持度相同时，`avg_score` 更高的赢。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "nature"), score=4.5)
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "food"), score=5.0)
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "food"


def test_full_ties_are_broken_by_semantic_tags_order() -> None:
    """**完全平票**时按 `SEMANTIC_TAGS` 顺序裁决，与前端 `ALL_TAGS` 一致。

    两条候选的 support 与 avg_score 都相同，`nature` 在词表里比 `metropolis` 靠前。
    """
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "metropolis"))
        agg.submit(("heritage", "nature"))
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"
    assert SEMANTIC_TAGS.index("nature") < SEMANTIC_TAGS.index("metropolis")


def test_ranking_is_independent_of_submission_order() -> None:
    """先报哪一条不影响结果：排序键全确定性，不依赖 dict 的插入顺序。"""
    first = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        first.submit(("heritage", "nature"))
        first.submit(("heritage", "metropolis"))

    second = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        second.submit(("heritage", "metropolis"))
        second.submit(("heritage", "nature"))

    assert first.recommend(("heritage",)) == second.recommend(("heritage",))


# ---------------------------------------------------------------- confidence


def test_confidence_is_a_positive_ratio_capped_at_one() -> None:
    """`confidence` 落在 `(0, 1]`：分子分母都 > 0，且分母含全部候选所以不会溢出。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "nature"))
    for _ in range(3):
        agg.submit(("heritage", "food"))
    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"
    assert 0.0 < hit.confidence <= 1.0
    assert hit.confidence == pytest.approx(K_ANONYMITY / (K_ANONYMITY + 3))


def test_unpublished_candidates_stay_in_the_denominator() -> None:
    """分母是**全部**候选（含未达标）：confidence 反映的是"在观测到的全部走向里占多少"。

    若只统计达标模式，5/(5+3) 会被虚报成 5/5 = 1.0 —— 那是幸存者偏差，不是置信度。
    """
    agg = SemanticAggregator()
    unqualified = K_ANONYMITY - 2  # 3 条，未达门禁
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "nature"))
    for _ in range(unqualified):
        agg.submit(("heritage", "food"))

    hit = agg.recommend(("heritage",))
    assert hit is not None
    assert hit.tag == "nature"
    assert hit.confidence == pytest.approx(K_ANONYMITY / (K_ANONYMITY + unqualified))
    assert hit.confidence < 1.0


# ---------------------------------------------------------------- 聚合器本身


def test_stats_counts_published_patterns_only() -> None:
    """`stats()` 的 `published` 与门禁同源，不另算一套。"""
    agg = SemanticAggregator()
    assert agg.stats() == {"patterns": 0, "published": 0, "submissions": 0}
    for _ in range(K_ANONYMITY - 1):
        agg.submit(("heritage", "nature"))
    agg.submit(("heritage", "food"))
    assert agg.stats() == {"patterns": 2, "published": 0, "submissions": K_ANONYMITY}


def test_clear_resets_everything() -> None:
    """`clear()` 连 unique 账本一起清：换一轮试验时不能留下上轮的去重痕迹。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(LUOYANG_LIKE, user_key="alice")
    agg.clear()
    assert agg.stats() == {"patterns": 0, "published": 0, "submissions": 0}
    assert agg.recommend(("heritage",)) is None
    # 清掉之后，同一个 user_key 又能贡献一次（证明 `_seen` 确实被清了）
    assert agg.submit(LUOYANG_LIKE, user_key="alice") == 1


def test_query_longer_than_the_limit_is_truncated() -> None:
    """查询侧也截断：模式库里前缀最长 6 步，不截断就永远查不到东西。"""
    agg = SemanticAggregator()
    for _ in range(K_ANONYMITY):
        agg.submit(("heritage", "nature"))
    long_query = SEMANTIC_TAGS * 2  # 12 步
    assert agg.recommend(long_query) is None
    assert agg.recommend(("heritage",)) is not None


def test_build_aggregator_returns_the_same_instance() -> None:
    """`build_aggregator()` 是单例：API 层的多次上报必须汇进同一个匿名集。"""
    first = build_aggregator()
    try:
        assert build_aggregator() is first
    finally:
        first.clear()
