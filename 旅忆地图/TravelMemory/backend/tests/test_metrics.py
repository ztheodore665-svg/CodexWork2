"""检索指标测试。"""

from app.agent.eval.metrics import citation_rate, is_refusal, mrr, recall_at_k

EXPECTED = [("trip", "t1", 0)]


def test_recall_at_k_all_hit():
    retrieved = [("trip", "t1", 0), ("place", "p10", 0)]
    assert recall_at_k(retrieved, EXPECTED, k=5) == 1.0


def test_recall_at_k_missed():
    retrieved = [("place", "p10", 0), ("place", "p11", 0)]
    assert recall_at_k(retrieved, EXPECTED, k=5) == 0.0


def test_recall_respects_k():
    """超出 k 的部分不算数，否则指标会虚高。"""
    retrieved = [("place", "p10", 0), ("place", "p11", 0), ("trip", "t1", 0)]
    assert recall_at_k(retrieved, EXPECTED, k=2) == 0.0
    assert recall_at_k(retrieved, EXPECTED, k=3) == 1.0


def test_recall_returns_none_without_expected():
    assert recall_at_k([("trip", "t1", 0)], [], k=5) is None


def test_mrr_uses_first_hit_rank():
    retrieved = [("place", "p10", 0), ("trip", "t1", 0)]
    assert mrr(retrieved, EXPECTED) == 0.5


def test_mrr_zero_when_no_hit():
    assert mrr([("place", "p10", 0)], EXPECTED) == 0.0


def test_is_refusal_matches_standard_wording():
    assert is_refusal(
        "抱歉，我在你的照片和行程记录里没有找到能支撑这个问题的证据，所以不替你编一个答案。"
    )
    assert not is_refusal("你在 2025 年去过乌镇[1]。")


def test_citation_rate_matches_citation_utils():
    """评测里的引用率必须和 self_check 同口径（都走 utils/citation）。"""
    assert citation_rate("去过乌镇[1]。其他地方也去过。") == 0.5
