"""分块逻辑测试。"""

from app.agent.rag.chunker import ChunkConfig, chunk_text
from app.utils.text import estimate_tokens


def test_short_text_returns_single_chunk():
    """短文本保持原样，不要无意义地切一刀。"""
    text = "2025 年秋天去了乌镇和西塘。"
    assert chunk_text(text) == [text]


def test_long_text_is_split():
    sentences = [
        f"第 {i} 天，我们在{'乌镇' if i % 2 else '西塘'}拍了很多照片。" for i in range(200)
    ]
    chunks = chunk_text("".join(sentences), ChunkConfig(max_tokens=200))
    assert len(chunks) > 1


def test_chunks_have_overlap():
    """相邻块必须有重叠部分，否则跨块的语义会断掉。"""
    sentences = [f"这是第{i}句用于测试重叠的话。" for i in range(200)]
    chunks = chunk_text("".join(sentences), ChunkConfig(max_tokens=120, overlap_ratio=0.2))
    assert len(chunks) >= 2
    overlap_found = any(chunks[i][-8:] in chunks[i + 1] for i in range(len(chunks) - 1))
    assert overlap_found, "相邻 chunk 之间应当存在重叠内容"


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_estimate_tokens_grows_with_length():
    assert estimate_tokens("你好") < estimate_tokens("你好，今天我们去了乌镇和西塘古镇游玩")
