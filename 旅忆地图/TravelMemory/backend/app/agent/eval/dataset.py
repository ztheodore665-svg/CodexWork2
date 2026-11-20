"""评测集加载。

文件格式：每行一个 JSON（设计文档 §4.5 规定的 schema）：

```json
{"kind":"rag","question":"我去年秋天去过哪些古镇？",
 "expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}]}
{"kind":"refusal","question":"我 2019 年去过巴黎吗？","expected_answer":"拒答（无证据）"}
{"kind":"recommend","question":"去完洛阳该去哪？","note":"群体先验，阶段 7 才计入"}
```

kind 的含义决定统计口径：
  - rag       —— 计算 Recall@k / MRR（需要 expected_evidence）
  - refusal   —— 期望拒答，计算 refusal_correct
  - recommend —— 群体先验，不计入 citation_rate
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.schemas.document import DocumentRef


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


#: 默认评测集位置（backend/data/eval_cases.jsonl）
DEFAULT_DATASET_PATH = Path(__file__).resolve().parents[3] / "data" / "eval_cases.jsonl"

#: 中文时间表达用例（backend/data/time_cases.jsonl）
DEFAULT_TIME_CASES_PATH = Path(__file__).resolve().parents[3] / "data" / "time_cases.jsonl"

#: 工具选择用例（backend/data/tool_cases.jsonl，阶段 3 W3-1）
DEFAULT_TOOL_CASES_PATH = Path(__file__).resolve().parents[3] / "data" / "tool_cases.jsonl"


@dataclass
class EvalCaseItem:
    """一条评测题目。"""

    kind: str
    question: str
    expected_evidence: list[DocumentRef] = field(default_factory=list)
    expected_answer: str | None = None
    note: str | None = None

    @property
    def expected_keys(self) -> list[tuple[str, str, int]]:
        """期望命中的文档三元组集合（可能被划分到多个 chunk）。"""
        return [ref.key for ref in self.expected_evidence]


def load_dataset(path: Path | str | None = None) -> list[EvalCaseItem]:
    """读取评测集，跳过空行与 `#` 开头的注释行。"""
    dataset_path = Path(path) if path else DEFAULT_DATASET_PATH
    if not dataset_path.exists():
        raise FileNotFoundError(f"评测集不存在: {dataset_path}")

    items: list[EvalCaseItem] = []
    for lineno, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"评测集第 {lineno} 行不是合法 JSON: {exc}") from exc

        items.append(
            EvalCaseItem(
                kind=payload.get("kind", "rag"),
                question=payload["question"],
                expected_evidence=[
                    DocumentRef(
                        source_type=e["source_type"],
                        source_id=str(e["source_id"]),
                        chunk_index=int(e.get("chunk_index", 0)),
                    )
                    for e in payload.get("expected_evidence") or []
                ],
                expected_answer=payload.get("expected_answer"),
                note=payload.get("note"),
            )
        )
    return items


@dataclass
class TimeCaseItem:
    """一条中文时间表达用例（W2-1 的出口条件就是它全过）。

    为什么要自带 `today`：时间解析的结果依赖"今天"，写死日期才能让用例
    **在任何时候跑都得到同一个答案**。否则明年再跑一遍，所有期望值都变了，
    用例就从"回归测试"变成了"一次性快照"。
    """

    today: date
    question: str
    expected_start: date | None = None
    expected_end: date | None = None

    @property
    def expects_range(self) -> bool:
        return self.expected_start is not None and self.expected_end is not None


def load_time_cases(path: Path | str | None = None) -> list[TimeCaseItem]:
    """读取中文时间表达用例。"""
    dataset_path = Path(path) if path else DEFAULT_TIME_CASES_PATH
    if not dataset_path.exists():
        raise FileNotFoundError(f"时间表达用例不存在: {dataset_path}")

    items: list[TimeCaseItem] = []
    for lineno, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"时间用例第 {lineno} 行不是合法 JSON: {exc}") from exc
        items.append(
            TimeCaseItem(
                today=_parse_date(payload["today"]),
                question=payload["question"],
                expected_start=_parse_date(payload["start"]) if payload.get("start") else None,
                expected_end=_parse_date(payload["end"]) if payload.get("end") else None,
            )
        )
    return items


@dataclass
class ToolCaseItem:
    """一条工具选择用例。

    `expected` 为 `None` 表示**不该调工具**（负例）。负例和正例同等重要：
    只会"该调就调"的系统会把"我去年去过哪"也拿去查地图，既烧配额又答非所问。
    """

    question: str
    expected: str | None = None

    @property
    def expects_tool(self) -> bool:
        return self.expected is not None


def load_tool_cases(path: Path | str | None = None) -> list[ToolCaseItem]:
    """读取工具选择用例。"""
    dataset_path = Path(path) if path else DEFAULT_TOOL_CASES_PATH
    if not dataset_path.exists():
        raise FileNotFoundError(f"工具选择用例不存在: {dataset_path}")

    items: list[ToolCaseItem] = []
    for lineno, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"工具用例第 {lineno} 行不是合法 JSON: {exc}") from exc
        items.append(ToolCaseItem(question=payload["question"], expected=payload.get("expected")))
    return items


def group_by_kind(items: list[EvalCaseItem]) -> dict[str, list[EvalCaseItem]]:
    """按 kind 分组，便于分口径统计。"""
    grouped: dict[str, list[EvalCaseItem]] = {}
    for item in items:
        grouped.setdefault(item.kind, []).append(item)
    return grouped
