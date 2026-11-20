"""演示语料导入脚本（`make seed-demo`）。

为什么需要它：阶段 0 还没有真实照片管线（那是阶段 6 的 W6-1），
但 RAG 必须能被验证，所以用一批人工撰写的旅行记录充当数据源，
把索引链路（切块 → 向量化 → 落 Document）完整跑一遍。

真实数据接入后，本脚本可以被 `jobs/index_job.py` 完全取代。

用法：
```bash
python -m app.scripts.seed_demo           # 幂等增量导入
python -m app.scripts.seed_demo --force   # 清空该用户的 document 后重导
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import delete

from app.agent.rag.indexer import DocumentIndexer
from app.core.config import Settings, get_settings
from app.core.embedding import build_embedder
from app.core.logging import get_logger, setup_logging
from app.db.checks import run_startup_checks
from app.db.session import SessionFactory, dispose_engine, ping_database
from app.models.document import Document
from app.schemas.document import DocumentRef
from app.services.user_service import get_or_create_demo_user

logger = get_logger(__name__)

#: 默认语料文件：行程记录 + 地点记录，两类的 source_type 不同
DEFAULT_CORPUS_FILES = ("demo_corpus.jsonl", "demo_places.jsonl")


def load_corpus(data_dir: Path, files: tuple[str, ...]) -> list[dict]:
    """读取语料 JSONL（每行一个记录）。"""
    records: list[dict] = []
    for name in files:
        path = data_dir / name
        if not path.exists():
            logger.warning("语料文件不存在，跳过：%s", path)
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {lineno} 行不是合法 JSON: {exc}") from exc
    return records


async def seed(*, force: bool = False, data_dir: Path | None = None) -> int:
    """导入演示语料，返回成功索引的 chunk 总数。"""
    settings: Settings = get_settings()
    data_dir = data_dir or (settings.backend_dir / "data")

    # 数据库起不来就直接给提示，不要抛连接栈把人劝退
    if not await ping_database():
        print("✗ 数据库不可达。请先执行：make up && make migrate")
        await dispose_engine()
        return 0

    records = load_corpus(data_dir, DEFAULT_CORPUS_FILES)
    if not records:
        print(f"没有找到演示语料，检查目录：{data_dir}")
        return 0

    embedder = build_embedder(settings)
    total_chunks = 0

    async with SessionFactory() as session:
        # 先做一致性自检：扩展缺失 / 维度不符 / 脏向量都要在写数据之前拦住
        problems = await run_startup_checks(session, settings)
        if problems:
            print("数据库自检未通过：")
            for problem in problems:
                print(f"  - {problem}")
            print("\n请先执行：make up && make migrate")
            return 0

        user = await get_or_create_demo_user(session)

        if force:
            await session.execute(delete(Document).where(Document.user_id == user.id))
            print(f"已清空该用户既有 document（user_id={user.id}）")

        indexer = DocumentIndexer(session, embedder)
        print(f"开始导入 {len(records)} 条记录（embedding 模型：{embedder.model_version}）")

        for record in records:
            ref = DocumentRef(
                source_type=record["source_type"],
                source_id=str(record["source_id"]),
            )
            result = await indexer.index_source(
                user_id=user.id,
                source_type=ref.source_type,
                source_id=ref.source_id,
                text=record["content"],
                metadata=record.get("metadata") or {},
            )
            total_chunks += result.chunks
            print(f"  ✓ {result}")

        await session.commit()
        print(f"\n导入完成：{len(records)} 条记录 → {total_chunks} 个 chunk")
        print("下一步：make cli")

    await dispose_engine()
    return total_chunks


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入演示语料到 Document 表")
    parser.add_argument("--force", action="store_true", help="清空现有 document 后重导")
    parser.add_argument("--data-dir", type=Path, default=None, help="语料目录（默认 backend/data）")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    setup_logging(get_settings().log_level)
    raise SystemExit(0 if asyncio.run(seed(force=args.force, data_dir=args.data_dir)) >= 0 else 1)


if __name__ == "__main__":
    main()
