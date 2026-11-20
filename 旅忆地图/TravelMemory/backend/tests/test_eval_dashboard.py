"""阶段 5 W5-3 看板测试：纯数据构造，不连库、不读文件系统。

钉的是三件事：
  1. 聚合口径（卡片取最新一组、P50/P95 用最近秩、箭头方向与 compare 一致）
  2. 渲染契约（汇总卡片 / 归因表 / 延迟表 / 内嵌 JSON 都在）
  3. 逃逸（分组名来自数据库，等于不可信输入，不许冲出 `<script>` / HTML）
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.agent.eval.compare import ResultRecord
from app.agent.eval.dashboard import (
    build_dashboard_data,
    render_dashboard_html,
)

# ---------------------------------------------------------------- 数据构造
_BASE = datetime(2026, 10, 1, tzinfo=UTC)


def _record(
    *,
    version: str = "pv1",
    model: str = "bge-m3@v1",
    recall: float | None = 0.8,
    latency: int = 100,
    created_at: datetime | None = None,
    faithfulness: float | None = None,
) -> ResultRecord:
    return ResultRecord(
        prompt_version=version,
        embedding_model=model,
        recall_at_k=recall,
        mrr=recall,
        citation_rate=1.0,
        refusal_correct=1.0,
        faithfulness=faithfulness,
        latency_ms=latency,
        created_at=created_at or _BASE,
    )


def _two_groups() -> list[ResultRecord]:
    """pv1（慢、弱）→ pv2（快、强），用来验证卡片取最新一组 + 箭头方向。"""
    return [
        _record(version="pv1", recall=0.60, latency=200, created_at=_BASE),
        _record(version="pv2", recall=0.90, latency=100, created_at=_BASE.replace(minute=5)),
    ]


# ---------------------------------------------------------------- 聚合
def test_empty_data_is_flagged_not_crashing():
    data = build_dashboard_data([])
    assert data.is_empty
    assert data.latest is None
    assert data.total_runs == 0
    html_text = render_dashboard_html(data)
    assert "暂无历史评测结果" in html_text
    assert "make eval" in html_text


def test_cards_take_latest_group_not_global_mean():
    """跨版本求平均会把两套行为混成一个数 —— 卡片必须取最新一组。"""
    data = build_dashboard_data(_two_groups())
    assert data.latest is not None
    assert data.latest.prompt_version == "pv2"
    recall_card = next(c for c in data.cards if c.label == "平均 Recall@5")
    assert recall_card.value == "0.900"  # 不是 (0.6+0.9)/2 = 0.75


def test_card_count_shows_latest_n_and_total_runs():
    records = _two_groups() + [_record(version="pv2", created_at=_BASE.replace(minute=6))]
    data = build_dashboard_data(records)
    count_card = next(c for c in data.cards if c.label == "总用例数")
    assert count_card.value == "2"  # 最新一组 2 条
    assert data.total_runs == 3  # 历史累计 3 条
    assert "历史累计 3 条运行记录" in count_card.hint


def test_p50_and_p95_use_nearest_rank():
    """4 个样本：P50 = 第 ceil(0.5*4)=2 个，P95 = 第 ceil(0.95*4)=4 个。"""
    records = [
        _record(version="pv1", latency=100),
        _record(version="pv1", latency=200),
        _record(version="pv1", latency=300),
        _record(version="pv1", latency=900),
    ]
    data = build_dashboard_data(records)
    row = data.groups[0]
    assert row.p50_latency_ms == 200
    assert row.p95_latency_ms == 900


def test_p50_lower_than_p95_when_latency_spread():
    data = build_dashboard_data(
        [
            _record(version="pv1", latency=100),
            _record(version="pv1", latency=110),
            _record(version="pv1", latency=120),
            _record(version="pv1", latency=2000),
        ]
    )
    row = data.groups[0]
    assert row.p50_latency_ms is not None and row.p95_latency_ms is not None
    assert row.p50_latency_ms < row.p95_latency_ms


def test_arrow_direction_matches_compare_semantics():
    """Recall 涨 = 进步（绿 ↑），P95 延迟涨 = 退步（红 ↓）。"""
    data = build_dashboard_data(
        [
            _record(version="pv1", recall=0.5, latency=500, created_at=_BASE),
            _record(version="pv2", recall=0.9, latency=800, created_at=_BASE.replace(minute=1)),
        ]
    )
    recall_card = next(c for c in data.cards if c.label == "平均 Recall@5")
    p95_card = next(c for c in data.cards if c.label == "P95 延迟")
    assert recall_card.delta == "↑" and recall_card.is_good
    assert p95_card.delta == "↓" and p95_card.is_bad


def test_first_group_has_no_delta():
    data = build_dashboard_data([_record(version="pv1")])
    recall_card = next(c for c in data.cards if c.label == "平均 Recall@5")
    assert recall_card.delta == ""
    assert recall_card.tone == "neutral"


def test_faithfulness_none_renders_as_dash_not_zero():
    """未评 ≠ 评为 0：'-' 是留白，0.000 会把"没开 judge"记成"全是幻觉"。"""
    data = build_dashboard_data([_record(version="pv1", faithfulness=None)])
    card = next(c for c in data.cards if c.label == "平均忠实度")
    assert card.value == "-"


# ---------------------------------------------------------------- 渲染
def test_html_contains_all_required_sections():
    html_text = render_dashboard_html(build_dashboard_data(_two_groups()))
    assert "<!doctype html>" in html_text
    assert "总用例数" in html_text
    assert "平均 Recall@5" in html_text
    assert "平均引用率" in html_text
    assert "版本归因对比" in html_text
    assert "延迟分布（P50 / P95）" in html_text
    assert 'id="chart-trend"' in html_text
    assert 'id="chart-latency"' in html_text
    # CDN（Pico.css + ECharts）而不是本地资源：单文件可分发
    assert "picocss/pico" in html_text
    assert "echarts" in html_text


def test_embedded_json_is_parseable_and_carries_nulls():
    html_text = render_dashboard_html(build_dashboard_data(_two_groups()))
    match = re.search(r"window\.DASHBOARD_DATA = (.*?);\n", html_text, re.S)
    assert match is not None, "看板数据没有内联进 <script>"
    payload = json.loads(match.group(1))
    assert len(payload["labels"]) == 2
    assert len(payload["latency"]["p50"]) == 2
    assert len(payload["latency"]["p95"]) == 2
    # judge 未开 → null（ECharts 断线画缺口），不能是 0
    faithful = next(s for s in payload["trend"] if s["name"] == "忠实度")
    assert faithful["data"] == [None, None]


def test_group_label_pairs_version_and_model():
    data = build_dashboard_data([_record(version="pv9", model="bge-m3@v2")])
    assert data.group_label(data.groups[0]) == "pv9 / bge-m3@v2"


def test_generated_at_is_injected():
    data = build_dashboard_data([_record()], generated_at="2026-10-23 02:00:00 UTC")
    assert "2026-10-23 02:00:00 UTC" in render_dashboard_html(data)


# ---------------------------------------------------------------- 逃逸
def test_untrusted_group_name_is_escaped():
    """分组名来自数据库（等于不可信输入）：`<script>` 不许冲出 HTML 与 `<script>` 块。"""
    evil = "</script><script>alert(1)</script>"
    data = build_dashboard_data([_record(version=evil)])
    html_text = render_dashboard_html(data)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;/script&gt;" in html_text
    # 脚本块里的 JSON 同样要挡住
    match = re.search(r"window\.DASHBOARD_DATA = (.*?);\n", html_text, re.S)
    assert match is not None
    payload = json.loads(match.group(1))
    assert evil in payload["labels"][0]
    assert "</script>" not in match.group(1)


def test_latency_table_shows_ratio():
    html_text = render_dashboard_html(
        build_dashboard_data(
            [
                _record(version="pv1", latency=100),
                _record(version="pv1", latency=400),
            ]
        )
    )
    assert "P95 / P50" in html_text
    assert "4.00×" in html_text
