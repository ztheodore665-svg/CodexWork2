"""缓存命中率报表（`make cache-stats`）—— 阶段 3 W3-3 的出口条件工具（R3-3.6）。

口径严格按 §10.1 原文：**只统计 POI / 路线类工具**（`search_poi` / `calculate_route`），
`命中率 = count(cache_hit=true) / count(*)`，阈值 ≥ 0.6。

为什么分母要把其他工具排除（Q6）：把高频低价值调用（比如会话状态写入）算进分母，
会让一条本来 0.5 的 POI 命中率被"平均值"粉饰成 0.8 —— 指标一旦能被刷，就等于没有指标。

两条数据来源：
  1. **trace JSONL**（默认路径 `settings.resolved_trace_dir`）：排线上真实命中率。
     取它而不是查库，是因为托底要求"离线可跑"（E1）。
  2. `--synthetic`：合成一条"10 次查询里 8 次重复"的序列，用 `InMemoryCache` + `CacheClient`
     语义直接算出命中率 —— 不用真跑工具也能验证统计口径本身对不对。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.cache import InMemoryCache, api_cache_key, build_cache
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 参与命中率统计的工具（§10.1 "POI / 路线类工具"）
STAT_TOOLS: tuple[str, ...] = ("search_poi", "calculate_route")

#: §10.1 的命中率阈值
TARGET_HIT_RATE: float = 0.60


@dataclass(frozen=True)
class ToolCallRecord:
    """一条 `type=tool_call` 的 trace 记录中我们关心的字段。"""

    tool_name: str
    cache_hit: bool = False
    latency_ms: int = 0

    @classmethod
    def from_line(cls, payload: dict) -> ToolCallRecord | None:
        """从一行 JSONL 还原；不是工具调用的行返回 None（trace 里还混着 step / run_end）。"""
        if payload.get("type") != "tool_call":
            return None
        return cls(
            tool_name=str(payload.get("tool_name", "")),
            cache_hit=bool(payload.get("cache_hit", False)),
            latency_ms=int(payload.get("latency_ms", 0)),
        )


@dataclass(frozen=True)
class ToolStats:
    """单个工具的命中统计。"""

    tool_name: str
    calls: int = 0
    hits: int = 0

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.calls, 4) if self.calls else 0.0


@dataclass(frozen=True)
class CacheReport:
    """一次统计的完整结果（纯数据，便于单测直接断言）。"""

    per_tool: tuple[ToolStats, ...] = ()
    calls: int = 0
    hits: int = 0

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.calls, 4) if self.calls else 0.0

    @property
    def meets_target(self) -> bool:
        return bool(self.calls) and self.hit_rate >= TARGET_HIT_RATE


def load_tool_calls(paths: Iterable[Path]) -> list[ToolCallRecord]:
    """扫描若干 JSONL 文件，抽出全部工具调用记录。

    坏行（半截写入 / 手工编辑过）**跳过而不是抛错**：trace 是旁路设施，
    一张报表不该因为一行坏 JSON 就跑不出来。
    """
    records: list[ToolCallRecord] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取 trace 失败，跳过 %s: %s", path, exc)
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            record = ToolCallRecord.from_line(payload)
            if record is not None and record.tool_name in STAT_TOOLS:
                records.append(record)
    return records


def build_report(records: Sequence[ToolCallRecord]) -> CacheReport:
    """把记录聚合成报表。纯函数 —— 没有 I/O，单测喂什么就吐什么。

    过滤在这里**再做一次**（loader 已经滤过一遍）：口径必须只有一处入口被担保，
    否则谁直接喂一批 Raw 记录进来，分母就会被别的工具冲淡 —— 那正是 Q6 要防的事。
    """
    buckets: dict[str, list[int]] = {name: [0, 0] for name in STAT_TOOLS}
    for record in records:
        if record.tool_name not in STAT_TOOLS:
            continue
        bucket = buckets.setdefault(record.tool_name, [0, 0])
        bucket[0] += 1
        bucket[1] += int(record.cache_hit)
    per_tool = tuple(
        ToolStats(tool_name=name, calls=calls, hits=hits)
        for name, (calls, hits) in buckets.items()
        if calls
    )
    return CacheReport(
        per_tool=per_tool,
        calls=sum(stat.calls for stat in per_tool),
        hits=sum(stat.hits for stat in per_tool),
    )


async def synthetic_report(*, repeats: int = 8, uniques: int = 2) -> CacheReport:
    """合成序列：验证**统计口径**本身（不需要真实工具调用）。

    `repeats` 次重复提问 + `uniques` 次新提问，全部走真实 `CacheClient` 语义：
    第一次 miss 之后写缓存，第二次 get 命中。用它来确认
    「查 → miss → 调 → 写 → 再查 → hit」这条链路确实能产生 ≥0.6 的命中率。
    """
    cache = InMemoryCache(max_entries=100)
    records: list[ToolCallRecord] = []
    for tool in STAT_TOOLS:
        for i in range(repeats):
            key = api_cache_key(tool, {"q": f"same-{i % 2}", "tool": tool})
            hit = await cache.get(key)
            records.append(ToolCallRecord(tool_name=tool, cache_hit=hit is not None))
            if hit is None:
                await cache.set(key, "cached")
        for i in range(uniques):
            key = api_cache_key(tool, {"q": f"unique-{tool}-{i}", "tool": tool})
            await cache.delete(key)  # 保证一定是 miss，模拟首次提问
            records.append(ToolCallRecord(tool_name=tool, cache_hit=False))
    return build_report(records)


def trace_paths(trace_dir: Path) -> list[Path]:
    return sorted(trace_dir.glob("*.jsonl"))


def print_report(report: CacheReport, *, source: str) -> None:
    print("\n=== 缓存命中率（make cache-stats）===")
    print(f"数据源：{source}")
    print(f"统计范围：{', '.join(STAT_TOOLS)}（§10.1口径，其他工具不计入分母）")
    print("-" * 62)
    if not report.calls:
        print(f"✗ 未找到 {', '.join(STAT_TOOLS)} 的工具调用记录。")
        print("  该能力依赖 `search_poi` / `calculate_route` 真正被调用并写 ToolCallLog，")
        print("  属于阶段 3 W3-2（工具本体）+ W3-3（缓存接入），本阶段后续批次实现。")
        print("  当前可先验证统计口径：python -m app.scripts.cache_stats --synthetic")
        print("-" * 62)
        return
    header = f"{'工具':<20}{'调用数':>8}{'命中数':>8}{'命中率':>9}"
    print(header)
    for stat in report.per_tool:
        print(f"{stat.tool_name:<20}{stat.calls:>8}{stat.hits:>8}{stat.hit_rate:>9.4f}")
    print("-" * 62)
    print(f"{'合计':<20}{report.calls:>8}{report.hits:>8}{report.hit_rate:>9.4f}")
    flag = "✓ 达标" if report.meets_target else "✗ 未达标"
    print(f"{flag}：阈值 {TARGET_HIT_RATE:.2f}（§10.1 缓存命中率 ≥ 60%）")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TravelMemory 缓存命中率报表")
    parser.add_argument(
        "--dir", type=str, default=None, help="trace 目录（默认取 settings.resolved_trace_dir）"
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="不读 trace，用合成序列验证统计口径（离线可用）",
    )
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace, settings: Settings) -> int:
    # 用与运行时同一套 CacheClient，避免"看的报表 ≠ 跑的实现"
    cache = build_cache(settings)
    logger.debug("cache-stats 使用缓存实现: %s", type(cache).__name__)

    if args.synthetic:
        report = await synthetic_report()
        print_report(report, source="合成序列（8 次重复 + 2 次首次查询 / 工具）")
        return 0 if report.meets_target else 1

    trace_dir = Path(args.dir) if args.dir else settings.resolved_trace_dir
    report = build_report(load_tool_calls(trace_paths(trace_dir)))
    print_report(report, source=str(trace_dir))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args(), get_settings())))


if __name__ == "__main__":
    main()
