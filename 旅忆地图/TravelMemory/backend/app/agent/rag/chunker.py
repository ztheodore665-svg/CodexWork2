"""分块（离线索引期）。

设计文档 §7.2：文档超过 800 token 时用滑窗切块，overlap 10%。

为什么按句子切而不是按字符硬切：中文没有空格，硬切会把地名、日期从中间劈开，
检索时"我问的那句话"和"库里那句话"就对不上了。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.utils.text import estimate_tokens, split_sentences

DEFAULT_MAX_TOKENS = 800
DEFAULT_OVERLAP_RATIO = 0.1


@dataclass(frozen=True)
class ChunkConfig:
    max_tokens: int = DEFAULT_MAX_TOKENS
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO


def chunk_text(text: str, config: ChunkConfig | None = None) -> list[str]:
    """把长文本切成带重叠的若干块。短文本原样返回（不产生无意义的 #0）。"""
    cfg = config or ChunkConfig()
    text = text.strip()
    if not text:
        return []
    if estimate_tokens(text) <= cfg.max_tokens:
        return [text]

    sentences = split_sentences(text)
    overlap_tokens = max(1, int(cfg.max_tokens * cfg.overlap_ratio))

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    for sentence in sentences:
        tokens = estimate_tokens(sentence)
        if buffer and buffer_tokens + tokens > cfg.max_tokens:
            chunks.append("".join(buffer))
            # 取尾部若干句作为下一块的开头（overlap），让跨块的语义不断裂
            tail: list[str] = []
            tail_tokens = 0
            for s in reversed(buffer):
                t = estimate_tokens(s)
                if tail_tokens + t > overlap_tokens:
                    break
                tail.insert(0, s)
                tail_tokens += t
            buffer = tail
            buffer_tokens = tail_tokens
        buffer.append(sentence)
        buffer_tokens += tokens

    if buffer:
        chunks.append("".join(buffer))
    return chunks
