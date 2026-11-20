"""启动自检的守护测试。

为什么值得单测：这些检查是"防止你把环境搞坏还不自知"的护栏。
护栏失效的表现不是报错，而是**该拦的时候没拦** —— 那种 bug 只能靠测试发现。

本轮真实事故：库里 39 条真实向量 + 31 条旧哈希向量，按"主流模型"比对会判通过
（真实的是多数），脏批次被放过 → 纯向量档 Recall@5 = 0.000，而日志里毫无线索。
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.db.checks import check_embedding_model

pytestmark = pytest.mark.db


class _FakeResult:
    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, int]]:
        return self._rows


class _FakeSession:
    """只提供 `execute(text(...)).all()` —— 刚好够 check_embedding_model 用。"""

    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self.rows = rows

    async def execute(self, _stmt):  # noqa: ANN001, ANN202
        return _FakeResult(self.rows)


CURRENT = get_settings().model_construct().embedding_model_version


async def test_mixed_models_are_reported() -> None:
    """出现一个以上 model 必须报错 —— 这是本轮事故的直接守卫。"""
    session = _FakeSession([("qwen3.7-text-embedding-flash@v1", 39), ("hashing-1024@v1", 31)])
    problems = await check_embedding_model(session, get_settings().model_construct())
    assert any("多个模型" in p for p in problems), problems


async def test_single_matching_model_is_silent() -> None:
    """全部同源且与配置一致时**不报警**。

    这条同样重要：护栏如果误报，人很快就会养成"看到警告就关掉"的习惯，
    于是真警告也被一起关掉。
    """
    settings = get_settings().model_construct()
    session = _FakeSession([(CURRENT, 70)])
    assert await check_embedding_model(session, settings) == []


async def test_single_mismatched_model_still_reported() -> None:
    """只用一个模型但和配置不同（换模型忘了 reindex）也要报。"""
    settings = get_settings().model_construct()
    session = _FakeSession([("old-model@v1", 70)])
    problems = await check_embedding_model(session, settings)
    assert any("reindex" in p for p in problems), problems


async def test_empty_library_is_not_a_problem() -> None:
    """空库谈不上脏向量：还没索引过，不该拿"脏"去责怪用户。"""
    settings = get_settings().model_construct()
    assert await check_embedding_model(_FakeSession([]), settings) == []


# ---------------------------------------------------------------- 真实库集成
@pytest.fixture
async def db_ready():
    from conftest import db_reachable

    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")
    yield


async def test_real_library_reports_mixed_models(db_ready) -> None:
    """在真实库上造一批混用向量，确认自检真的能抓到（而不是只在我编的行上成立）。"""
    import math

    from sqlalchemy import text

    from app.db.session import SessionFactory, dispose_engine
    from app.schemas.document import DocumentRef, DocumentUpsert
    from app.services.document_service import DocumentService
    from app.services.user_service import get_or_create_demo_user

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        service = DocumentService(session)
        tag = uuid.uuid4().hex[:6]
        dim = 1024

        for index, model in enumerate(("model-a@v1", "model-b@v1")):
            raw = [math.sin(0.3 + index + i) for i in range(dim)]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            await service.upsert_many(
                user.id,
                [
                    DocumentUpsert(
                        ref=DocumentRef(
                            source_type="day", source_id=f"chk-{tag}-{index}", chunk_index=0
                        ),
                        content=f"自检用文档 {index}",
                        metadata={"date": "2025-10-01"},
                        token_count=4,
                        char_len=8,
                    )
                ],
                [[v / norm for v in raw]],
                embedding_model=model,
            )
        await session.commit()

        problems = await check_embedding_model(session, get_settings())
        assert any("多个模型" in p for p in problems), f"混用向量必须被报出来，实际：{problems}"

        await session.execute(
            text("delete from document where source_id like :pat"), {"pat": f"chk-{tag}-%"}
        )
        await session.commit()
    await dispose_engine()
