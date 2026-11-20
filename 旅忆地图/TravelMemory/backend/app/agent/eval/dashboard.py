"""阶段 5 W5-3 可观测看板（**纯函数层**：不 import SQLAlchemy、不碰文件系统）。

和 `compare.py` 一样，聚合与渲染全部做成纯函数，理由完全相同（见 `compare.py`
模块 docstring）：**看板口径必须能在没有 Docker 的机器上被单测钉死**。
读 `EvalResult` 表、写 HTML 文件的脏活留在 `app/scripts/export_dashboard.py`。

## 顶部卡片为什么取「最新一组」而不是「全部历史平均」

跨版本求平均是错的：`pv2026.10-stage5.2` 与 `pv2026.10-stage5.3` 是两套不同的
行为，把它们混在一起算出来的 0.9 既不代表旧版也不代表新版，
却会被当成"系统当前的水平"。所以卡片取**最新分组**的聚合值，
并在副标题写明这个数属于哪个版本 —— 卡片回答"现在怎么样"，
归因表回答"比上次好还是坏"，两件事分开。

## 为什么 P50 与 P95 必须同时出现

- 只看 P95：一次 3 秒的冷启动会把整组"看起来很慢"，于是去优化一个一次性的抖动；
- 只看 P50：尾延迟从 200ms 涨到 2s 完全看不见，而那才是用户实际抱怨的东西。

两者都沿用 `compare.percentile` 的**最近秩**口径（不插值），
所以看板上的数字与 `--compare` 终端表里的 P95 是同一个数 ——
两个出口不允许给出两个答案。
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from string import Template

from app.agent.eval.compare import CompareRow, ResultRecord, build_compare_rows

#: 卡片元数据：(CompareRow 字段名, 卡片标题, 小数位, 副标题)
#: 表驱动而不是一串 if：加一个指标只改这一行，卡片、图表 series、图例跟着走。
_CARD_METRICS: tuple[tuple[str, str, int, str], ...] = (
    ("recall_at_k", "平均 Recall@5", 3, "检索命中率，越高越好"),
    ("mrr", "平均 MRR", 3, "首条命中排名的倒数"),
    ("citation_rate", "平均引用率", 3, "红线目标 1.000（100% 引用）"),
    ("refusal_correct", "拒答正确率", 3, "该拒的是否拒对了"),
    ("faithfulness", "平均忠实度", 3, "judge 默认关闭，'-' 表示未评"),
    ("p50_latency_ms", "P50 延迟", 0, "毫秒，最近秩（典型一次请求）"),
    ("p95_latency_ms", "P95 延迟", 0, "毫秒，最近秩（最慢的 5%）"),
)

#: 折线图里的质量类指标（0~1 之间，共用一根 y 轴）
_TREND_METRICS: tuple[tuple[str, str, str], ...] = (
    ("recall_at_k", "Recall@5", "#2563eb"),
    ("mrr", "MRR", "#0891b2"),
    ("citation_rate", "引用率", "#16a34a"),
    ("refusal_correct", "拒答正确率", "#d97706"),
    ("faithfulness", "忠实度", "#7c3aed"),
)

_EMPTY_TITLE = "暂无历史评测结果"
_EMPTY_MESSAGE = "先跑一次评测：<code>make eval</code>，再来 <code>make dashboard</code>"

_ARROW_UP = "↑"
_ARROW_DOWN = "↓"


@dataclass(frozen=True)
class MetricCard:
    """顶部一张汇总卡片。

    `delta` 是**已经判过方向**的箭头（↑ 恒为进步、↓ 恒为退步，方向由
    `compare._arrows` 按指标元数据决定），因此 `tone` 只由箭头决定：
    ↑ → good、↓ → bad、无箭头 → neutral。卡片上不再重复判断方向，
    免得出现"表格说退步、卡片说进步"的两套口径。
    """

    label: str
    value: str
    hint: str
    delta: str = ""
    tone: str = "neutral"

    @property
    def is_good(self) -> bool:
        return self.tone == "good"

    @property
    def is_bad(self) -> bool:
        return self.tone == "bad"


@dataclass(frozen=True)
class DashboardData:
    """看板需要的全部数据（渲染成 HTML 之前的最后一层）。"""

    generated_at: str
    #: 历史 EvalResult 总行数（不是题目数：同一批题跑多次会累计）
    total_runs: int
    groups: list[CompareRow]
    cards: list[MetricCard] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.groups

    @property
    def latest(self) -> CompareRow | None:
        """最新一组（排序由 `build_compare_rows` 保证：按首现时间）。"""
        return self.groups[-1] if self.groups else None

    def group_label(self, row: CompareRow) -> str:
        return f"{row.prompt_version} / {row.embedding_model}"


def _fmt(value: float | None, *, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _build_cards(data_groups: list[CompareRow], total_runs: int) -> list[MetricCard]:
    latest = data_groups[-1] if data_groups else None
    previous = data_groups[-2] if len(data_groups) >= 2 else None

    cards: list[MetricCard] = []

    # ① 总用例数：不是"全部历史条数"——那会把"跑了几轮"混进"有多少题"。
    #    卡片显示最新一组的 n（= 一次评测的题目数），历史条数降级为副标题。
    if latest is None:
        cards.append(MetricCard("总用例数", "-", "尚无评测记录"))
    else:
        hint = f"最新分组 {latest.prompt_version} / {latest.embedding_model}"
        delta = ""
        if previous is not None and latest.n != previous.n:
            delta = f"{latest.n - previous.n:+d}"
        cards.append(
            MetricCard(
                "总用例数", str(latest.n), f"{hint}；历史累计 {total_runs} 条运行记录", delta
            )
        )

    # ② 质量与延迟指标卡片
    for name, label, digits, hint in _CARD_METRICS:
        if latest is None:
            cards.append(MetricCard(label, "-", hint))
            continue
        arrow = latest.deltas.get(name, "")
        cards.append(
            MetricCard(
                label=label,
                value=_fmt(getattr(latest, name), digits=digits),
                hint=hint,
                delta=arrow,
                tone="good"
                if arrow == _ARROW_UP
                else ("bad" if arrow == _ARROW_DOWN else "neutral"),
            )
        )
    return cards


def build_dashboard_data(
    records: list[ResultRecord],
    *,
    generated_at: str | None = None,
) -> DashboardData:
    """把扁平的历史结果聚合成看板数据。

    排序、均值、P95、环比箭头全部复用 `build_compare_rows` ——
    看板与终端表格不允许出现两套聚合口径（两套口径迟早给出两个答案）。
    """
    groups = build_compare_rows(records)
    stamp = generated_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return DashboardData(
        generated_at=stamp,
        total_runs=len(records),
        groups=groups,
        cards=_build_cards(groups, len(records)),
    )


# ---------------------------------------------------------------- 渲染
def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _arrow_span(arrow: str) -> str:
    """箭头渲染：↑ 绿、↓ 红（方向已在 compare 层判完，这里只负责上色）。"""
    if arrow == _ARROW_UP:
        return '<span class="delta up">↑</span>'
    if arrow == _ARROW_DOWN:
        return '<span class="delta down">↓</span>'
    return ""


def _render_cards(cards: list[MetricCard]) -> str:
    parts: list[str] = []
    for card in cards:
        delta = (
            f'<span class="delta {"up" if card.is_good else "down"}">{_esc(card.delta)}</span>'
            if card.is_good or card.is_bad
            else (f'<span class="delta flat">{_esc(card.delta)}</span>' if card.delta else "")
        )
        parts.append(
            "<article class='card'>"
            f"<h3>{_esc(card.label)}{delta}</h3>"
            f"<p class='value'>{_esc(card.value)}</p>"
            f"<p class='hint'>{_esc(card.hint)}</p>"
            "</article>"
        )
    return "\n      ".join(parts)


def _render_compare_table(data: DashboardData) -> str:
    headers = (
        "prompt_version",
        "embedding_model",
        "n",
        "Recall@5",
        "MRR",
        "引用率",
        "拒答正确率",
        "忠实度",
    )
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body: list[str] = []
    for row in data.groups:
        cells = [
            _esc(row.prompt_version),
            _esc(row.embedding_model),
            str(row.n),
        ]
        for name in ("recall_at_k", "mrr", "citation_rate", "refusal_correct", "faithfulness"):
            cells.append(_fmt(getattr(row, name), digits=3) + _arrow_span(row.deltas.get(name, "")))
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<table class="striped">\n'
        f"        <thead><tr>{head}</tr></thead>\n"
        "        <tbody>\n          "
        + "\n          ".join(body)
        + "\n        </tbody>\n      </table>"
    )


def _render_latency_table(data: DashboardData) -> str:
    headers = ("分组", "n", "P50 延迟 (ms)", "P95 延迟 (ms)", "P95 / P50")
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body: list[str] = []
    for row in data.groups:
        p50, p95 = row.p50_latency_ms, row.p95_latency_ms
        ratio = "-"
        if p50 and p95 is not None and p50 > 0:
            ratio = f"{p95 / p50:.2f}×"
        cells = [
            _esc(data.group_label(row)),
            str(row.n),
            _fmt(p50, digits=0),
            _fmt(p95, digits=0) + _arrow_span(row.deltas.get("p95_latency_ms", "")),
            ratio,
        ]
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<table class="striped">\n'
        f"        <thead><tr>{head}</tr></thead>\n"
        "        <tbody>\n          "
        + "\n          ".join(body)
        + "\n        </tbody>\n      </table>"
    )


def _chart_payload(data: DashboardData) -> str:
    """给 ECharts 用的轻量 JSON（嵌在 <script> 里，不额外发请求）。

    `None` 在 JSON 里是 `null`，ECharts 会自动断线 —— 这正是我们想要的：
    「上一轮没开 judge，忠实度不可比」应当显示成**缺口**，而不是插值成一条假的平滑曲线。
    """
    payload = {
        "labels": [data.group_label(row) for row in data.groups],
        "trend": [
            {
                "name": label,
                "color": color,
                "data": [getattr(row, name) for row in data.groups],
            }
            for name, label, color in _TREND_METRICS
        ],
        "latency": {
            "p50": [row.p50_latency_ms for row in data.groups],
            "p95": [row.p95_latency_ms for row in data.groups],
        },
    }
    raw = json.dumps(payload, ensure_ascii=False)
    # `<` / `&` 必须转义：否则分组名里若含 `</script>` 会直接截断脚本块
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


_TEMPLATE = Template(
    """<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$TITLE</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  :root { --tm-border: #e5e7eb; --tm-muted: #6b7280; --tm-bg: #f9fafb; }
  body { background: var(--tm-bg); color: #111827; }
  main { max-width: 1180px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
  h1 { font-size: 1.6rem; margin-bottom: .25rem; }
  .subtitle { color: var(--tm-muted); font-size: .9rem; margin-bottom: 1.75rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem; }
  article.card {
    background: #fff; border: 1px solid var(--tm-border); border-radius: 12px;
    padding: 1rem 1.1rem; box-shadow: 0 1px 2px rgba(16,24,40,.04);
  }
  article.card h3 { font-size: .85rem; font-weight: 600; color: var(--tm-muted);
    margin: 0 0 .35rem; letter-spacing: .02em; }
  article.card .value { font-size: 1.85rem; font-weight: 650; margin: 0; line-height: 1.15; }
  article.card .hint { font-size: .72rem; color: var(--tm-muted); margin: .4rem 0 0; }
  .delta { font-weight: 700; margin-left: .3rem; }
  .delta.up { color: #16a34a; }
  .delta.down { color: #dc2626; }
  .delta.flat { color: var(--tm-muted); }
  section { background: #fff; border: 1px solid var(--tm-border); border-radius: 12px;
    padding: 1.25rem 1.4rem 1.5rem; margin-top: 1.5rem; }
  section h2 { font-size: 1.05rem; margin-top: 0; }
  table { font-size: .85rem; }
  th, td { white-space: nowrap; }
  .chart { width: 100%; height: 340px; }
  .empty { text-align: center; padding: 3rem 1rem; color: var(--tm-muted); }
  footer { color: var(--tm-muted); font-size: .78rem; margin-top: 2rem; text-align: center; }
  code { background: #f3f4f6; padding: .1rem .35rem; border-radius: 4px; }
</style>
</head>
<body>
<main>
  <h1>$TITLE</h1>
  <p class="subtitle">生成时间 $GENERATED_AT ｜ 数据来源 <code>eval_result</code> 表 ｜ 分组键 <code>prompt_version + embedding_model</code></p>
$CARDS
$BODY
  <footer>TravelMemory 阶段 5 可观测看板 ｜ 重新生成：<code>make dashboard</code></footer>
</main>
<script>
  window.DASHBOARD_DATA = $DATA_JSON;
  (function () {
    var d = window.DASHBOARD_DATA;
    if (!d || typeof echarts === 'undefined') { return; }
    var labels = d.labels;
    var trend = new echarts.init(document.getElementById('chart-trend'));
    trend.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: d.trend.map(function (s) { return s.name; }), bottom: 0 },
      grid: { left: 48, right: 24, top: 32, bottom: 56 },
      xAxis: { type: 'category', data: labels, axisLabel: { interval: 0, rotate: 18 } },
      yAxis: { type: 'value', min: 0, max: 1 },
      series: d.trend.map(function (s) {
        return { name: s.name, type: 'line', data: s.data, connectNulls: false,
                 smooth: false, symbolSize: 7, lineStyle: { width: 2 }, itemStyle: { color: s.color } };
      })
    });
    var latency = new echarts.init(document.getElementById('chart-latency'));
    latency.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['P50', 'P95'], bottom: 0 },
      grid: { left: 56, right: 24, top: 32, bottom: 56 },
      xAxis: { type: 'category', data: labels, axisLabel: { interval: 0, rotate: 18 } },
      yAxis: { type: 'value', name: 'ms' },
      series: [
        { name: 'P50', type: 'bar', data: d.latency.p50, itemStyle: { color: '#93c5fd' } },
        { name: 'P95', type: 'bar', data: d.latency.p95, itemStyle: { color: '#1d4ed8' } }
      ]
    });
    window.addEventListener('resize', function () { trend.resize(); latency.resize(); });
  })();
