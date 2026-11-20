"""阶段 0 的入口：命令行问答（`make cli`）。

```bash
python -m app.cli                      # 交互式问答
python -m app.cli --query "我去年秋天去过哪些古镇？"
python -m app.cli --check              # 只做环境自检，不提问
```

设计文档第十一章：阶段 0 不建 HTTP 接口，只做 CLI —— 先把 RAG 跑明白。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import uuid

from app.agent.observability import Trace
from app.agent.pipeline import AgentPipeline
from app.agent.prompts.loader import PROMPT_VERSION
from app.core.config import get_settings
from app.core.embedding import build_embedder
from app.core.llm import build_llm
from app.core.logging import get_logger, setup_logging
from app.db.checks import run_startup_checks
from app.db.session import SessionFactory, dispose_engine, ping_database
from app.services.document_service import DocumentService
from app.services.user_service import get_or_create_demo_user
from app.utils.text import truncate

logger = get_logger(__name__)

BANNER = """
╔══════════════════════════════════════════════╗
║   旅忆地图 TravelMemory · 阶段 0 · RAG CLI    ║
╚══════════════════════════════════════════════╝"""


def _print_help() -> None:
    print("输入问题开始问答；直接回车跳过；输入 exit 退出。")


async def run_check() -> bool:
    """环境自检：数据库连通性 + 扩展 + 向量维度 + embedding 模型一致性。"""
    settings = get_settings()
    print("=== 环境自检 ===")
    if not await ping_database():
        print("✗ 数据库不可达。请先执行：make up && make migrate")
        return False
    print("✓ 数据库已连通")

    async with SessionFactory() as session:
        problems = await run_startup_checks(session, settings)
        if problems:
            for problem in problems:
                print(f"✗ {problem}")
            return False
        count = await DocumentService(session).count()
        print(f"✓ 扩展与向量维度检查通过（document 表 {count} 条记录）")
        if count == 0:
            print("  ⚠ 库是空的，先执行：make seed-demo")
    return True


async def _ask_once(
    *,
    user_id: uuid.UUID,
    session_id: str,
    query: str,
    history: list[dict[str, str]],
    embedder,
    llm,
) -> None:
    """跑一次问答并打印结果（含引用证据，便于人工核对「有没有编造」）。"""
    async with SessionFactory() as session:
        service = DocumentService(session)
        pipeline = AgentPipeline.build(service, embedder=embedder, llm=llm)
        trace = Trace(session=session, user_id=user_id, session_id=session_id, query=query)
        state = await pipeline.run(
            user_id=user_id, session_id=session_id, query=query, history=history, trace=trace
        )
        await session.commit()

    print(f"\n助手 > {state.answer}")
    if state.refused:
        print(f"（已拒答：{state.refuse_reason}）")
    if state.blocks:
        print("\n证据：")
        for block in state.blocks:
            print(f"  [{block.index}] {block.ref}  score={block.score:.3f}")
            print(f"      {truncate(block.content, 70)}")
    usage = state.token_usage
    print(
        f"\n用时 {state.latency_ms}ms | prompt_version={PROMPT_VERSION} | "
        f"tokens={usage.get('prompt_tokens', 0)}+{usage.get('completion_tokens', 0)} | "
        f"成本≈{usage.get('cost_cents', 0):.4f}分"
    )


async def interactive(user_id: uuid.UUID, session_id: str) -> None:
    """交互问答循环。"""
    settings = get_settings()
    embedder = build_embedder(settings)
    llm = build_llm(settings)
    history: list[dict[str, str]] = []

    print(BANNER)
    print(f"LLM      : {llm.provider} / {llm.model}")
    print(f"Embedding: {embedder.provider} / {embedder.model_version}")
    print(f"prompt   : {PROMPT_VERSION}")
    if llm.provider == "mock" or embedder.provider == "hashing":
        print(
            "\n⚠ 当前处于离线占位模式（未配置 API Key）。\n"
            "  回答由本地规则或哈希向量生成，只用于验证链路是否通畅，**不代表检索质量**。\n"
            "  在 .env 里填 LLM_API_KEY / LLM_BASE_URL 后重启即可切到真实模型。"
        )
        # 哈希向量的余弦相似度普遍只有 0.05~0.13，沿用真实模型的阈值会导致"全部拒答"，
        # 这里主动提醒，省得用户以为管道坏了。
        if embedder.provider == "hashing" and settings.retrieval_min_score >= 0.2:
            print(
                f"  ⚠ 哈希向量相似度普遍偏低，当前 RETRIEVAL_MIN_SCORE={settings.retrieval_min_score} "
                "会把证据全过滤掉（表现为一直拒答）。\n"
                "    请把 .env 里的 RETRIEVAL_MIN_SCORE 改成 0.05 后重启。"
            )
    _print_help()

    while True:
        try:
            query = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return
        if query.lower() in {"exit", "quit", "q", ":q"}:
            print("再见！")
            return
        if not query:
            continue

        try:
            await _ask_once(
                user_id=user_id,
                session_id=session_id,
                query=query,
                history=history,
                embedder=embedder,
                llm=llm,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 要友好，不抛栈
            logger.exception("问答失败")
            print(f"出错了：{exc}")

        history.append({"role": "user", "content": query})
        if len(history) > 6:  # 轻量滑动窗口，阶段 4 会被正式短期记忆替换
            history = history[-6:]


async def async_main(args: argparse.Namespace) -> int:
    setup_logging(get_settings().log_level)

    if not await run_check():
        return 1
    if args.check:
        return 0

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)

    session_id = args.session_id or f"cli-{uuid.uuid4().hex[:8]}"

    try:
        if args.query:
            settings = get_settings()
            embedder = build_embedder(settings)
            llm = build_llm(settings)
            await _ask_once(
                user_id=user.id,
                session_id=session_id,
                query=args.query,
                history=[],
                embedder=embedder,
                llm=llm,
            )
        else:
            await interactive(user.id, session_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C 退出时，asyncio 会在关闭连接池的瞬间抛出 CancelledError。
        # 这是正常的退出流程，不该打印一堆噪音吓人。
        print("\n已退出。")
    finally:
        # Ctrl+C 后事件循环正在关闭，dispose 可能抛 Cancelled/Runtime，
        # 这两种都是"已经退出"的正常噪音，吞掉即可。
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await dispose_engine()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TravelMemory RAG CLI")
    parser.add_argument("--query", "-q", type=str, default=None, help="单条问答后退出")
    parser.add_argument("--session-id", type=str, default=None, help="自定义会话 id")
    parser.add_argument("--check", action="store_true", help="只做环境自检")
    return parser.parse_args(argv)


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
