"""文本工具：token 估算、分句、截断。

放在 utils 而不是某个节点里，是因为分块（索引期）与引用检查（生成期）
需要完全一致的分句口径，两处各写一遍迟早会产生分歧。
"""

from __future__ import annotations

import re

# 中日韩统一表意文字区段
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 拉丁单词（含数字），用于英文/数字 token 估算
_WORD_RE = re.compile(r"[A-Za-z]+|\d+")
# 句子切分：连同结尾标点一起保留
_SENT_PATTERN = re.compile(r"[^。！？!？；;\n]*[。！？!？；;]+|[^。！？!？；;\n]+")


def estimate_tokens(text: str) -> int:
    """粗估 token 数。

    经验系数：中文一个字约 0.6 token，英文一个词约 1.3 token。
    够用于「是否超过 800 token 要切块」这类判断，不用于计费（计费以 LLM 返回 usage 为准）。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    words = len(_WORD_RE.findall(text))
    other = max(0, len(text) - cjk - sum(len(w) for w in _WORD_RE.findall(text)))
    return max(1, int(cjk * 0.6 + words * 1.3 + other * 0.3))


def split_sentences(text: str) -> list[str]:
    """按句号/问号/叹号/分号/换行切句，**保留标点**。

    保留标点是刻意的：分块后要能还原成可读文本，引用检查也需要准确的句子边界。
    注意 reference 检查与分块共用这一份实现，避免两处口径漂移。
    """
    if not text:
        return []
    return [p.strip() for p in _SENT_PATTERN.findall(text) if p.strip()]


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """按字符数截断（用于上下文预算之外的展示场景）。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


def dedupe_blank(text: str) -> str:
    """压缩连续空白，让入库内容不会因为换行使 embedding 抖动。"""
    return re.sub(r"\s+", " ", text).strip()