</script>
</body>
</html>
"""
)

_BODY_TEMPLATE = Template(
    """  <section>
    <h2>版本归因对比</h2>
    <p class="subtitle">↑ 进步 / ↓ 退步（方向按指标语义判定：延迟越小越好）；两侧有 None 则不给箭头 —— 不可比就不比。</p>
    $COMPARE_TABLE
  </section>
  <section>
    <h2>质量指标趋势</h2>
    <div id="chart-trend" class="chart"></div>
  </section>
  <section>
    <h2>延迟分布（P50 / P95）</h2>
    <p class="subtitle">最近秩分位数，与 <code>make eval-compare</code> 终端表的 P95 同口径。P95/P50 越大说明尾延迟越拖。</p>
    <div id="chart-latency" class="chart"></div>
    $LATENCY_TABLE
  </section>"""
)


def render_dashboard_html(data: DashboardData, *, title: str = "TravelMemory 评测看板") -> str:
    """渲染单文件静态 HTML（Pico.css + ECharts 走 CDN，数据内联，无需后端）。"""
    if data.is_empty:
        body = f'  <section class="empty"><h2>{_esc(_EMPTY_TITLE)}</h2><p>{_EMPTY_MESSAGE}</p></section>'
        cards = ""
        payload = _chart_payload(data)
    else:
        body = _BODY_TEMPLATE.substitute(
            COMPARE_TABLE=_render_compare_table(data),
            LATENCY_TABLE=_render_latency_table(data),
        )
        cards = f'  <div class="grid">\n      {_render_cards(data.cards)}\n    </div>'
        payload = _chart_payload(data)

    return _TEMPLATE.substitute(
        TITLE=_esc(title),
        GENERATED_AT=_esc(data.generated_at),
        CARDS=cards,
        BODY=body,
        DATA_JSON=payload,
    )
