"""从用户原话里**抽取偏好**（阶段 4 W4-1 的写回策略之一）。

## 为什么这一版是规则而不是 LLM

设计文档 §7.5 的写回策略是三条：每 8 轮生成一次会话摘要、用户明确表达偏好时抽取、
行程结束后生成复盘。其中"用户明确表达偏好"这一类**句式高度固定**
（"我不喜欢人多""我偏好自驾"），规则抽取有三个 LLM 给不了的好处：

1. **确定性**：单测能钉死，不必 mock 大模型（PROJECT.md 红线：测试不依赖真实外部 API）
2. **零成本**：写回发生在每一轮之后，用 LLM 等于每轮多烧一次调用
3. **可解释**：抽错了能直接看正则，而不是去猜模型当时怎么想的

阶段 5 可以在规则之上加一层 LLM 抽取作为补充，但规则层要保留 ——
它同时是 LLM 抽取的**兜底**：模型挂了，偏好依然能记住。

## 抽取的克制原则

宁可少抽，不要错抽。判断不出明确偏好时返回 None：
把"我今天心情不错"记成一条"用户喜欢心情不错"的长期偏好，是灾难性的噪声。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.memory import LAYER_SEMANTIC, MemoryDraft, MemorySourceRef

#: 明确的偏好动词。「讨厌 / 反感」自带否定极性，其余动词靠前缀的否定词判断
_VERB = (
    "不喜欢|不太喜欢|讨厌|反感|不爱|不想要|不想|不愿|不敢|不吃|不喝|不去|不坐|不住"
    "|喜欢|喜爱|爱|偏好|偏爱|习惯|想要|想|愿意|吃|喝|去|坐|住"
)

#: 主模式：主语「我」+ 可选否定 + 可选程度副词 + 偏好动词 + 偏好对象
_PREFERENCE_RE = re.compile(
    rf"我(?P<degree>很|最|比较|特别|挺|超|非常)?(?P<verb>{_VERB})(?P<obj>[^，。！？；,.!?;]{{1,24}})"
)

#: 动词自带的否定极性
_NEGATIVE_VERBS = {
    "不喜欢",
    "不太喜欢",
    "讨厌",
    "反感",
    "不爱",
    "不想要",
    "不想",
    "不愿",
    "不敢",
    "不吃",
    "不喝",
    "不去",
    "不坐",
    "不住",
}

#: 疑问句不抽：「你喜欢人多吗？」不是用户的偏好陈述
_QUESTION_MARKERS = ("吗", "呢", "？", "?")

#: 明显的非偏好对象（代词 / 泛词），抽到就丢弃
_STOP_OBJECTS = {"这个", "那个", "它", "他", "她", "你", "你们", "我们", "大家", "自己"}


@dataclass(frozen=True)
class ExtractedPreference:
    """一条被抽出来的偏好。"""

    key: str
    value: str
    negative: bool

    def to_draft(
        self, *, half_life_days: float | None = None, sources: list[MemorySourceRef] | None = None
    ) -> MemoryDraft:
        return MemoryDraft(
            layer=LAYER_SEMANTIC,
            key=self.key,
            value=self.value,
            half_life_days=half_life_days,
            sources=sources or [],
        )


def extract_preference(text: str) -> ExtractedPreference | None:
    """从一句话里抽偏好；抽不到就返回 None（**克制原则**）。"""
    if not text or not text.strip():
        return None
    sentence = text.strip()
    if any(marker in sentence for marker in _QUESTION_MARKERS):
        return None

    match = _PREFERENCE_RE.search(sentence)
    if match is None:
        return None

    verb = match.group("verb")
    obj = _clean_object(match.group("obj"))
    if not obj:
        return None

    negative = verb in _NEGATIVE_VERBS
    key = f"pref.{_slug(obj)}"
    value = f"用户{'不喜欢' if negative else '喜欢'}{obj}"
    return ExtractedPreference(key=key, value=value, negative=negative)


def should_summarize_session(turn_count: int, *, every: int = 8) -> bool:
    """是否到了写会话摘要的时机（设计文档 §7.5：每 8 轮一次）。

    `turn_count` 从 1 开始计数；未轮到时返回 False，绝不"顺便写一次" ——
    摘要是要写进情景记忆的，写多了库里全是流水账。
    """
    if every <= 0:
        raise ValueError(f"every 必须为正数，收到 {every}")
    return turn_count > 0 and turn_count % every == 0


def _clean_object(raw: str) -> str:
    """清洗偏好对象：去语气词与尾部的"了/的"，去掉代词。"""
    cleaned = raw.strip()
    # 去掉尾部语气助词
    cleaned = re.sub(r"(了|的|呀|啊|哦|吧)$", "", cleaned).strip()
    # 去掉开头的"太/很"之类（"很喜欢热闹"里的"很"已在模式里吃掉，这里防漏网）
    cleaned = re.sub(r"^(太|挺|很|超)", "", cleaned).strip()
    if not cleaned or cleaned in _STOP_OBJECTS:
        return ""
    return cleaned


def _slug(text: str) -> str:
    """把中文对象压成稳定的 key 片段（只保留中英数，其余折叠为 `_`）。"""
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_")
    return slug or "unknown"
