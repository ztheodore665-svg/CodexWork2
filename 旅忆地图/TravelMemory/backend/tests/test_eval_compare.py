"""阶段 5 W5-2 归因对比测试：纯数据构造，不连库、不碰 ORM。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.eval.compare import (
    ResultRecord,
    build_compare_rows,
    percentile,
    render_compare_markdown,
)


def _record(
    *,
    version: str = "pv1",
    model: str = "bge-m3",
    recall: float | None = 1.0,
    latency: int = 100,
    created_at: datetime | None = None,
    faithfulness: float | None = None,
) -> ResultRecord:
    return ResultRecord(
        prompt_version=version,
        embedding_model=model,
        recall_at_k=recall,
        mrr=recall,
        citation_rate=recall,
        refusal_correct=recall,
        faithfulness=faithfulness,
        latency_ms=latency,
        created_at=created_at or datetime(2026, 10, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------- 分组
def test_groups_by_version_and_model():
    records = [
        _record(version="pv1", model="m1"),
        _record(version="pv2", model="m1"),
        _record(version="pv2", model="m2"),
    ]
    rows = build_compare_rows(records)
    assert len(rows) == 3
    assert [row.n for row in rows] == [1, 1, 1]
    assert {(r.prompt_version, r.embedding_model) for r in rows} == {
        ("pv1", "m1"),
        ("pv2", "m1"),
        ("pv2", "m2"),
    }


def test_mean_skips_none():
    rows = build_compare_rows([_record(recall=1.0), _record(recall=None)])
    assert rows[0].n == 2
    assert rows[0].recall_at_k == 1.0


# ---------------------------------------------------------------- P95 最近秩
def test_percentile_nearest_rank():
    assert percentile([float(x) for x in range(10, 101, 10)], 0.95) == 100.0
    assert percentile([1.0, 2.0, 3.0], 0.95) == 3.0
    assert percentile([], 0.95) is None
    assert percentile([7.0], 0.95) == 7.0


# ---------------------------------------------------------------- 箭头方向
def test_arrows_follow_metric_direction():
    rows = build_compare_rows(
        [
            _record(version="pv1", recall=0.8, latency=100),
            _record(version="pv2", recall=0.9, latency=200),
        ]
    )
    assert rows[1].deltas["recall_at_k"] == "↑"  # 越大越好：进步
    assert rows[1].deltas["p95_latency_ms"] == "↓"  # 越小越好：退步

    rows = build_compare_rows(
        [
            _record(version="pv1", recall=0.9, latency=200),
            _record(version="pv2", recall=0.8, latency=100),
        ]
    )
    assert rows[1].deltas["recall_at_k"] == "↓"
    assert rows[1].deltas["p95_latency_ms"] == "↑"


def test_first_group_and_equal_or_none_have_no_arrow():
    rows = build_compare_rows(
        [
            _record(version="pv1", recall=0.8),
            _record(version="pv2", recall=0.8),  # 相等
        ]
    )
    assert rows[0].deltas == {}
    assert rows[1].deltas == {}

    rows = build_compare_rows(
        [
            _record(version="pv1", faithfulness=None),  # 上一轮没开 judge
            _record(version="pv2", faithfulness=1.0),
        ]
    )
    assert "faithfulness" not in rows[1].deltas


# ---------------------------------------------------------------- 排序
def test_order_follows_first_seen_not_insertion():
    rows = build_compare_rows(
        [
            _record(version="pv2", created_at=datetime(2026, 10, 5, tzinfo=UTC)),
            _record(version="pv1", created_at=datetime(2026, 10, 1, tzinfo=UTC)),
        ]
    )
    assert [r.prompt_version for r in rows] == ["pv1", "pv2"]


# ---------------------------------------------------------------- 渲染
def test_render_header_and_none_and_arrows():
    rows = build_compare_rows(
        [
            _record(version="pv1", recall=0.8, latency=100),
            _record(version="pv2", recall=0.9, latency=100, faithfulness=None),
        ]
    )
    text = render_compare_markdown(rows)
    lines = text.splitlines()
    assert (
        lines[0]
        == "| prompt_version | embedding_model | n | Recall@5 | MRR | 引用率 | 拒答正确率 | 忠实度 | P95延迟ms |"
    )
    assert lines[1] == "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    # 上一轮没评 judge → 忠实度列渲染成 `-`
    assert "| - |" in lines[2]
    # 箭头落在对应的指标单元格
    assert "0.900 ↑" in lines[3]


def test_render_color_uses_ansi():
    rows = build_compare_rows(
        [
            _record(version="pv1", recall=0.8),
            _record(version="pv2", recall=0.9),
        ]
    )
    colored = render_compare_markdown(rows, color=True)
    assert "\033[32m↑\033[0m" in colored

    rows_down = build_compare_rows(
        [
            _record(version="pv1", recall=0.9),
            _record(version="pv2", recall=0.8),
        ]
    )
    assert "\033[31m↓\033[0m" in render_compare_markdown(rows_down, color=True)


def test_render_empty():
    assert render_compare_markdown([]) == "暂无历史评测结果，先跑一次：make eval"
