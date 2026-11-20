"""阶段 2 检索索引：JSONB 结构化过滤 + pg_trgm 全文。

## 为什么建这两个索引

阶段 2 的检索从"纯向量扫描"变成"先结构化过滤、再两路召回"：

1. **结构化过滤**全部作用在 `metadata` JSONB 上（时间窗 / 地名 / 主题），
   用到的算子是 `?|`（数组是否包含任一元素）。默认 GIN（`jsonb_ops`）正好支持
   `? / ?| / ?& / @>`，不加索引时每查一次就要全表展开 JSONB。
2. **词面召回**用 `content ILIKE '%关键词%'`。普通 B-tree 索引对前置通配符
   **完全无效**（ LIKE '%x%' 走不了 B-tree），必须靠 pg_trgm 的 GIN 索引。

   注意：pg_trgm 的 trigram 依赖数据库 locale 判断"什么是词字符"，
   **中文环境下它的相似度可能退化**（这一点在阶段文档中写清楚了）。
   索引本身不影响正确性 —— 索引不可用时 Postgres 会退回顺序扫描，
   ILIKE 的子串语义依然成立。所以这里的收益是"加速"，不是"改变行为"。

## 为什么不给 embedding 建 HNSW

设计文档 §3.5：pgvector 的 HNSW 是"先图遍历、后过滤"，带 user_id + 时间 + 空间
过滤时可能返回 0 条。阶段 0~2 一律精确扫描，阶段 3 才建 HNSW 并配套回退策略。
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # JSONB 默认 GIN：支持 ? / ?| / ?& / @>，覆盖 places / themes / cities 的包含判断
    op.create_index(
        "ix_document_metadata_gin",
        "document",
        ["metadata"],
        postgresql_using="gin",
    )
    # pg_trgm GIN：让 content ILIKE '%词%' 能走索引
    op.create_index(
        "ix_document_content_trgm",
        "document",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_content_trgm", table_name="document")
    op.drop_index("ix_document_metadata_gin", table_name="document")
