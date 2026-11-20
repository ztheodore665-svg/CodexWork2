"""引用工具测试：这是「引用率 100%」这条硬指标的地基。"""

from app.utils.citation import (
    assertive_sentences,
    citation_rate,
    extract_citation_indices,
    is_soft_sentence,
    out_of_range_indices,
)


def test_extract_citation_indices():
    assert extract_citation_indices("去过乌镇[1]和西塘[2]，还重访了乌镇[1]。") == [1, 2, 1]


def test_out_of_range_indices():
    assert out_of_range_indices("去过乌镇[1]和某个地方[5]", max_index=3) == [5]
    assert out_of_range_indices("去过乌镇[1]", max_index=3) == []


def test_soft_sentence_not_counted():
    assert is_soft_sentence("抱歉，这条没有记录。")
    assert not is_soft_sentence("你在 2025 年去过乌镇。")


def test_assertive_sentences_filters_soft_ones():
    text = "你在 2025 年去过乌镇。抱歉，剩下那段没有记录。"
    assert assertive_sentences(text) == ["你在 2025 年去过乌镇。"]


def test_citation_rate_full_when_all_cited():
    answer = "你在 2025-10-02 去了乌镇[1]。西塘是第二站[2]。"
    assert citation_rate(answer) == 1.0


def test_citation_rate_detects_uncited_fact():
    answer = "你在 2025-10-02 去了乌镇[1]。然而你在 2024 年还去过别的地方。"
    assert citation_rate(answer) == 0.5


def test_citation_rate_of_empty_answer_is_one():
    """没有事实句时不该让指标崩掉，正确性由拒答指标单独判断。"""
    assert citation_rate("") == 1.0
