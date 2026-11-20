"""Reranker：把融合后的候选集重新排序（阶段 3 W3-4）。

## 为什么有了融合还需要重排

融合（阶段 2）解决的是"两路分数怎么放一起比"，它用的是**召回时顺手算出来的分数**：
向量余弦、关键词覆盖率。这两者都是"单点相似度"，看的是 query 与文档各自像不像。

Reranker 做的是另一件事：把 (query, 文档) **拼在一起**送进模型，让模型判断
"这段证据能不能回答这个问题"。它能捕捉融合捕捉不到的东西：
否定词、条件状语、问题与证据的类型错配（问"几点关门"，证据里写的是"建于明代"）。

一句话：**融合决定"哪些进来"，重排决定"谁在最前面"。**

## 为什么必须有 no-op 实现

`RERANK_ENABLED` 默认 False（PROJECT.md §二、[8]）。关闭时 `NoOpReranker` 必须让
输出顺序与阶段 2 **逐条一致** —— 这是 `make test-degradation` 的核心断言之一。
如果"关掉重排"会改变顺序，那这个开关就不是降级开关，而是行为开关。

## 为什么远程重排失败要静默降级

重排是旁路中的旁路：它失败不应该让问答失败，甚至不该让证据变少，
只是"这一次的排序没优化"。所以 `RemoteReranker` 出错时返回 `None`，
由调用方保留原顺序。
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger
from app.schemas.document import SearchHit

logger = get_logger(__name__)


class BaseReranker(Protocol):
    """重排契约。`rerank()` 返回 `None` 表示"本次不重排，保留原顺序"。"""

    async def rerank(
        self, query: str, hits: list[SearchHit], top_n: int
    ) -> list[SearchHit] | None: ...


class NoOpReranker:
    """不重排。**返回原顺序的副本**，而不是 None —— 两者语义不同：

    - 返回副本：关闭重排时的正常路径（顺序与阶段 2 一致）
    - 返回 None：重排"没做成"（远程失败），调用方用法相同但会在 trace 里留痕
    """

    async def rerank(self, query: str, hits: list[SearchHit], top_n: int) -> list[SearchHit] | None:
        return list(hits[:top_n]) if top_n > 0 else list(hits)


class RemoteReranker:
    """远程重排（bge-reranker 服务端 / 通义 rerank API）。

    端点复用 `LLM_BASE_URL` 下的 `/rerank`（A2 决策）：不额外引入一套 BaseURL 配置，
    少一个配置项就少一处"配错了但看起来在跑"的隐患。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def rerank(self, query: str, hits: list[SearchHit], top_n: int) -> list[SearchHit] | None:
        import httpx

        if not hits:
            return []
        base = (self.settings.rerank_base_url or self.settings.llm_base_url).rstrip("/")
        api_key = self.settings.rerank_api_key or self.settings.llm_api_key
        if not base or not api_key:
            logger.info("未配置 rerank 端点或 key，本次不重排")
            return None

        payload = {
            "model": self.settings.rerank_model,
            "query": query,
            # 只送候选上限条：重排接口的计费是按 pair 数算的，全量送等于白烧钱
            "documents": [hit.content[:2000] for hit in hits[: self.settings.rerank_candidates]],
            "top_n": min(top_n, len(hits)),
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.tool_timeout_sec) as client:
                response = await client.post(
                    f"{base}/rerank",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001 - 重排是旁路，失败只影响排序质量
            logger.warning("远程重排失败，保留原顺序：%s", exc)
            return None

        order = _parse_order(data)
        if order is None:
            logger.warning("重排返回格式无法解析，保留原顺序")
            return None
        return _reorder(hits, order)


def build_reranker(settings: Settings | None = None) -> BaseReranker:
    """按开关选实现。默认 `NoOpReranker`（PROJECT.md §二、[8]：默认全关）。"""
    target = settings or get_settings()
    if not is_enabled("rerank", target):
        return NoOpReranker()
    return RemoteReranker(target)


def _parse_order(data: object) -> list[int] | None:
    """从常见响应格式里提取下标顺序（`results[].index` 或直接是下标数组）。"""
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if isinstance(results, list):
        order: list[int] = []
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                order.append(int(item["index"]))
        return order or None
    indexes = data.get("indexes")
    if isinstance(indexes, list) and all(isinstance(i, int) for i in indexes):
        return [int(i) for i in indexes]
    return None


def _reorder(hits: list[SearchHit], order: list[int]) -> list[SearchHit]:
    """按重排结果重排；越界下标忽略（外部接口返回什么都可能）。"""
    seen: list[SearchHit] = []
    for index in order:
        if 0 <= index < len(hits):
            seen.append(hits[index])
    # 重排没覆盖到的候选按原顺序补在后面：宁可多留，不要静默丢证据
    for hit in hits:
        if hit not in seen:
            seen.append(hit)
    return seen
