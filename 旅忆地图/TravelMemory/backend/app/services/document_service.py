"""Document 的读写服务。

职责边界（设计文档 §5.1）：本服务只做文档 CRUD 与「底层向量/结构化查询」，
**不**做查询改写、融合、重排 —— 那些属于 `agent/rag/retriever.py`。

阶段 0 刻意用**精确扫描**（ORDER BY 距离 + LIMIT），不建 HNSW：
pgvector 的 HNSW 是先遍历图再套 WHERE，带 user_id 过滤时可能一条都不返回。
先用精确扫描保证正确性，阶段 3+ 再引入索引并配套「结果不足回退精确扫描」。

阶段 2（W2-2）在这里增加两件事：

1. **结构化过滤**：时间窗 / 地名 / 主题 / 包围盒，全部作用在 `metadata` JSONB 上。
   之所以过滤条件读 JSONB 而不是加列：Document 的 metadata 是「随数据源而变」的，
   加列会让每种 source_type 都要迁移一次；JSONB + GIN 索引对万级数据完全够用。
2. **全文（词面）召回** `lexical_search`：向量对专名（"乌镇""兵马俑"）并不比
   子串匹配更准，两路互补才是混合检索的意义。
"""

from __future__ import annotations

import operator
import typing
import uuid
from collections.abc import Sequence
from functools import reduce
from typing import Any

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Float,
    String,
    and_,
    case,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.rag.filters import StructuredFilter
from app.models.document import Document
from app.schemas.document import DocumentRef, DocumentUpsert, SearchHit


def _rowcount(result: object) -> int:
    """取 DML 影响行数。

    `session.execute(delete(...))` 的静态类型是 `Result`，但运行时返回的是
    `CursorResult`（才有 `rowcount`）。这里用 `typing.cast` 表达这个
    "静态类型比运行时窄"的事实 —— 注意必须写全 `typing.cast`，
    因为本模块从 sqlalchemy 导入的 `cast` 是 SQL 的 CAST 函数。
    """
    return int(typing.cast(CursorResult[Any], result).rowcount or 0)


