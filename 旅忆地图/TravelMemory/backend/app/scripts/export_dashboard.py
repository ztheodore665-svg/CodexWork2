"""可观测看板导出（`make dashboard`）—— 阶段 5 W5-3 的出口条件工具。

把 `eval_result` 表里的历史结果按 `prompt_version + embedding_model` 分组，
渲染成一个**单文件静态 HTML**：汇总卡片 + 归因对比表（↑↓）+ 延迟 P50/P95 + ECharts 趋势图。

用法：

```bash
cd backend
python -m app.scripts.export_dashboard                    # 读库，写到 data/dashboard.html
python -m app.scripts.export_dashboard --out ../docs/x.html
python -m app.scripts.export_dashboard --demo             # 不连库，合成两组样例数据
```

## 为什么单文件 + CDN

看板要给"明早开会的人"打开，不该要求他先起一个服务。所以聚合结果直接内联成
`window.DASHBOARD_DATA`，只把 Pico.css 与 ECharts 交给 CDN；
断网时页面仍然可读（表格与卡片是纯 HTML，图表块留空），不会白屏。

## 为什么要有 `--demo`

`make dashboard` 的出口条件是"看板能出"，而本机（以及 CI）没有 PostgreSQL。
没有 `--demo` 就等于这条出口条件在没起 Docker 的机器上**永远判定不了** ——
和 `--ablation --offline` 是同一个道理（阶段 4 W4-3）。
`--demo` 只喂合成的 `ResultRecord`，走的是与真实路径完全相同的聚合与渲染函数，
因此它验证的是"看板对不对"，而不是"演示数据好不好看"。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.agent.eval.compare import ResultRecord
from app.agent.eval.dashboard import build_dashboard_data, render_dashboard_html
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionFactory, dispose_engine, ping_database
from app.models.eval import EvalResult

logger = get_logger(__name__)

#: 默认输出位置（backend/data/dashboard.html）
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "dashboard.html"

_DB_HINT = "数据库不可达。请依次执行：make up → make migrate → make seed-demo → make eval"


async def load_records() -> list[ResultRecord]:
    """把 `eval_result` 表搬成纯数据（`compare` 层不认识 ORM）。

    聚合放在 Python 而不是 SQL，理由见 `eval/compare.py`：
    口径要能在没有数据库的机器上被单测钉死。
    """
    async with SessionFactory() as session:
        result = await session.execute(select(EvalResult).order_by(EvalResult.created_at))
        return [
            ResultRecord(
                prompt_version=r.prompt_version or "",
                embedding_model=r.embedding_model or "",
                recall_at_k=r.recall_at_k,
                mrr=r.mrr,
                citation_rate=r.citation_rate,
                refusal_correct=r.refusal_correct,
                faithfulness=r.faithfulness,
                latency_ms=r.latency_ms,
                created_at=r.created_at,
            )
            for r in result.scalars().all()
        ]


def demo_records() -> list[ResultRecord]:
    """合成两组样例结果（`--demo` 用）：刻意做成"一组进步、一组退步"，
    这样箭头、P50/P95、缺口（旧组无忠实度）都能被肉眼验证。
    """
    base = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

    def group(
        version: str, offset: int, recall: float, latency: int, faithful: float | None, n: int
    ):
        return [
            ResultRecord(
                prompt_version=version,
                embedding_model="bge-m3@v1",
                recall_at_k=recall,
                mrr=recall - 0.05,
                citation_rate=1.0 if version.endswith("5.3") else 0.94,
                refusal_correct=1.0,
                faithfulness=faithful,
                latency_ms=latency + (i * 17 % 260),  # 组内打个散，让 P50 ≠ P95
                created_at=base + timedelta(minutes=offset + i),
            )
            for i in range(n)
        ]

    return group("pv2026.10-stage5.2", 0, 0.857, 420, None, 29) + group(
        "pv2026.10-stage5.3", 60, 0.893, 380, 1.0, 29
    )


#: `--demo` 的标题。**必须在页面上就写明这是合成数据**：
#: 看板是用来做决策的（"这版 prompt 到底好不好"），一张看起来像真的、
#: 数字却是编的看板，比没有看板更危险。
DEMO_TITLE = "TravelMemory 评测看板（DEMO · 合成样例数据，非真实指标）"
REAL_TITLE = "TravelMemory 评测看板"


def export(records: list[ResultRecord], out: Path, *, title: str = REAL_TITLE) -> Path:
    """聚合 + 渲染 + 写盘，返回实际写入的路径。"""
    html_text = render_dashboard_html(build_dashboard_data(records), title=title)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出 TravelMemory 评测可观测看板（静态 HTML）")
    parser.add_argument("--out", type=str, default=None, help=f"输出路径（默认 {DEFAULT_OUTPUT}）")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="不连数据库，用合成样例数据渲染（验证看板本身，不验证真实指标）",
    )
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    out = Path(args.out) if args.out else DEFAULT_OUTPUT

    records: list[ResultRecord]
    if args.demo:
        records = demo_records()
        logger.warning("--demo：使用合成样例数据，看板里的数字不是真实评测结果")
    else:
        if not await ping_database():
            print(f"✗ {_DB_HINT}")
            print(
                "  （无数据库时可用 `python -m app.scripts.export_dashboard --demo` 验证看板本身）"
            )
            return 1
        records = await load_records()
        await dispose_engine()

    if not records:
        logger.warning("eval_result 表为空：将生成一张空态看板（先跑 make eval 才有数据）")

    path = export(records, out, title=DEMO_TITLE if args.demo else REAL_TITLE)
    print(f"✓ 看板已生成：{path}")
    print(
        f"  分组数 {len({(r.prompt_version, r.embedding_model) for r in records})}，结果条数 {len(records)}"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
