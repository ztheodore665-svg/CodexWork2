"""加权 RRF 与分档排名的行为测试（W4-3，全程内存替身）。

守的是 W4-3 真实数据暴露的两个缺陷：

1. **词面路噪声淹没向量路**：词面覆盖率只有 1.0/0.5/0.0 三档，
   无差别 RRF 让弱匹配与强匹配的名次贡献几乎相同，MRR 从 0.778 掉到 0.679。
   → 修复：每路带权重。
2. **同分文档名次乱排**：1234 式排名让同一档内的文档只因 SQL 返回顺序拿到不同名次。
   → 修复：竞争排名（同分同名次）。
"""

from __future__ import annotations

import pytest

from app.agent.rag.retriever import _competition_ranks, reciprocal_rank_fusion
from app.schemas.document import DocumentRef, SearchHit


def _hit(source_id: str, score: float) -> SearchHit:
    return SearchHit(
        ref=DocumentRef(source_type="day", source_id=source_id, chunk_index=0),
        content=f"文档 {source_id}",
        score=score,
    )


# ---------------------------------------------------------------- 分档排名
def test_tied_scores_share_the_same_rank() -> None:
    """同分必须同名次：否则同一档内的先后只反映 SQL 返回顺序，没有语义。"""
    hits = [_hit("a", 1.0), _hit("b", 0.5), _hit("c", 0.5), _hit("d", 0.5)]
    pairs = _competition_ranks(hits)
    ranks = {hit.ref.source_id: rank for hit, rank in pairs}
    assert ranks == {"a": 1, "b": 2, "c": 2, "d": 2}


def test_tied_ranks_are_dense_not_skipping() -> None:
    """名次是 1,2,2,2 而不是 1,2,2,2 后面接 5 —— 1224 式（竞争排名）。"""
    hits = [_hit("a", 1.0), _hit("b", 1.0), _hit("c", 0.5)]
    ranks = [rank for _, rank in _competition_ranks(hits)]
    assert ranks == [1, 1, 3], f"竞争排名应是 1,1,3，实际 {ranks}"


def test_ranks_ignore_input_order() -> None:
    """同一批文档无论列表顺序如何，名次必须一致。"""
    hits = [_hit("a", 0.5), _hit("b", 1.0), _hit("c", 0.5)]
    forward = {h.ref.source_id: r for h, r in _competition_ranks(hits)}
    backward = {h.ref.source_id: r for h, r in _competition_ranks(list(reversed(hits)))}
    assert forward == backward


# ---------------------------------------------------------------- 加权
def test_zero_weight_path_is_ignored() -> None:
    """权重 0 = 这一路完全不参与，它的名次不该再影响结果。"""
    vec = [_hit("a", 0.9), _hit("b", 0.5)]
    lex = [_hit("b", 1.0), _hit("a", 0.5)]

    only_vec = reciprocal_rank_fusion([vec, lex], weights=[1.0, 0.0])

    assert [h.ref.source_id for h in only_vec] == ["a", "b"]
    assert only_vec[0].score == pytest.approx(1.0 / 61, abs=1e-6)


def test_lower_lexical_weight_keeps_vector_leader_on_top() -> None:
    """这是 W4-3 真实数据要求的核心行为：

    向量路第 1 名 + 词面路同档垫底，不应被"词面路第 1 名 + 向量路第 2 名"挤下去。
    降低词面权重后，向量路的领先必须保留。
    """
    # d1：向量第 1、词面与一批文档并列在第 2 档
    # d2：向量第 2、词面第 1（覆盖率 1.0）
    vec = [_hit("d1", 0.95), _hit("d2", 0.90)]
    lex = [_hit("d2", 1.0), _hit("d1", 0.5), _hit("d3", 0.5)]

    balanced = reciprocal_rank_fusion([vec, lex], weights=[1.0, 1.0])
    weighted = reciprocal_rank_fusion([vec, lex], weights=[0.65, 0.35])

    # 等权时两者**完全打平**：d1 是(向量1, 词面2)、d2 是(向量2, 词面1)，
    # 1/(k+1)+1/(k+2) == 1/(k+2)+1/(k+1)。谁在前取决于 sort 的稳定性，
    # 也就是 SQL 的返回顺序 —— 这就是真实数据里 MRR 从 0.778 掉到 0.679 的机制。
    assert balanced[0].score == pytest.approx(balanced[1].score, abs=1e-9)
    # 加权后向量路的领先重新体现出来，且顺序稳定
    assert weighted[0].ref.source_id == "d1"
    assert weighted[0].score > weighted[1].score


def test_weights_must_match_path_count() -> None:
    """权重与路数不一致必须报错：静默错配会让某一路的权重作用到另一路上。"""
    vec = [_hit("a", 0.9)]
    with pytest.raises(ValueError, match="权重数量"):
        reciprocal_rank_fusion([vec], weights=[1.0, 1.0])


def test_single_path_still_works() -> None:
    """只有一路（纯向量档）时 RRF 退化为该路的名次分，不能报错或丢结果。"""
    vec = [_hit("a", 0.9), _hit("b", 0.5)]
    out = reciprocal_rank_fusion([vec])
    assert [h.ref.source_id for h in out] == ["a", "b"]


def test_empty_input_returns_empty() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_default_weights_are_uniform() -> None:
    """不传 weights 时每路等权 —— 保持 RRF 的经典语义，便于与加权版对比。"""
    vec = [_hit("a", 0.9)]
    lex = [_hit("a", 0.5)]
    out = reciprocal_rank_fusion([vec, lex])
    assert out[0].score == pytest.approx(1 / 61 + 1 / 61, abs=1e-6)
