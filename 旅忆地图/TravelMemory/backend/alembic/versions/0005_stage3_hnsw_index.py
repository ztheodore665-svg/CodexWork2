"""阶段 3：embedding 上的 HNSW 近似索引。

## 为什么拖到阶段 3 才建

设计文档第 241 行是硬约束：pgvector 的 HNSW **先遍历图、后套 WHERE**，
带 `user_id` + 时间 + 空间过滤时可能返回 **0 条**。阶段 0~2 不需要这个风险，
一律精确扫描。

阶段 3 建它的前提，是**回退策略已经就位**：`Retriever` 在 ANN 命中不足
（< `filter_min_hits`）时自动回退 `similar_search`（精确扫描），
并在 `retrieve_debug.index_path` 上留下 `exact_fallback` 的痕迹。
**没有回退就建索引 = 把"偶尔召回为 0"变成永久 bug。**

## 参数选择

- `m=16`：每个节点 16 条边。万级数据的常用值，再大收益递减且建索引变慢
- `ef_construction=64`：建图时的候选队列长度，影响建索引速度与图质量
- 查询期的 `ef_search` 由会话参数控制（本迁移不设），默认值够用

## 为什么 downgrade 必须存在

这条索引是**可以安全丢弃**的：它只影响查询计划，不影响数据。
丢掉它之后行为退化为精确扫描（阶段 2 行为），所以回滚是安全且必要的。
"""

from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # using=None 让 Alembic 原样下发 `USING hnsw`（它不认识 pgvector 的算子类名）
    op.create_index(
        "ix_document_embedding_hnsw",
        "document",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_embedding_hnsw", table_name="document")
