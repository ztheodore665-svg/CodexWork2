"""检索链路诊断（**零 API 成本**：只用库里已存的向量，不发任何外部请求）。

## 为什么需要它

"纯向量档 Recall@5 = 0.000"这种症状有至少四种互不相同的成因：

| 成因 | 表现 | 怎么区分 |
| --- | --- | --- |
| 走了 HNSW 近似 | 精确扫描能命中、近似不能 | 看 `retrieve_debug.index_path` |
| 查询向量与库存向量不同源 | 任何查询都召回不出来 | 用库存向量当查询，看能不能召回自己 |
| user_id 不匹配 | 所有档位都归零 | 直接数库里该 user 有多少条带向量的文档 |
| 阈值把证据滤光 | 有命中但被过滤 | 看返回的 score 与 min_score |

这个脚本把前三项一次问完，而且**不花一分钱** —— 它拿库存文档自己的向量当查询，
任何正确的余弦检索都必须把它自己排在第一。

用法：

```bash
python -m app.scripts.diag_retrieval
```
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.core.logging import setup_logging
from app.db.session import SessionFactory
from app.services.user_service import get_or_create_demo_user


async def main() -> int:
    setup_logging("WARNING")
    print("=== 检索链路诊断（只读库，不发外部请求）===\n")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)

        # ① 库里到底有多少条属于这个用户的向量
        counts = (
            await session.execute(
                text(
                    "select count(*) as total, "
                    "count(*) filter (where embedding is not null) as with_vec "
                    "from document where user_id = :uid"
                ),
                {"uid": user.id},
            )
        ).one()
        print(f"demo user_id            : {user.id}")
        print(f"document 总数 / 有向量  : {counts.total} / {counts.with_vec}")
        if not counts.with_vec:
            print("✗ 库里没有任何带向量的文档 —— 先 make seed-demo && make index-batch")
            return 1

        # ② 向量维度分布：维度不一致是"看着像检索坏了"的另一个常见原因
        dims = (
            await session.execute(
                text(
                    "select vector_dims(embedding) as dim, count(*) "
                    "from document where user_id = :uid and embedding is not null "
                    "group by dim order by dim"
                ),
                {"uid": user.id},
            )
        ).all()
        print(f"库存向量维度分布        : {[(d, c) for d, c in dims]}")

        # ③ 拿库存文档**自己的向量**当查询，正确实现必须把它排第一
        rows = (
            await session.execute(
                text(
                    "select source_id, embedding from document "
                    "where user_id = :uid and embedding is not null "
                    "order by created_at limit 3"
                ),
                {"uid": user.id},
            )
        ).all()

        ok = True
        for source_id, embedding in rows:
            query_vector = list(embedding)
            top = (
                await session.execute(
                    text(
                        "select source_id, 1 - (embedding <=> :q) as score "
                        "from document where user_id = :uid and embedding is not null "
                        "order by embedding <=> :q limit 3"
                    ),
                    {"q": str(query_vector), "uid": user.id},
                )
            ).all()
            hit = top[0].source_id == source_id
            ok = ok and hit
            print(
                f"  用「{source_id}」的向量查询 → Top1 = 「{top[0].source_id}」"
                f"（score {float(top[0].score):.4f}）{'✓' if hit else '✗ 未命中自己'}"
            )

        # ④ 顺带看一眼 HNSW 索引是否存在于这张表上
        idx = (
            await session.execute(
                text(
                    "select indexname from pg_indexes "
                    "where tablename = 'document' and indexdef like '%hnsw%'"
                )
            )
        ).all()
        print(f"\nHNSW 索引              : {[i for (i,) in idx] or '（无）'}")

    print("\n结论：")
    if ok:
        print("✓ 精确扫描链路是通的（库存向量能召回自己）。")
        print("  若评测里纯向量档仍为 0，说明**查询向量与库存向量不同源** ——")
        print("  最常见原因是 embedding 模型/维度在评测时与建库时不一致。")
    else:
        print("✗ 精确扫描链路本身有问题，先查 user_id 过滤与 ORDER BY 方向。")
    return 0 if ok else 1


if __name__ == "__main__":
    _ = uuid  # noqa: F841 - 保留导入以便将来按 user 排查
    raise SystemExit(asyncio.run(main()))
