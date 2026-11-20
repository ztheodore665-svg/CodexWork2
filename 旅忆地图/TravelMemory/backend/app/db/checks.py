"""数据层自检。

设计文档 §4.4 要求「启动时校验库内主流值与配置一致，不一致直接报错退出」，
因为脏向量的后果非常隐蔽：检索看起来还能返回东西，但质量已经崩了。

三件事必须查：
  1. 三个扩展都在
  2. document.embedding 的实际维度 == EMBEDDING_DIM
  3. 库内占多数的 embedding_model == 当前配置（换模型没 reindex 时立刻暴露）
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

REQUIRED_EXTENSIONS = ("postgis", "vector", "pg_trgm")


async def check_extensions(session: AsyncSession) -> list[str]:
    """检查必备扩展。返回问题描述列表，空列表表示通过。"""
    rows = (await session.execute(text("SELECT extname FROM pg_extension"))).scalars().all()
    installed = set(rows)
    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if missing:
        return [f"缺少扩展 {missing}，请执行 make migrate（首次迁移会创建扩展）"]
    return []


async def check_embedding_dim(session: AsyncSession, settings: Settings) -> list[str]:
    """检查库中 embedding 列的实际维度是否与配置一致。"""
    result = await session.execute(
        text(
            """
            SELECT a.atttypmod
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'document' AND a.attname = 'embedding'
            """
        )
    )
    typmod = result.scalar_one_or_none()
    if typmod is None:
        return ["未找到 document.embedding 列，请先执行 make migrate"]
    # pgvector：typmod 即声明的维度
    if int(typmod) != settings.embedding_dim:
        return [
            f"向量维度不一致：库中 {int(typmod)} 维，配置 EMBEDDING_DIM={settings.embedding_dim}。"
            "需新建迁移改列 + make reindex，不可直接改配置继续跑"
        ]
    return []


async def check_embedding_model(session: AsyncSession, settings: Settings) -> list[str]:
    """检查库内向量的 embedding_model，**任何混用都算问题**。

    为什么不再只看"主流"那一批：曾经的做法是取 count 最多的模型与配置比对，
    于是 39 条真实向量 + 31 条旧哈希向量的库会被判为"通过" ——
    少数派脏向量足以把召回打乱，却因为不是主流而被放过。
    这类问题的表现极其隐蔽：检索照样返回东西，只是该命中的不命中。

    因此这里分两条独立判据：
      1. 库里出现了**一个以上**的 model → 混用，必须 reindex
      2. 主流 model 与当前配置不一致 → 换过模型没重建
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT embedding_model, count(*) AS n
                FROM document
                WHERE embedding_model IS NOT NULL
                GROUP BY embedding_model
                ORDER BY n DESC
                """
            )
        )
    ).all()
    if not rows:
        return []  # 空库：还没有向量，谈不上脏

    problems: list[str] = []
    if len(rows) > 1:
        breakdown = "，".join(f"{model} × {int(n)}" for model, n in rows)
        problems.append(
            f"库内向量来自多个模型（{breakdown}）—— 混用时查询只会和同源的那批相似，"
            "另一批的相似度等于随机数，表现为「该命中的不命中」。请执行 make reindex。"
        )

    dominant, count = rows[0][0], int(rows[0][1])
    if dominant != settings.embedding_model_version:
        problems.append(
            f"库内主流向量来自 {dominant}（{count} 条），当前配置是 "
            f"{settings.embedding_model_version}：请先执行 make reindex。"
        )
        # 离线占位模式最容易撞上这条：库里是哈希向量，.env 却还写着 bge-m3
        problems.append(
            "如果你在用离线占位模式（本地哈希向量），请把 .env 改成："
            "EMBEDDING_PROVIDER=hashing / EMBEDDING_MODEL=hashing / "
            "EMBEDDING_MODEL_VERSION=hashing-1024@v1（维度随 EMBEDDING_DIM 变化）"
        )
    return problems


async def run_startup_checks(session: AsyncSession, settings: Settings | None = None) -> list[str]:
    """跑全部自检，返回问题描述列表（空列表 = 全部通过）。"""
    settings = settings or get_settings()
    problems: list[str] = []
    problems += await check_extensions(session)
    if problems:
        return problems  # 扩展都没有，后面的查询会直接报错
    problems += await check_embedding_dim(session, settings)
    problems += await check_embedding_model(session, settings)
    return problems