class DocumentService:
    """注入 AsyncSession 构造，方便批处理脚本与测试各自管理事务。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 写
    async def upsert_many(
        self,
        user_id: uuid.UUID,
        items: Sequence[DocumentUpsert],
        vectors: Sequence[Sequence[float]],
        *,
        embedding_model: str,
    ) -> int:
        """批量写入（同 triple 覆盖）。

        冲突目标用唯一约束 `uq_document_source_chunk` 的三个字段，
        因此**重复导入同一数据源不会重复烧 embedding 的钱**，这是幂等重索引的基础。
        """
        if len(items) != len(vectors):
            raise ValueError("items 与 vectors 数量必须一致")
        if not items:
            return 0

        rows = [
            {
                "user_id": user_id,
                "source_type": item.ref.source_type,
                "source_id": item.ref.source_id,
                "chunk_index": item.ref.chunk_index,
                "content": item.content,
                "metadata": item.metadata or {},
                "token_count": item.token_count,
                "char_len": item.char_len or len(item.content),
                "embedding": list(vector),
                "embedding_model": embedding_model,
            }
            for item, vector in zip(items, vectors, strict=True)
        ]

        # excluded 指向「本次想插入但发生冲突的那一行」，用它做 update 的值
        # 冲突目标必须覆盖唯一键的全部字段（含 user_id），否则不同用户会互相覆盖
        insert_stmt = insert(Document).values(rows)
        excluded = insert_stmt.excluded
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["user_id", "source_type", "source_id", "chunk_index"],
            set_={
                "content": excluded.content,
                "metadata": excluded.metadata,
                "token_count": excluded.token_count,
                "char_len": excluded.char_len,
                "embedding": excluded.embedding,
                "embedding_model": excluded.embedding_model,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        return len(rows)

    async def delete_stale_chunks(
        self, source_type: str, source_id: str, keep_count: int, *, user_id: uuid.UUID
    ) -> int:
        """重索引后清理多余旧 chunk（新的比旧的少时会有残留）。

        比"先全删再插"安全：万一中途失败，旧数据还在，检索不会短暂失效。
        """
        result = await self.session.execute(
            delete(Document).where(
                Document.user_id == user_id,
                Document.source_type == source_type,
                Document.source_id == source_id,
                Document.chunk_index >= keep_count,
            )
        )
        return _rowcount(result)

    async def delete_by_source(
        self, source_type: str, source_id: str | None = None, *, user_id: uuid.UUID | None = None
    ) -> int:
        """删除某个数据源的全部 chunk（source_id 为空则删整个类型）。"""
        stmt = delete(Document).where(Document.source_type == source_type)
        if source_id is not None:
            stmt = stmt.where(Document.source_id == source_id)
        if user_id is not None:
            stmt = stmt.where(Document.user_id == user_id)
        result = await self.session.execute(stmt)
        return _rowcount(result)

    # ------------------------------------------------------------------ 结构化过滤
    def _filter_clauses(self, flt: StructuredFilter | None) -> list:
        """把 StructuredFilter 翻译成 SQL 条件列表（空过滤返回空列表）。

        时间用**区间相交**：一趟行程 2025-10-02~10-05，问"10 月 3 日"也必须命中。
        **没有时间字段的文档不因时间过滤被排除**（IS NULL 直接放行）——
        否则问"我七月去过湖边吗"时所有地点文档会被整体误杀。
        """
        if flt is None or flt.is_empty():
            return []

        meta = Document.metadata_json
        clauses: list = []

        if flt.date_start is not None:
            doc_start = func.coalesce(meta["date_start"].astext, meta["date"].astext)
            # doc_end >= 查询start：文档结束得晚于查询起点
            doc_end = func.coalesce(meta["date_end"].astext, meta["date"].astext)
            clauses.append(or_(doc_end.is_(None), doc_end >= flt.date_start.isoformat()))
        if flt.date_end is not None:
            doc_start = func.coalesce(meta["date_start"].astext, meta["date"].astext)
            clauses.append(or_(doc_start.is_(None), doc_start <= flt.date_end.isoformat()))

        if flt.places:
            arr = cast(list(flt.places), ARRAY(String))
            place_hits = [
                meta["cities"].op("?|")(arr),  # 城市数组
                meta["places"].op("?|")(arr),  # 景区数组
            ]
            for name in flt.places:
                place_hits.append(meta["city"].astext.ilike(f"%{name}%"))
                place_hits.append(meta["name"].astext.ilike(f"%{name}%"))
            clauses.append(or_(*place_hits))

        if flt.themes:
            arr = cast(list(flt.themes), ARRAY(String))
            clauses.append(meta["themes"].op("?|")(arr))

        return clauses

    def _apply_filter(self, stmt, flt: StructuredFilter | None, user_id: uuid.UUID):
        """把 user_id 与结构化条件套到 select 上（bbox 需要数值比较，单独处理）。"""
        clauses = [Document.user_id == user_id]
        if flt is not None and flt.bbox is not None:
            clauses.extend(self._bbox_clauses(flt.bbox))
        for clause in self._filter_clauses(flt):
            clauses.append(clause)
        return stmt.where(and_(*clauses))

    @staticmethod
    def _bbox_clauses(bbox) -> list:
        """包围盒相交条件：两个矩形在经纬度两个方向上都重叠才算命中。

        文档缺 bbox 字段时直接放行 —— 没坐标的文档不该因为"查了某片区域"被误杀，
        这条与时间过滤是同一个原则。
        """
        meta = Document.metadata_json
        out = []
        for key, bound in (
            ("min_lat", bbox.max_lat),
            ("max_lat", bbox.min_lat),
            ("min_lng", bbox.max_lng),
            ("max_lng", bbox.min_lng),
        ):
            raw = meta[key].astext  # metadata->>'min_lat'
            value = raw.cast(Float)  # 必须转数值：'9.5' 与 '10.2' 按字符串比会出错
            compare = value <= bound if key.startswith("min") else value >= bound
            out.append(or_(raw.is_(None), compare))
        return out

    # ------------------------------------------------------------------ 读
    async def count(self, user_id: uuid.UUID | None = None) -> int:
        stmt = select(func.count()).select_from(Document)
        if user_id is not None:
            stmt = stmt.where(Document.user_id == user_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_refs_by_source(self, source_type: str, source_id: str) -> list[DocumentRef]:
        """列出某数据源的所有 chunk 引用（用于判断是否需要重索引）。"""
        stmt = (
            select(Document.source_type, Document.source_id, Document.chunk_index)
            .where(Document.source_type == source_type, Document.source_id == source_id)
            .order_by(Document.chunk_index)
        )
        rows = (await self.session.execute(stmt)).all()
        return [DocumentRef(source_type=r[0], source_id=r[1], chunk_index=r[2]) for r in rows]

    async def similar_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        source_types: Sequence[str] | None = None,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]:
        """余弦相似度检索（精确扫描）。

        注意：`<=>` 是余弦**距离**，所以相似度 = 1 - 距离。

        `flt` 是本阶段新增的结构化过滤。它作用在**扫描之前**（同一个 WHERE），
        因此仍然是精确扫描 —— 这正是我们暂时不建 HNSW 的原因：
        过滤 + 近似索引的组合在 pgvector 里可能一条都不返回。
        """
        distance = Document.embedding.cosine_distance(list(query_vector))
        stmt = select(Document, distance.label("distance")).where(Document.embedding.is_not(None))
        stmt = self._apply_filter(stmt, flt, user_id)
        if source_types:
            stmt = stmt.where(Document.source_type.in_(list(source_types)))
        stmt = stmt.order_by(distance).limit(top_k)

        rows = (await self.session.execute(stmt)).all()
        return [
            SearchHit(
                document_id=doc.id,
                ref=DocumentRef(
                    source_type=doc.source_type,
                    source_id=doc.source_id,
                    chunk_index=doc.chunk_index,
                ),
                content=doc.content,
                metadata=dict(doc.metadata_json or {}),
                score=round(1.0 - float(dist), 6),
            )
            for doc, dist in rows
        ]

    def coverage_expr(self, terms: Sequence[str]) -> tuple[ColumnElement[float], float]:
        """构造「关键词覆盖率」表达式，返回 `(表达式, 关键词数)`。

        单独抽成方法只有一个理由：**让它可以在没有数据库的情况下被编译断言**。
        `lexical_search` 里那句 `select(...)` 要跑起来必须有 session，
        而覆盖率表达式本身是最容易写错（类型转换、转义、除零）的一段，
        抽出来就能在 `tests/test_document_service_sql.py` 里秒级验证。
        """
        cleaned = [t.strip() for t in terms if t and t.strip()]
        if not cleaned:
            # 空关键词：返回一个恒为 0 的可编译表达式，调用方不必再分支。
            return cast(literal(0), Float), 1.0

        n = float(len(cleaned))
        hit_exprs = [
            case(
                (
                    or_(
                        Document.content.icontains(term, autoescape=True),
                        # 整个 metadata 转文本再匹配：一个词的线索可能落在
                        # places / cities / themes 任意一个字段里，逐个字段 or 太啰嗦。
                        # 注意不能写 metadata_json.astext —— ORM 的 InstrumentedAttribute
                        # 不代理 JSONB 的 astext 属性（SQLAlchemy 2.1 实测），必须显式 cast。
                        cast(Document.metadata_json, String).icontains(term, autoescape=True),
                    ),
                    1,
                ),
                else_=0,
            )
            for term in cleaned
        ]
        # 用 reduce 而不是内置 sum：sum 会以整数 0 起步，SQLAlchemy 会把 Python 的
        # int 0 拼进 SQL，导致后续除法与类型表现不一致。
        expr = typing.cast(ColumnElement[float], reduce(operator.add, hit_exprs) / n)
        return expr, n

    async def ann_search(
        self,
        user_id: uuid.UUID,
        query_vector: Sequence[float],
        *,
        top_k: int = 8,
        oversample: int = 4,
        flt: StructuredFilter | None = None,
    ) -> list[SearchHit]:
        """近似检索（HNSW）路径。

        **这个方法刻意把危险路径写得很直白**：内层只做
        `ORDER BY embedding <=> v LIMIT top_k * oversample`（纯 ANN，不带任何过滤），
        外层才施加 `user_id` 与结构化过滤。

        为什么是这个顺序 —— 因为 HNSW 索引一旦参与，pgvector 就是"先遍历图、后套 WHERE"：
        如果过滤条件很严，图里最相似的 N 条可能全被 WHERE 过滤掉，结果就是 **0 条**。
        过采样（多取几倍候选）只能缓解，不能消除。

        **本方法不负责回退**：回退决策在 `Retriever`（它才知道"够不够"）。
        职责分开，回退逻辑才能在无库环境下被单测覆盖。
        """
        distance = Document.embedding.cosine_distance(list(query_vector))
        # 过采样是**乘数**不是下限：candidates = top_k × oversample。
        # 写成 max(top_k, top_k × oversample) 会让 oversample=1 时失去意义，
        # 也就永远暴露不出"候选里没有符合条件的文档"这个真实风险。
        candidate_k = top_k * max(1, oversample)

        # 内层：纯 ANN，只带 user_id（它永远存在），不带任何结构化过滤
        inner = (
            select(Document.id, distance.label("distance"))
            .where(Document.embedding.is_not(None), Document.user_id == user_id)
            .order_by(distance)
            .limit(candidate_k)
            .subquery()
        )

        # 外层：结构化过滤 + 按近似距离再排一次
        outer = select(Document, inner.c.distance).join(inner, inner.c.id == Document.id)
        outer = self._apply_filter(outer, flt, user_id)
        outer = outer.order_by(inner.c.distance).limit(top_k)

        rows = (await self.session.execute(outer)).all()
        return [
            SearchHit(
                document_id=doc.id,
                ref=DocumentRef(
                    source_type=doc.source_type,
                    source_id=doc.source_id,
                    chunk_index=doc.chunk_index,
                ),
                content=doc.content,
                metadata=dict(doc.metadata_json or {}),
                score=round(1.0 - float(dist), 6),
            )
            for doc, dist in rows
        ]

    async def lexical_search(
        self,
        user_id: uuid.UUID,
        terms: Sequence[str],
        *,
        top_k: int = 8,
        flt: StructuredFilter | None = None,
        min_coverage: float = 0.5,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        """词面（全文）召回：关键词子串匹配，分数 = 关键词覆盖率。

        ## 为什么向量之外还要这一路

        embedding 擅长语义，但对**专名**很笨：问"兵马俑"，向量可能把"秦始皇陵"
        和"博物馆"都拉回来，却排不到真正含"兵马俑"三个字的那条。
        子串匹配恰好补上这块 —— 它认的是"字面有没有出现"。

        ## 分数定义

        `覆盖率 = 命中关键词数 / 关键词总数`，取值 0~1。
        之所以不用 pg_trgm 的 `similarity()` 当主分数：它的中文 trigram 效果
        依赖数据库 locale，不同机器结果不一致，不适合做可复现的评测指标。
        这里 `similarity()` 只用于**同分时的排序**（错字 / 语序容错），
        GIN trgm 索引则用来加速 ILIKE 扫描 —— 索引不可用也不影响正确性。

        `min_coverage` 是防噪声闸门：只命中 1/3 关键词的文档不该进来，
        否则"古镇"这种常见词会把半个库捞出来。
        """
        coverage, _n = self.coverage_expr(terms)
        cleaned = [t.strip() for t in terms if t and t.strip()]
        if not cleaned:
            return []

        stmt = select(Document, coverage.label("coverage")).where(coverage >= min_coverage)
        stmt = self._apply_filter(stmt, flt, user_id)
        order_by = [coverage.desc()]
        if query_text:
            # 同覆盖率时按 pg_trgm 相似度排：长查询串能体现语序与错字差异
            order_by.append(func.similarity(Document.content, query_text).desc())
        stmt = stmt.order_by(*order_by).limit(top_k)

        rows = (await self.session.execute(stmt)).all()
        return [
            SearchHit(
                document_id=doc.id,
                ref=DocumentRef(
                    source_type=doc.source_type,
                    source_id=doc.source_id,
                    chunk_index=doc.chunk_index,
                ),
                content=doc.content,
                metadata=dict(doc.metadata_json or {}),
                score=round(float(cov), 6),
            )
            for doc, cov in rows
        ]

    async def embedding_stats(self) -> dict[str, int]:
        """各 embedding_model 的文档数量（脏向量排查用）。"""
        rows = (
            await self.session.execute(
                select(Document.embedding_model, func.count())
                .group_by(Document.embedding_model)
                .order_by(func.count().desc())
            )
        ).all()
        return {str(model): int(count) for model, count in rows}
