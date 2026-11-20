"""上下文构建：把检索结果变成「LLM 可读且带编号」的证据块，
并按设计文档 §7.4 的**固定顺序**装配记忆与历史。

```
[系统指令]              ← generate 节点的 system message
[用户偏好 / 语义记忆]    ← 本模块（W4-1）
[当前行程上下文]         ← 本模块（W4-1，短期 + 情景层）
[检索证据]              ← 本模块（带 [n] 编号）
[群体语义先验]          ← 阶段 7（无编号、禁止引用）
[对话历史]              ← 本模块（W4-3 压缩的最先牺牲品）
[用户问题]              ← generate 的 user prompt 末尾
```

顺序本身不要动 —— 位置变化会直接影响模型对"能不能引用"的判断：
把记忆放在证据之后，模型会误以为偏好也是可引用的材料，
从而产出 `[1]` 指向一条根本不存在的证据。

## W4-3：字符预算与压缩优先级

优先级 **证据 > 历史对话 > 记忆**。理由：

- 证据是引用率 100% 的物质基础，砍它等于直接制造拒答；
- 历史对话提供指代消解线索，砍掉只会让回答略微"断片"；
- 记忆是调味，砍掉回答依然成立，只是没那么个性化。

压缩必须**整块丢弃而不截断单行**：半句话的偏好（"用户不喜欢人"）比没有更糟。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.state import EvidenceBlock
from app.core.logging import get_logger
from app.schemas.document import SearchHit
from app.schemas.memory import MemoryContext
from app.utils.citation import CITATION_RE

logger = get_logger(__name__)

#: 元数据里用来给模型看的「人类可读线索」，按展示优先级排列
_META_LABELS = {
    "date_start": "开始",
    "date_end": "结束",
    "city": "城市",
    "places": "地点",
    "themes": "主题",
    "trip_title": "行程",
}

#: 预算下限。配成 0 或负数时按它兜底 ——
#: 「上下文为 0 字符」不是"极致压缩"，而是把每条问答都变成拒答
MIN_CONTEXT_CHARS = 200

#: 记忆区块的标题（**必须带方括号但与引用编号区分**：引用只认 `[数字]`）
PREFERENCE_HEADER = "[用户偏好]"
TRIP_CONTEXT_HEADER = "[当前行程上下文]"
HISTORY_HEADER = "[对话历史]"


@dataclass
class BuiltContext:
    blocks: list[EvidenceBlock]
    text: str
    #: 是否因预算被截断（写进 trace，便于发现"证据被吃掉"导致的拒答）
    truncated: bool
    dropped: int
    #: 被整块丢弃的区块名（W4-3 压缩的可观测出口，排障时第一眼看它）
    dropped_parts: list[str] = field(default_factory=list)
    #: 记忆 / 历史各自因超预算少渲染了几条
    dropped_memory_items: int = 0
    dropped_history_turns: int = 0


def build_context(
    hits: list[SearchHit],
    *,
    max_chars: int = 4000,
    memory: MemoryContext | None = None,
    history: list[dict[str, str]] | None = None,
) -> BuiltContext:
    """按分数顺序编号，并在字符预算内按优先级装配各区块。

    编号必须在**这里**定下来：模型输出里的 [n] 就是这里的 index，
    一旦前后编号不一致，引用就全部错位。
    """
    budget = max(int(max_chars), MIN_CONTEXT_CHARS)

    # ---- 第 1 优先：证据 ----
    blocks: list[EvidenceBlock] = []
    lines: list[str] = []
    used = 0
    dropped = 0

    for hit in hits:
        index = len(blocks) + 1
        line = _format_block(index, hit)
        if blocks and used + len(line) > budget:
            # 至少保留一条；超出预算的直接丢弃（保证上下文可控，宁缺毋滥）
            dropped = len(hits) - index + 1
            break
        blocks.append(
            EvidenceBlock(
                index=index,
                ref=hit.ref,
                content=hit.content,
                score=hit.score,
                meta=dict(hit.metadata),
            )
        )
        lines.append(line)
        used += len(line)

    if not blocks:
        empty = BuiltContext(blocks=[], text="", truncated=False, dropped=len(hits))
        # 没有证据时，记忆与历史都不该进上下文：
        # 一旦模型在"无证据"的提示下又看到一堆用户偏好，很容易拿偏好去编答案
        return empty

    evidence_text = "\n\n".join(lines)
    dropped_parts: list[str] = []
    dropped_memory_items = 0
    dropped_history_turns = 0

    # ---- 第 2 优先：历史对话 ----
    history_text, dropped_history_turns = _fit_history(history, budget - len(evidence_text))
    if history and not history_text:
        dropped_parts.append(HISTORY_HEADER)

    # ---- 第 3 优先：记忆（最不重要，最后分配剩余预算）----
    remaining = budget - len(evidence_text) - len(history_text)
    memory_text, dropped_memory_items = _fit_memory(memory, remaining)
    if memory is not None and not memory.is_empty() and not memory_text:
        dropped_parts.append(PREFERENCE_HEADER)

    sections = [memory_text, evidence_text, history_text]
    text = "\n\n".join(part for part in sections if part)

    return BuiltContext(
        blocks=blocks,
        text=text,
        truncated=dropped > 0 or bool(dropped_parts),
        dropped=dropped,
        dropped_parts=dropped_parts,
        dropped_memory_items=dropped_memory_items,
        dropped_history_turns=dropped_history_turns,
    )


# ---------------------------------------------------------------- 区块装配
def _fit_memory(memory: MemoryContext | None, budget: int) -> tuple[str, int]:
    """在预算内渲染记忆区块；放不下就**整条丢弃**，绝不截断单行。

    返回 `(渲染文本, 少渲染的条数)`。

    降级保护（PROJECT.md §二、[8]：失败必须降级而非抛错）：
    任何异常都退回"不渲染记忆"，绝不让记忆装配把整个上下文带崩 ——
    记忆是加分项，为了它让问答 500 是完全不划算的。
    """
    if memory is None or memory.is_empty() or budget <= 0:
        return "", 0
    try:
        sections: list[tuple[str, list[str]]] = []
        if memory.preferences:
            sections.append((PREFERENCE_HEADER, list(memory.preferences)))
        if memory.trip_context:
            sections.append((TRIP_CONTEXT_HEADER, list(memory.trip_context)))
        total = sum(len(items) for _header, items in sections)

        rendered: list[str] = []
        used = 0
        kept = 0
        for header, items in sections:
            lines = [header]
            for item in items:
                line = str(item)
                if not line.startswith("- "):
                    line = f"- {line}"
                # +len(lines) 是 "\n".join 的分隔符开销（每个已有行后面一个换行）
                if sum(len(x) for x in lines) + len(line) + len(lines) > budget - used:
                    continue
                lines.append(line)
                kept += 1
            if len(lines) == 1:
                # 一条都没塞进去（连标题都放不下）→ 整块丢弃，而不是只留个空标题
                continue
            text = "\n".join(lines)
            cost = len(text) + (2 if rendered else 0)
            if used + cost > budget:
                continue
            used += cost
            rendered.append(text)

        if not rendered:
            return "", total
        return "\n\n".join(rendered), total - kept
    except Exception:  # noqa: BLE001 - 降级保护：记忆装配失败不影响主流程
        logger.warning("记忆区块装配失败，本次不带记忆作答", exc_info=True)
        return "", 0


def _fit_history(history: list[dict[str, str]] | None, budget: int) -> tuple[str, int]:
    """在预算内渲染历史（放不下就丢最早的几轮，而不是截断某一轮）。

    **必须剥掉历史里的 `[n]`**：上一轮的编号指的是**上一轮的证据**，
    放进本轮上下文后，模型极可能照抄 `[3]`，而本轮可能只有 2 条证据 ——
    于是 self_check 报"引用越界"，用户看到一次莫名其妙的拒答。
    编号在上下文里只能有一个含义：本轮证据。
    """
    if not history or budget <= 0:
        return "", 0
    lines: list[str] = []
    used = 0
    dropped = 0
    for turn in list(reversed(history)):
        role = "用户" if turn.get("role") == "user" else "助手"
        line = f"{role}：{CITATION_RE.sub('', str(turn.get('content', ''))).strip()}"
        if lines and used + len(line) > budget:
            dropped += 1
            continue
        if not lines and len(line) > budget:
            dropped += 1
            continue
        lines.append(line)
        used += len(line)
    if not lines:
        return "", len(history)
    return f"{HISTORY_HEADER}\n" + "\n".join(reversed(lines)), dropped


def _format_block(index: int, hit: SearchHit) -> str:
    """单条证据的文本形式：`[n] 来源 / 元信息 + 原文`。"""
    head = f"[{index}] 来源={hit.ref}"
    metas = _format_meta(hit.metadata)
    if metas:
        head += f"（{metas}）"
    head += f" 相似度={hit.score:.3f}"
    return f"{head}\n{hit.content}"


def _format_meta(metadata: dict) -> str:
    """把结构化元信息压成一行短标签。

    目的：让模型一眼看到时间与地点线索，同时不占用过多上下文预算。
    """
    parts: list[str] = []
    for key, label in _META_LABELS.items():
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        parts.append(f"{label}={value}")
    return " ".join(parts)
