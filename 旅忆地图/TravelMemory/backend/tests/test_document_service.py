"""依赖真实数据库的基础用例（PostGIS + pgvector）。

没有数据库时自动跳过，不影响 `make test` 的整体结果。
本地要跑这些用例：

```bash
make up          # docker compose：启动自建的 postgis+pgvector 镜像
make migrate     # 建扩展与表
make test
```

本文件验证的是 **W0-1 的出口条件**：两个扩展可用、向量能写入、精确检索返回预期结果。
"""

from __future__ import annotations

import math
import uuid

import pytest
from conftest import db_reachable
from sqlalchemy import text

from app.db.session import SessionFactory, dispose_engine
from app.models.user import User
from app.schemas.document import DocumentRef, DocumentUpsert
from app.services.document_service import DocumentService

pytestmark = pytest.mark.db


@pytest.fixture
async def db_ready():
    """数据库不可用时跳过；可用时返回一个附着到临时用户的 service。"""
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")
    yield
    await dispose_engine()


def _unit_vector(seed: float, dim: int) -> list[float]:
    """构造一个确定性的单位向量（用于验证余弦检索的正确性）。"""
    raw = [math.sin(seed + i) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


async def _create_user(session) -> User:
    user = User(name=f"pytest-{uuid.uuid4().hex[:8]}")
    session.add(user)
    await session.flush()
    return user


async def _column_dim(session) -> int:
    """读取库中 embedding 列的真实维度。

    不能用 settings.embedding_dim：单测环境（conftest）把它改成了 256，
    而真实库的列是迁移时按当时的 EMBEDDING_DIM 建的（1024）。
    """
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
    return int(result.scalar_one())


async def test_extensions_installed(db_ready):
    async with SessionFactory() as session:
        rows = (await session.execute(text("SELECT extname FROM pg_extension"))).scalars().all()
        installed = set(rows)
        assert {"postgis", "vector", "pg_trgm"} <= installed, "三个扩展必须都可用"


async def test_upsert_is_idempotent_and_searchable(db_ready):
    async with SessionFactory() as session:
        user = await _create_user(session)
        service = DocumentService(session)
        dim = await _column_dim(session)

        item = DocumentUpsert(
            ref=DocumentRef(
                source_type="note", source_id=f"unit-{uuid.uuid4().hex[:8]}", chunk_index=0
            ),
            content="单元测试用的乌镇游记",
            metadata={"city": "嘉兴"},
            token_count=10,
            char_len=10,
        )
        vector = _unit_vector(1.0, dim)

        await service.upsert_many(user.id, [item], [vector], embedding_model="unit-test@v1")
        await session.commit()

        hits = await service.similar_search(user.id, vector, top_k=3)
        assert hits, "精确检索必须返回刚写入的文档"
        assert hits[0].ref.key == item.ref.key
        assert hits[0].score > 0.99, "自己跟自己比，余弦相似度应接近 1"

        # 重复写入同一三元组不应产生新行
        await service.upsert_many(user.id, [item], [vector], embedding_model="unit-test@v1")
        await session.commit()
        count = await service.count(user.id)
        assert count == 1, "同一 (source_type, source_id, chunk_index) 必须被覆盖而不是新增"

        # 清理
        await service.delete_by_source("note", item.ref.source_id, user_id=user.id)
        await session.commit()
