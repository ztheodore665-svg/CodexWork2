"""Embedding 客户端。

两个实现：
  1. `openai`  —— OpenAI 兼容协议 `POST /embeddings`。国产平台（阿里云百炼兼容模式、
     SiliconFlow、火山方舟）与自部署的 bge-m3 server 都能直接对接。
  2. `hashing` —— 本地确定性哈希向量。**只用于没有 API Key 时跑通工程链路与单测**，
     它只捕捉字面/字级重合，语义能力远低于真实模型，**禁止用于任何评测结论**。

为什么必须抽象出这一层：换模型时只需改 .env，然后 `make reindex`；业务代码零改动，
并且 `embedding_model` 会随向量落库，脏向量可被启动自检拦下（设计文档 §4.4）。
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingError

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass
class BaseEmbedder(ABC):
    """向量化接口。

    子类必须保证：`embed` 返回的每条向量长度为 `dim`，否则后续入库会因维度不符报错。
    """

    settings: Settings
    #: 记录已向量化的文本数量，便于估算成本与观察批处理进度
    embedded_count: int = field(default=0, init=False)

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def model_version(self) -> str: ...

    @property
    def dim(self) -> int:
        return self.settings.embedding_dim

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        """单条向量化（查询时使用）。"""
        vectors = await self.embed([text])
        return vectors[0]

    def _validate_dim(self, vector: list[float]) -> list[float]:
        """维度不符合配置时立刻失败，避免脏向量入库后污染整个库。"""
        if len(vector) != self.dim:
            raise EmbeddingError(
                f"向量维度不符：模型 {self.model} 返回 {len(vector)} 维，"
                f"配置 EMBEDDING_DIM={self.dim}。请修正配置或换模型后执行 make reindex"
            )
        return vector


class OpenAICompatEmbedder(BaseEmbedder):
    """走 OpenAI 兼容协议的 embedding。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base_url = settings.resolved_embedding_base_url.rstrip("/")
        # 没单独配 embedding key 时复用 LLM key（多数平台同一个 key）
        self._api_key = settings.embedding_api_key.strip() or settings.llm_api_key.strip()

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    @property
    def model_version(self) -> str:
        return self.settings.embedding_model_version

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float]] = []
        size = max(1, self.settings.embedding_batch_size)
        # 分批发送：单批文本过长会被服务端拒绝，也方便失败时只重跑该批
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self.settings.llm_timeout_sec,
        ) as client:
            for start in range(0, len(texts), size):
                batch = texts[start : start + size]
                payload = {"model": self.model, "input": batch}
                resp = await client.post("/embeddings", json=payload)
                if resp.status_code != 200:
                    raise EmbeddingError(
                        f"embedding 请求失败 status={resp.status_code} body={resp.text[:300]}"
                    )
                data = resp.json().get("data", [])
                if len(data) != len(batch):
                    raise EmbeddingError(
                        f"embedding 返回条数不符：期望 {len(batch)}，实际 {len(data)}"
                    )
                # data 可能乱序，必须按 index 归位
                ordered: dict[int, list[float]] = {
                    item["index"]: item["embedding"] for item in data
                }
                for i in range(len(batch)):
                    results.append(self._validate_dim(ordered[i]))
                self.embedded_count += len(batch)
        return results


class HashingEmbedder(BaseEmbedder):
    """本地哈希向量（hashing trick）。

    做法：把文本拆成「字 + 相邻二字 + 英文数字词」三类特征，用 blake2b 稳定映射到 dim 维，
    再做 L2 归一化，于是余弦相似度 = 点积。

    特点：完全确定性（同文本永远同向量）、零成本、零网络；
    缺陷：只认字面相似，不理解同义/上下位关系。仅用于离线测试。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def provider(self) -> str:
        return "hashing"

    @property
    def model(self) -> str:
        return f"hashing-{self.dim}"

    @property
    def model_version(self) -> str:
        return f"hashing-{self.dim}@v1"

    @staticmethod
    def _features(text: str) -> set[str]:
        chars = [c for c in text if not c.isspace()]
        feats: set[str] = set(chars)
        # 相邻二字：中文里近似词，效果好于单字
        feats.update(a + b for a, b in zip(chars, chars[1:], strict=False))
        feats.update(w.lower() for w in _WORD_RE.findall(text))
        return feats

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
                h = int.from_bytes(digest, "big")
                idx = h % self.dim
                # 高位比特决定符号，降低不同特征哈希碰撞带来的偏移
                signed = 1.0 if ((h >> 63) & 1) == 0 else -1.0
                vec[idx] += signed
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(self._validate_dim(vec))
            self.embedded_count += 1
        return vectors


def build_embedder(settings: Settings | None = None) -> BaseEmbedder:
    """工厂：按配置选择 embedding 实现。

    auto 策略：有 key → 真实模型；无 key → hashing 并在日志里明确标注降级。
    """
    from app.core.logging import get_logger

    settings = settings or get_settings()
    provider = settings.embedding_provider.lower()

    if provider == "auto":
        provider = "openai" if settings.embedding_key_present else "hashing"

    if provider == "openai":
        return OpenAICompatEmbedder(settings)
    if provider == "hashing":
        get_logger(__name__).warning(
            "EMBEDDING_PROVIDER=hashing：正在使用本地哈希向量，检索质量不可作为评测结论"
        )
        return HashingEmbedder(settings)

    raise EmbeddingError(f"未知的 embedding provider: {settings.embedding_provider}")
