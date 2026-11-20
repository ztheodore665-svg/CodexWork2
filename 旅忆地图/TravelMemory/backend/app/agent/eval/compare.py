"""版本归因对比（纯函数，不碰数据库）。

评测结果只有和当时的 `prompt_version` + `embedding_model` 绑在一起才有意义：
改完 prompt 再跑一次，指标变好还是变坏，靠的是**和上一次对比**，
而不是绝对值（绝对值还受数据、天气、模型抽风影响）。

本模块把「分组 → 聚合 → 环比 → 渲染」做成纯函数，
因此 `tests/test_eval_compare.py` 不需要数据库、不需要 Docker，
就能把整张表的行为钉死。从 ORM 搬数据的脏活留在 `eval/runner.py`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

#: 指标元数据：名字 + 是否「越大越好」。表驱动而不是一堆 if/else ——
#: 加一个指标只改这一行，箭头方向、渲染表头跟着走，不会漏。
_METRICS: tuple[tuple[str, bool], ...] = (
    ("recall_at_k", True),
    ("mrr", True),
    ("citation_rate", True),
    ("refusal_correct", True),
    ("faithfulness", True),
    ("p95_latency_ms", False),  # 延迟越小越好
)

#: Markdown 表头（渲染的契约，测试直接断言这一行）
_HEADER = "| prompt_version | embedding_model | n | Recall@5 | MRR | 引用率 | 拒答正确率 | 忠实度 | P95延迟ms |"
_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
_EMPTY_MESSAGE = "暂无历史评测结果，先跑一次：make eval"

#: 环比箭头方向
_ARROW_UP = "↑"
_ARROW_DOWN = "↓"


@dataclass
class ResultRecord:
    """一条历史评测结果（runner 负责从 EvalResult ORM 对象搬过来）。"""

    prompt_version: str
    embedding_model: str
    recall_at_k: float | None = None
    mrr: float | None = None
    citation_rate: float | None = None
    refusal_correct: float | None = None
    faithfulness: float | None = None
    latency_ms: int = 0
    created_at: datetime | None = None


@dataclass
class CompareRow:
    """对比表的一行 = 一个 (prompt_version, embedding_model) 分组的聚合。"""

    prompt_version: str
    embedding_model: str
    n: int
    recall_at_k: float | None
    mrr: float | None
    citation_rate: float | None
    refusal_correct: float | None
    faithfulness: float | None
    #: P50 常驻分组行（不只服务于看板）：中位数才是"典型一次请求有多慢"，
    #: P95 回答的是"最慢的 5% 有多慢"。只看 P95 会以为系统整体很慢，
    #: 只看 P50 会漏掉尾延迟失控 —— 两个一起看才不会误判。
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    #: 环比箭头，key 与 _METRICS 中的指标名一致；首组为空
    deltas: dict[str, str] = field(default_factory=dict)


def percentile(values: list[float], q: float) -> float | None:
    """最近秩（nearest-rank）分位数：rank = max(1, ceil(q * len(ordered)))。

    为什么不用线性插值：P95 的用途是「最慢的 5% 到底有多慢」，
    插值法在样本少时会给出一个从未出现过的值 ——
    3 个样本的"P95"是第 2.85 个，那个延迟没有任何一次真实请求对应，
    拿它做告警阈值会永远等不到触发。
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _mean_or_none(values: list[float | None]) -> float | None:
    """均值，None 不参与；全 None 返回 None（而不是 0.0，那是把"没数据"算成 0 分）。"""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def _arrows(prev: CompareRow, curr: CompareRow) -> dict[str, str]:
    """环比箭头：prev → curr，依 _METRICS 判方向。

    不给箭头的情况（而不是硬给一个"持平"符号）：首组没有对比对象；
    任一侧为 None（比如上一轮没开 judge，忠实度不可比）；
    两侧完全相等（延迟这种事，持平才是常态，满屏 → 等于没有信息）。
    """
    deltas: dict[str, str] = {}
    for name, higher_better in _METRICS:
        before = getattr(prev, name)
        after = getattr(curr, name)
        if before is None or after is None or before == after:
            continue
        improved = after > before if higher_better else after < before
        deltas[name] = _ARROW_UP if improved else _ARROW_DOWN
    return deltas


def _sort_key(item: tuple[tuple[str, str], list[ResultRecord]]) -> tuple[str, str, str]:
    (version, model), records = item
    # 首现 created_at 的 isoformat 字符串排序：datetime 本身不可跨 None 比较，
    # 而 isoformat 是字典序单调的（同年同格式下字符序 = 时间序），None 用 "" 兜底排最前
    first_seen = min(
        (r.created_at.isoformat() for r in records if r.created_at is not None),
        default="",
    )
    return (first_seen, version, model)


def build_compare_rows(records: list[ResultRecord]) -> list[CompareRow]:
    """把扁平结果聚合成按时间排序的对比行。

    分组键 = (prompt_version, embedding_model)；组间按**首现 created_at** 排序
    （并列时按 version、model 字典序）——评测是顺序跑的，首现时间就是版本的先后。
    """
    groups: dict[tuple[str, str], list[ResultRecord]] = {}
    for record in records:
        groups.setdefault((record.prompt_version, record.embedding_model), []).append(record)

    rows: list[CompareRow] = []
    previous: CompareRow | None = None
    for (version, model), group in sorted(groups.items(), key=_sort_key):
        row = CompareRow(
            prompt_version=version,
            embedding_model=model,
            n=len(group),
            recall_at_k=_mean_or_none([r.recall_at_k for r in group]),
            mrr=_mean_or_none([r.mrr for r in group]),
            citation_rate=_mean_or_none([r.citation_rate for r in group]),
            refusal_correct=_mean_or_none([r.refusal_correct for r in group]),
            faithfulness=_mean_or_none([r.faithfulness for r in group]),
            p50_latency_ms=percentile([float(r.latency_ms) for r in group], 0.50),
            p95_latency_ms=percentile([float(r.latency_ms) for r in group], 0.95),
        )
        if previous is not None:
            row.deltas = _arrows(previous, row)
        rows.append(row)
        previous = row
    return rows


def _fmt(value: float | None, *, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _cell(row: CompareRow, name: str, *, digits: int, color: bool) -> str:
    """指标单元格：数值 + 可选环比箭头。

    颜色只包箭头不包数字：终端里整格变色会让表格对齐看起来错乱，
    而箭头本身就是唯一的"变化信号"，值得高亮。
    """
    value = getattr(row, name)
    text = _fmt(value, digits=digits)
    arrow = row.deltas.get(name)
    if arrow is None:
        return text
    if not color:
        return f"{text} {arrow}"
    if arrow == _ARROW_UP:
        return f"{text} \033[32m{_ARROW_UP}\033[0m"
    return f"{text} \033[31m{_ARROW_DOWN}\033[0m"


def render_compare_markdown(rows: list[CompareRow], *, color: bool = False) -> str:
    """渲染 Markdown 归因对比表。↑ 恒为进步、↓ 恒为退步（方向由 _arrows 保证）。"""
    if not rows:
        return _EMPTY_MESSAGE

    lines = [_HEADER, _SEPARATOR]
    for row in rows:
        cells = [
            row.prompt_version,
            row.embedding_model,
            str(row.n),
            _cell(row, "recall_at_k", digits=3, color=color),
            _cell(row, "mrr", digits=3, color=color),
            _cell(row, "citation_rate", digits=3, color=color),
            _cell(row, "refusal_correct", digits=3, color=color),
            _cell(row, "faithfulness", digits=3, color=color),
            _cell(row, "p95_latency_ms", digits=0, color=color),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
