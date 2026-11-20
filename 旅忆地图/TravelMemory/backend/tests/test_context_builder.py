"""上下文构建测试：编号与预算是最容易出错的两点。"""

from app.agent.rag.context_builder import build_context
from app.schemas.document import DocumentRef, SearchHit


def _hit(source_type: str, source_id: str, content: str, score: float = 0.9) -> SearchHit:
    return SearchHit(
        ref=DocumentRef(source_type=source_type, source_id=source_id),
        content=content,
        metadata={"city": "嘉兴"},
        score=score,
    )


def test_blocks_are_numbered_from_one():
    hits = [_hit("trip", "t1", "乌镇夜景"), _hit("place", "p10", "乌镇景区")]
    built = build_context(hits)
    assert [b.index for b in built.blocks] == [1, 2]
    assert "[1]" in built.text and "[2]" in built.text


def test_context_respects_char_budget():
    hits = [_hit("trip", f"t{i}", "内容" * 200) for i in range(10)]
    built = build_context(hits, max_chars=500)
    assert built.truncated is True
    assert len(built.blocks) >= 1
    assert len(built.text) <= 500 + 400  # 允许最后一条略微超出预算（保证至少一条证据）
    assert built.dropped > 0


def test_higher_score_keeps_order():
    hits = [_hit("trip", "low", "低分", score=0.4), _hit("trip", "high", "高分", score=0.9)]
    built = build_context(hits)
    # 入参已按分数排好，编号必须跟随该顺序，否则引用会错位
    assert built.blocks[0].ref.source_id == "low"
    assert built.blocks[1].ref.source_id == "high"


def test_empty_hits():
    built = build_context([])
    assert built.blocks == []
    assert built.text == ""
    assert built.truncated is False


def test_metadata_is_shown_to_model():
    hits = [_hit("trip", "t1", "乌镇夜景", score=0.88)]
    built = build_context(hits)
    assert "城市=嘉兴" in built.text
    assert "相似度=0.880" in built.text
