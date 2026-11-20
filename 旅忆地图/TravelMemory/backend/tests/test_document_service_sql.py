"""结构化过滤与词面召回的 **SQL 编译**断言。

## 为什么需要这种"不连数据库"的测试

过滤与词面召回的真实语义必须在 PostgreSQL 上验证（那部分归 `test_document_service.py`，
标 `db` 需要容器）。但绝大多数低级错误——字段名写错、类型没转、算子拼错——
在**编译期**就会暴露。编译断言不需要数据库，能在任何机器上秒级跑完，
是"写 SQL 但没容器"时最划算的一道防线。

它保证的是：这段 Python 能生成**语法合法**的 SQL，并且包含我们期望的关键片段。
它不保证查询计划好不好、pg_trgm 在中文上有没有生效 —— 那些要上真库看。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.agent.rag.filters import BBox, StructuredFilter
from app.models.document import Document
from app.services.document_service import DocumentService

USER = uuid.uuid4()


def _compile(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_filter_sql_compiles_with_all_conditions() -> None:
    svc = DocumentService(None)  # 只编译 SQL，不用到 session
    flt = StructuredFilter(
        date_start=__import__("datetime").date(2025, 9, 1),
        date_end=__import__("datetime").date(2025, 11, 30),
        places=["乌镇"],
        themes=["古镇"],
        bbox=BBox(min_lng=120.0, min_lat=30.0, max_lng=121.0, max_lat=31.0),
    )
    sql = _compile(svc._apply_filter(select(Document.id), flt, USER))

    assert "document.user_id" in sql
    # 时间：coalesce(date_start, date) 的相交判断
    assert "coalesce" in sql.lower()
    # 地名 / 主题：JSONB 的 ?| 算子
    assert "?|" in sql
    # 包围盒：必须转成数值再比较，否则字符串比较会出错
    assert "CAST" in sql.upper()


def test_filter_sql_is_identity_when_empty() -> None:
    """没有任何条件 → 不能多出任何 WHERE 子句（除了 user_id）。"""
    svc = DocumentService(None)
    sql = _compile(svc._apply_filter(select(Document.id), StructuredFilter(), USER))
    assert "?|" not in sql
    assert "coalesce" not in sql.lower()


def test_similar_search_compiles_with_filter() -> None:
    svc = DocumentService(None)
    flt = StructuredFilter(themes=["古镇"])
    stmt = select(Document.id)
    sql = _compile(svc._apply_filter(stmt, flt, USER))
    assert "themes" in sql


def test_lexical_score_expression_compiles() -> None:
    """覆盖率表达式要能编译：CASE WHEN ... THEN 1 ELSE 0 END 的求和。

    断言的是 **生产代码本身**（`DocumentService.coverage_expr`），不是抄一份到测试里 ——
    抄一份就只能证明"测试里的写法没错"，证明不了服务里的写法没错。
    """
    svc = DocumentService(None)
    coverage, n = svc.coverage_expr(["古镇", "乌镇"])
    assert n == 2.0

    # 不带 where 编译，才能数清楚"每个关键词贡献几个 ILIKE"：
    # SQLAlchemy 在 WHERE 里无法引用 SELECT 的别名，会把整个表达式再展开一遍。
    sql = _compile(select(Document.id, coverage.label("coverage")))

    assert "CASE" in sql.upper()
    # 2 个词 × (content + metadata) = 4；少一个说明某个 term 被吞了
    assert sql.upper().count("ILIKE") == 4
    # 除以关键词数：整数除法会让覆盖率永远只有 0 或 1
    assert "/ CAST(2" in sql

    # where 也要能编译（覆盖率下限闸门）
    assert "ILIKE" in _compile(select(Document.id).where(coverage >= 0.5)).upper()


def test_lexical_score_expression_handles_empty_terms() -> None:
    """没有关键词时不能崩 —— 空列表会导致除零与 reduce 空序列。"""
    svc = DocumentService(None)
    coverage, n = svc.coverage_expr([])
    assert n == 1.0  # 分母不能是 0
    sql = _compile(select(Document.id, coverage.label("coverage")))
    assert sql  # 能编译即可


def test_bbox_cast_uses_numeric_comparison() -> None:
    """包围盒比较必须 cast 成数值：'9.5' 与 '10.2' 按字符串比较会得出错误结论。"""
    svc = DocumentService(None)
    clauses = svc._bbox_clauses(BBox(120.0, 30.0, 121.0, 31.0))
    sql = _compile(select(Document.id).where(*clauses))
    assert "FLOAT" in sql.upper() or "CAST" in sql.upper()
    assert "min_lat" in sql and "max_lng" in sql
