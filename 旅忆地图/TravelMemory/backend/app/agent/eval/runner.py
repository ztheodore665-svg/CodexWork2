"""评测执行器（`make eval` / `make eval-compare` 的入口）。

用法：
```bash
python -m app.agent.eval.runner            # 只测检索：Recall@5 / MRR（不烧生成 token）
python -m app.agent.eval.runner --full     # 含生成：额外算引用率与拒答表现
python -m app.agent.eval.runner --compare  # 读历史 EvalResult，按 prompt_version + embedding_model 分组输出 Markdown 归因对比表（含环比 ↑/↓）
python -m app.agent.eval.runner --limit 5  # 只跑前 N 条（省钱）
python -m app.agent.eval.runner --ablation --limit 3   # ★ 最小成本验证：3 条 × 4 档位
```

`--ablation --limit 3` 是**接入新模型后的第一步**：它只发十几个 LLM 请求
（解析 + embedding），就能确认链路是否通畅；确认后再放开全量 26 条。
小于等于 5 条时会自动逐条打印，便于确认 `--limit` 真的生效、以及哪一条在拖慢。

归因的前提：每条结果都记录 `prompt_version` + `embedding_model`，
所以改 prompt 之后能在同一张表里看到指标到底变好还是变坏。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.eval.compare import (
    ResultRecord,
    build_compare_rows,
    render_compare_markdown,
)
from app.agent.eval.dataset import (
    EvalCaseItem,
    TimeCaseItem,
    ToolCaseItem,
    load_dataset,
    load_time_cases,
    load_tool_cases,
)
from app.agent.eval.metrics import (
    EvalRow,
    aggregate,
    citation_rate_for,
    mrr,
    parse_faithfulness,
    recall_at_k,
    refusal_correct,
)
from app.agent.nodes.pipeline.query_parse import QueryParseNode
from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.prompts.loader import PROMPT_VERSION, render
from app.agent.rag.retriever import Retriever, RetrieverConfig
from app.agent.rag.time_parser import parse_time_range
from app.agent.state import PipelineState
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.llm import LLMMessage, build_llm
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionFactory, dispose_engine, ping_database
from app.models.eval import EvalCase, EvalResult
from app.schemas.document import DocumentRef, SearchHit
from app.services.user_service import get_or_create_demo_user
from app.utils.intent import UNSCORED_INTENTS

logger = get_logger(__name__)


# ---------------------------------------------------------------- W2-1 解析准确率
def run_time_parse_eval(cases: list[TimeCaseItem]) -> float:
    """中文时间表达解析准确率（`--time`）。

    单独评测解析的设计文档依据（§7.3）："独立节点 query_parse.py，单独评测解析准确率"。
    把它和检索指标分开，是因为**解析错了会连累检索，但检索指标看不出是解析的锅** ——
    分口径才能定位问题。
    """
    correct = 0
    print("\n=== 中文时间表达解析准确率 ===")
    print(f"{'命中':<6}{'期望':<26}{'实际':<26}问题")
    print("-" * 96)
    for case in cases:
        got = parse_time_range(case.question, case.today)
        if case.expects_range:
            ok = (
                got is not None
                and got.start == case.expected_start
                and got.end == case.expected_end
            )
            expected = f"{case.expected_start}~{case.expected_end}"
        else:
            ok = got is None
            expected = "不过滤"
        correct += int(ok)
        actual = f"{got.start}~{got.end}" if got else "不过滤"
        print(f"{'✓' if ok else '✗':<6}{expected:<26}{actual:<26}{case.question}")
    accuracy = correct / len(cases) if cases else 0.0
    print("-" * 96)
    print(f"解析准确率：{accuracy:.4f}（{correct}/{len(cases)}）")
    return accuracy


# ---------------------------------------------------------------- W3-1 工具选择准确率
TOOL_SELECT_TARGET = 0.90


def run_tool_selection_eval(
    items: list[ToolCaseItem], *, min_cases: int = 20, min_negatives: int = 5
) -> float:
    """工具选择准确率（W3-1 的出口条件：≥ 0.90）。

    **为什么只测"选得对不对"，不测"调得成不成功"**：
    这是两个失败模式。选错工具 = 语义理解问题（本阶段的重点）；
    调用失败 = 外部依赖问题（由降级逻辑兜底，不该让主流程失败）。
    混在一个数字里，指标下降时你无法判断该改 prompt 还是该改重试策略。

    **为什么在 mock LLM 下仍然有效**：`ToolCallNode` 在 `provider == "mock"` 时
    走 `rule_select`，规则是确定性计算，离线也能得到稳定数值 —— 这也是唯一能让
    "工具选择准确率"进入 CI 的方式。
    """
    from app.agent.nodes.pipeline.tool_call import rule_select

    negatives = [item for item in items if not item.expects_tool]
    if len(items) < min_cases:
        print(f"⚠ 用例数 {len(items)} < {min_cases}：样本太少，准确率不可信")
    if len(negatives) < min_negatives:
        print(f"⚠ 负例数 {len(negatives)} < {min_negatives}：负例不足会掩盖「该调就调」的过拟合")

    print("\n=== 工具选择准确率 ===")
    print(f"{'命中':<6}{'期望':<20}{'实际':<20}问题")
    print("-" * 96)
    correct = 0
    for item in items:
        got, reason = rule_select(item.question)
        ok = got == item.expected
        correct += int(ok)
        print(
            f"{'✓' if ok else '✗':<6}"
            f"{str(item.expected or '不调工具'):<20}"
            f"{str(got or '不调工具'):<20}{item.question}"
        )
    accuracy = correct / len(items) if items else 0.0
    print("-" * 96)
    print(f"工具选择准确率：{accuracy:.4f}（{correct}/{len(items)}）")
    print(
        f"目标：≥ {TOOL_SELECT_TARGET:.2f}　{'✓ 达标' if accuracy >= TOOL_SELECT_TARGET else '✗ 未达标'}"
    )
    return accuracy


# ---------------------------------------------------------------- 执行
async def run_retrieval_eval(
    retriever: Retriever,
    items: list[EvalCaseItem],
    *,
    user_id,
    k: int = 5,
    parse_node: QueryParseNode | None = None,
) -> list[EvalRow]:
    """只跑检索：快、便宜，适合每天改完代码先跑一遍。

    `parse_node` 不为空时会先跑一次结构化解析 —— 阶段 2 起**这才是默认该测的路径**：
    不跑解析就等于在评测"没有过滤的检索"，根本反映不了线上行为。
    """
    rows: list[EvalRow] = []
    for item in items:
        started = time.perf_counter()
        state = PipelineState(user_id=user_id, session_id="eval-retrieval", query=item.question)
        if parse_node is not None:
            # 评测用的轻量 trace：只记步骤内容，不落库
            await parse_node(
                state, Trace(session=None, user_id=user_id, session_id="eval", query=item.question)
            )
        hits = await retriever.retrieve(state)
        latency_ms = int((time.perf_counter() - started) * 1000)

        keys = [hit.ref.key for hit in hits]
        rows.append(
            EvalRow(
                kind=item.kind,
                question=item.question,
                recall_at_k=recall_at_k(keys, item.expected_keys, k),
                mrr=mrr(keys, item.expected_keys),
                latency_ms=latency_ms,
            )
        )
    return rows


async def judge_faithfulness(pipeline: AgentPipeline, state: PipelineState) -> float | None:
    """LLM-as-a-Judge：对已生成的答案多做一次调用，判断每句话是否有证据支撑。

    只在 `EVAL_LLM_JUDGE=true` 时启用（run_full_eval 里把关），
    且只评**未拒答**的答案——拒答话术没有事实句，评它没有意义。
    调用失败按 None 计并告警：judge 是附加指标，不该带崩整场评测。
    """
    evidence = "\n".join(f"[{b.index}] {b.content}" for b in state.blocks)
    if not evidence.strip() or not state.answer.strip():
        return None
    prompt = render("eval_faithfulness", evidence=evidence, answer=state.answer)
    try:
        result = await pipeline.llm.chat(
            [LLMMessage(role="user", content=prompt)],
            purpose="eval_judge",
            temperature=0.0,
            max_tokens=8,
        )
    except Exception as exc:  # noqa: BLE001 - judge 失败不该带崩评测
        logger.warning("忠实度评判失败（按未评计）: %s", exc)
        return None
    return parse_faithfulness(result.content)


async def run_full_eval(
    pipeline: AgentPipeline,
    items: list[EvalCaseItem],
    *,
    user_id,
    k: int = 5,
) -> list[EvalRow]:
    """跑完整管道：额外得到引用率与拒答表现（会消耗 LLM token）。"""
    rows: list[EvalRow] = []
    for item in items:
        state = await pipeline.run(user_id=user_id, session_id="eval-full", query=item.question)
        keys = [hit.ref.key for hit in state.hits]

        faithfulness = (
            None
            if state.refused
            or not pipeline.settings.eval_llm_judge
            # plan/recommend 与引用率同理：只要求"生成合理"，
            # 拿"每句话有没有证据支撑"去量建议句，只会得到一片虚假的 0 分
            or item.kind in UNSCORED_INTENTS
            else await judge_faithfulness(pipeline, state)
        )
        rows.append(
            EvalRow(
                kind=item.kind,
                question=item.question,
                recall_at_k=recall_at_k(keys, item.expected_keys, k),
                mrr=mrr(keys, item.expected_keys),
                citation_rate=citation_rate_for(item.kind, state.answer, refused=state.refused),
                refusal_correct=refusal_correct(item.kind, state.refused),
                latency_ms=state.latency_ms,
                answer=state.answer,
                refused=state.refused,
                refuse_reason=state.refuse_reason,
                faithfulness=faithfulness,
            )
        )
    return rows


# ---------------------------------------------------------------- 落库
async def upsert_eval_cases(
    session: AsyncSession, user_id, items: list[EvalCaseItem]
) -> dict[str, object]:
    """把评测集同步到 EvalCase 表（按 user_id + question 去重）。"""
    mapping: dict[str, object] = {}
    for item in items:
        existing = (
            await session.execute(
                select(EvalCase).where(
                    EvalCase.user_id == user_id, EvalCase.question == item.question
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = EvalCase(
                user_id=user_id,
                question=item.question,
                kind=item.kind,
                expected_evidence=[ref.model_dump() for ref in item.expected_evidence],
                expected_answer=item.expected_answer,
                note=item.note,
            )
            session.add(existing)
            await session.flush()
        mapping[item.question] = existing.id
    return mapping


async def save_results(
    session: AsyncSession,
    rows: list[EvalRow],
    items: list[EvalCaseItem],
    *,
    user_id,
    embedding_model: str,
) -> int:
    """写入 EvalResult。**不写**就没法做版本归因。"""
    mapping = await upsert_eval_cases(session, user_id, items)
    for row in rows:
        case_id = mapping.get(row.question)
        if case_id is None:
            continue
        session.add(
            EvalResult(
                eval_case_id=case_id,
                recall_at_k=row.recall_at_k,
                mrr=row.mrr,
                citation_rate=row.citation_rate,
                refusal_correct=row.refusal_correct,
                latency_ms=row.latency_ms,
                prompt_version=PROMPT_VERSION,
                embedding_model=embedding_model,
                faithfulness=row.faithfulness,
            )
        )
    await session.flush()
    return len(rows)


async def print_compare(session: AsyncSession) -> None:
    """按 prompt_version + embedding_model 聚合历史结果，输出归因对比表（Markdown）。

    聚合放在 Python 而不是 SQL：compare 的逻辑（分组、均值、P95、箭头）
    因此可以在没有 Docker 的机器上被单测钉死。若 eval_result 涨到万行以上
    再考虑把均值搬进 SQL。
    """
    result = await session.execute(select(EvalResult).order_by(EvalResult.created_at))
    records = [
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
    rows = build_compare_rows(records)
    print("\n=== 版本归因对比（↑ 进步 / ↓ 退步；P95 为最近秩）===")
    print(render_compare_markdown(rows, color=sys.stdout.isatty()))


def _fmt(value: float | None, *, digits: int = 3) -> str:
    """统一格式化：None 打印成 `-`，避免把"没数据"混进平均值的比较里。"""
    return "-" if value is None else f"{value:.{digits}f}"


# ---------------------------------------------------------------- 报告
def print_report(rows: list[EvalRow], *, k: int, mode: str) -> None:
    settings = get_settings()
    print(f"\n=== 评测报告（mode={mode}, k={k}）===")
    print(f"prompt_version={PROMPT_VERSION}  embedding_model={settings.embedding_model_version}")
    print("-" * 90)
    for row in rows:
        bits = [
            f"{row.kind:<9}",
            f"Recall@{k}={'-' if row.recall_at_k is None else f'{row.recall_at_k:.2f}'}",
            f"MRR={'-' if row.mrr is None else f'{row.mrr:.2f}'}",
        ]
        if row.citation_rate is not None:
            bits.append(f"引用率={row.citation_rate:.2f}")
        if row.refusal_correct is not None:
            bits.append(f"拒答正确={row.refusal_correct:.0f}")
        if row.faithfulness is not None:
            bits.append(f"忠实度={row.faithfulness:.2f}")
        if row.refused:
            bits.append(f"（拒答：{row.refuse_reason or 'unknown'}）")
        bits.append(f"{row.latency_ms}ms")
        print(" | ".join(bits) + f" | {row.question}")

    stats = aggregate(rows)
    print("-" * 90)
    print("按 kind 汇总：")
    for name, value in sorted(stats.items()):
        print(f"  {name:<28}{'-' if value is None else f'{value:.4f}'}")


# ---------------------------------------------------------------- 入口
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TravelMemory 评测执行器")
    parser.add_argument("--full", action="store_true", help="跑完整管道（含生成，消耗 token）")
    parser.add_argument(
        "--tools",
        action="store_true",
        help="阶段 3：工具选择准确率（离线可跑，不需要数据库）",
    )
    parser.add_argument("--compare", action="store_true", help="输出历史结果归因对比")
    parser.add_argument(
        "--limit", type=int, default=0, help="只跑前 N 条评测用例（省 token，先小样本验证链路）"
    )
    parser.add_argument("--top-k", type=int, default=5, help="Recall@k 的 k 值")
    parser.add_argument("--dataset", type=str, default=None, help="自定义评测集路径")
    parser.add_argument(
        "--time", action="store_true", help="只跑中文时间表达解析准确率（不需要数据库）"
    )
    parser.add_argument(
        "--no-parse", action="store_true", help="检索评测不跑结构化解析（= 阶段 0 的纯向量口径）"
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="消融实验：纯向量 / 仅过滤 / 混合 / RRF 四档对比（阶段 2 + 阶段 4 出口条件）",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不连数据库：把演示语料装进内存检索器跑（本机没起 Docker 时用）",
    )
    return parser.parse_args(argv)


async def run_ablation(
    items: list[EvalCaseItem],
    *,
    user_id,
    k: int,
    embedder,
    searcher,
    settings,
) -> None:
    """消融实验：把"加了什么"和"指标怎么变"放在同一张表里。

    阶段 2 的出口条件是"混合检索 Recall@5 不低于纯向量"，
    光看一次跑分说明不了问题 —— 必须同一批题、同一套权重做对比。

    阶段 4 W4-3 追加了第四档（RRF）：它的出口条件是
    **融合后 Recall@5 不低于阶段 3 基线**，所以必须和前三档在同一张表里。
    """
    from app.core.llm import build_llm

    llm = build_llm(settings)
    parse_node = QueryParseNode(llm)

    variants: tuple[tuple[str, AblationFlags], ...] = (
        ("纯向量（阶段 0）", AblationFlags(enable_filter=False, enable_lexical=False)),
        ("+结构化过滤", AblationFlags(enable_filter=True, enable_lexical=False)),
        ("+词面召回（混合）", AblationFlags(enable_filter=True, enable_lexical=True)),
        # ★ 阶段 3 基线就是上面这一档；下面这档只是把融合策略换成 RRF
        ("+RRF 融合（W4-3）", AblationFlags(enable_filter=True, enable_lexical=True, fusion="rrf")),
    )

    print(f"\n=== 消融实验（k={k}，共 {len(items)} 条用例）===")
    header = f"{'档位':<22}{'Recall@' + str(k):>10}{'MRR':>9}{'平均延迟':>10}"
    print(header)
    print("-" * len(header))
    scores: dict[str, float] = {}
    for name, flags in variants:
        retriever = Retriever(
            searcher,
            embedder,
            RetrieverConfig(
                top_k=settings.retrieval_top_k,
                min_score=0.0,  # 要测排序质量本身，阈值会淹没召回指标
                enable_filter=flags["enable_filter"],
                enable_lexical=flags["enable_lexical"],
                # ★ 评测一律走**精确扫描**：消融要测的是"检索策略本身"，
                #   而 HNSW 在万条以下的库上近似误差比它省的时间更致命
                #   （实测 70 条文档：精确扫描 Top1 命中，走 HNSW 归零）。
                #   想验证 HNSW 行为请用 tests/test_rerank_hnsw.py 的内存替身。
                use_ann=False,
                enable_hnsw=False,
                # 未声明的档位沿用默认策略（weighted），这样加新档位时不必逐条补字段
                fusion=flags.get("fusion", "weighted"),
                rrf_k=settings.retrieval_rrf_k,
            ),
            lexical=searcher,
        )
        rows = await run_retrieval_eval(
            retriever, items, user_id=user_id, k=k, parse_node=parse_node
        )
        if len(items) <= 5:
            # 小样本调试模式：逐条打印，否则"跑 3 条"和"跑 26 条"输出长得一样，
            # 你无法确认 --limit 真的生效了、也无法定位是哪一条在拖慢。
            _print_case_detail(name, rows)
        stats = aggregate(rows)
        latency = _mean_or_none([float(row.latency_ms) for row in rows])
        recall = stats.get("rag.recall_at_k")
        if recall is not None:
            scores[name] = recall
        print(
            f"{name:<22}"
            f"{_fmt(recall):>10}"
            f"{_fmt(stats.get('rag.mrr')):>9}"
            f"{_fmt(latency, digits=1):>10}"
        )

    _print_ablation_verdict(scores)


def _print_case_detail(variant: str, rows: list[EvalRow]) -> None:
    """小样本时逐条打印：确认 `--limit` 生效、并看清每条各自命中没命中。"""
    print(f"  └ {variant} 逐条：")
    for row in rows:
        recall = _fmt(row.recall_at_k, digits=0)
        print(f"     [{recall}] {row.question[:34]}")


def _print_ablation_verdict(scores: dict[str, float]) -> None:
    """把出口条件**直接判出来**，而不是留给人肉对比两个小数。"""
    baseline = scores.get("+词面召回（混合）")
    rrf = scores.get("+RRF 融合（W4-3）")
    pure_vector = scores.get("纯向量（阶段 0）")
    print("-" * 52)
    if pure_vector is not None and baseline is not None:
        ok = baseline >= pure_vector
        print(
            f"{'阶段 2 出口（混合 ≥ 纯向量）':<30}"
            f"{'✓ 通过' if ok else '✗ 未达标'}"
            f"  {pure_vector:.4f} → {baseline:.4f}"
        )
    if baseline is not None and rrf is not None:
        ok = rrf >= baseline
        print(
            f"{'W4-3 出口（RRF ≥ 阶段 3 基线）':<30}"
            f"{'✓ 通过' if ok else '✗ 未达标'}"
            f"  {baseline:.4f} → {rrf:.4f}"
        )


async def run_offline_ablation(args: argparse.Namespace, items) -> int:
    """离线消融：演示语料 + 内存检索，全程不连数据库。

    与真实库跑的是**同一套** `Retriever` 与同一批评测用例，
    差别只在检索后端（内存余弦 vs pgvector），所以顺序与量级可比、
    绝对值不可比 —— 它用来判定"加了这一档有没有变好"，不是用来对外报数字的。
    """
    import json
    import uuid

    settings = get_settings()
    embedder = build_embedder(settings)
    user_id = uuid.uuid4()

    from app.agent.rag.inmemory import CORPUS_FILES, InMemoryVectorSearch

    searcher = InMemoryVectorSearch()
    for name in CORPUS_FILES:
        path = settings.backend_dir / "data" / name
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            hit = SearchHit(
                ref=DocumentRef(
                    source_type=record["source_type"],
                    source_id=str(record["source_id"]),
                    chunk_index=0,
                ),
                content=record["content"],
                metadata=record.get("metadata") or {},
                score=1.0,
            )
            await searcher.add(user_id, hit, await embedder.embed_one(record["content"]))

    print(
        "⚠ 离线模式（--offline）：内存检索 + 哈希向量，仅用于验证顺序与趋势，绝对值不可作为对外指标"
    )
    await run_ablation(
        items,
        user_id=user_id,
        k=args.top_k,
        embedder=embedder,
        searcher=searcher,
        settings=settings,
    )
    return 0


class AblationFlags(TypedDict, total=False):
    """消融档位的开关组合。

    用 TypedDict 而不是裸 `dict(...)`：`dict()` 会把值推成 `object`，
    mypy 随后在 `RetrieverConfig(**flags)` 处逐个报错，
    而真正的风险是**拼错字段名**（比如 `fusion` 写成 `fustion`）无人发现 ——
    TypedDict 让这种错在静态检查阶段就暴露。
    """

    enable_filter: bool
    enable_lexical: bool
    fusion: str


def _mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def async_main(args: argparse.Namespace) -> int:
    setup_logging(get_settings().log_level)

    # 解析准确率不需要数据库：先处理，方便在没起 Docker 时也能验证 W2-1
    if args.time:
        cases = load_time_cases()
        accuracy = run_time_parse_eval(cases)
        return 0 if accuracy >= 1.0 else 1

    # `--tools` 刻意排在**数据库探活之前**（设计 §3.10）：工具选择评测不碰库，
    # 离线也必须能出数值，否则 E1 环境下这条出口条件永远判定不了。
    if args.tools:
        tool_cases = load_tool_cases()
        accuracy = run_tool_selection_eval(tool_cases)
        return 0 if accuracy >= TOOL_SELECT_TARGET else 1

    items = load_dataset(args.dataset)
    if args.limit:
        items = items[: args.limit]

    # 离线模式（阶段 4 W4-3）：把演示语料装进内存检索器，不碰数据库。
    # 存在意义：`make eval-ablation` 是 Recall@5 红线的判定入口，
    # 要求"必须起 Docker"等于这条红线在本机长期处于未验证状态。
    if args.offline:
        return await run_offline_ablation(args, items)

    # 先探连通性：数据库起不来就给一句人话，而不是抛一堆连接栈
    if not await ping_database():
        print("✗ 数据库不可达。请依次执行：make up → make migrate → make seed-demo")
        print("  （无数据库时可用 `python -m app.agent.eval.runner --ablation --offline` 验证）")
        return 1

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)

        if args.compare:
            await print_compare(session)
            return 0

        from app.agent.rag.retriever import Retriever, RetrieverConfig
        from app.services.document_service import DocumentService

        settings = get_settings()
        embedder = build_embedder(settings)
        searcher = DocumentService(session)

        rows: list[EvalRow]
        if args.ablation:
            await run_ablation(
                items,
                user_id=user.id,
                k=args.top_k,
                embedder=embedder,
                searcher=searcher,
                settings=settings,
            )
            await dispose_engine()
            return 0

        if args.full:
            pipeline = AgentPipeline.build(searcher)
            rows = await run_full_eval(pipeline, items, user_id=user.id, k=args.top_k)
        else:
            # 检索评测刻意把 min_score 设成 0：要测的是「排序质量」本身，
            # 相似度阈值会在 --full 的真实流程里体现，否则阈值会淹没召回指标。
            retriever = Retriever(
                searcher,
                embedder,
                RetrieverConfig(top_k=settings.retrieval_top_k, min_score=0.0),
                lexical=searcher,
            )
            parse_node = None if args.no_parse else QueryParseNode(build_llm(settings))
            rows = await run_retrieval_eval(
                retriever, items, user_id=user.id, k=args.top_k, parse_node=parse_node
            )

        print_report(
            rows,
            k=args.top_k,
            mode="full" if args.full else ("retrieval+parse" if not args.no_parse else "retrieval"),
        )
        try:
            await save_results(
                session, rows, items, user_id=user.id, embedding_model=embedder.model_version
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("评测结果入库失败（不阻塞本次报告）: %s", exc)

    await dispose_engine()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
