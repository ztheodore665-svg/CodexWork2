"""视觉模型封装（W1-2 用）：给照片生成 caption 与 ai_tags。

**为什么这一层必须存在**：照片本身没有文本，检索系统只能检索文本。
所以必须先让模型把"这张照片拍了什么"写成一句话（caption）和几个结构化标签（ai_tags），
否则阶段 0 建好的向量检索对照片完全无效（设计文档 §13.1 的头号风险）。

provider：
  - `openai`：OpenAI 兼容的多模态 `/chat/completions`，把缩略图以 base64 data URI 传进去
  - `mock`  ：本地规则生成（用照片的时间/地点线索拼出一句描述 + 关键词标签）

mock 的存在有两个理由：
  1. 阶段 1 还没有导入链路（W6-1），库里没有真实图片字节，真跑 VLM 无从谈起
  2. 单测要能不花钱验证"批处理 → 写回 Photo → 入索引"整条链路
它的输出**不能**作为效果评估依据。
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VisionInput:
    """给视觉模型的输入（刻意不依赖 ORM，便于单测与后续换成真实图片源）。"""

    #: 本地缩略图路径（阶段 1 的演示数据可能为空）
    thumb_path: str | None = None
    #: 拍摄时间（当地时间字符串，如 2025-10-03 09:12）
    taken_at_text: str = ""
    #: 地点线索：由 EXIF / 逆地理 / 用户备注得来，仅作提示用
    place_hint: str = ""
    city: str = ""
    exif: dict = field(default_factory=dict)


@dataclass
class PhotoDescription:
    caption: str
    ai_tags: list[str] = field(default_factory=list)
    cost_cents: float = 0.0


class VisionError(AppError):
    user_message = "图片理解失败，请稍后重试"


class BaseVision(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def model_version(self) -> str:
        """模型版本：会作为 IngestJob.ai_model 参与幂等键，换版本即触发重跑。"""

    @abstractmethod
    async def describe(self, payload: VisionInput) -> PhotoDescription: ...


class MockVision(BaseVision):
    """本地规则生成（确定性，零成本）。

    它做的事：用时间 + 地点线索拼一句通顺的描述，再用关键词表映射出 ai_tags。
    足以验证"照片 → 文本 → 入库"这条链路是否通畅。
    """

    #: 极简关键词 → 标签映射（正式版在阶段 7 换成 semantic_tags.py 词表 + LLM（yaml 快照已于阶段 16 删除））
    _KEYWORD_TAGS = {
        "古镇": ["古镇", "水乡"],
        "乌镇": ["古镇", "水乡", "夜景"],
        "西塘": ["古镇", "水乡"],
        "西湖": ["自然风光", "湖泊"],
        "灵隐": ["寺庙", "人文历史"],
        "石窟": ["人文历史", "古迹"],
        "博物": ["博物馆", "人文历史"],
        "老君山": ["自然风光", "山岳"],
        "天门山": ["自然风光", "山岳"],
        "熊猫": ["动物", "亲子"],
        "海": ["海岛", "自然风光"],
        "外滩": ["都市风光", "夜景"],
        "陆家嘴": ["都市风光"],
        "巷": ["美食", "休闲度假"],
        "兵马俑": ["人文历史", "古迹"],
        "鼓浪屿": ["海岛", "人文历史"],
        "园林": ["园林", "人文历史"],
        "青海湖": ["自然风光", "湖泊"],
        "冰雪": ["冰雪", "冬季"],
    }

    @property
    def provider(self) -> str:
        return "mock"

    @property
    def model_version(self) -> str:
        return "mock-vision@v1"

    async def describe(self, payload: VisionInput) -> PhotoDescription:
        where = payload.place_hint or payload.city or "途中"
        when = payload.taken_at_text or "某个时刻"
        caption = f"这张照片拍摄于 {when}，地点在{where}。"
        if payload.city and payload.place_hint and payload.city != payload.place_hint:
            caption += f"（{payload.city}·{payload.place_hint}）"
        caption += "画面记录了当时的场景与同行的人，属于该行程中的一帧。"

        text = f"{payload.place_hint}{payload.city}"
        tags: list[str] = []
        # 长关键词优先：避免"青海湖"被更短的"海"先匹配成"海岛"
        for keyword in sorted(self._KEYWORD_TAGS, key=len, reverse=True):
            if keyword in text:
                tags.extend(self._KEYWORD_TAGS[keyword])
        # 去重后最多保留 3 个，避免标签泛滥
        tags = list(dict.fromkeys(tags))[:3] or ["日常"]
        return PhotoDescription(caption=caption, ai_tags=tags, cost_cents=0.0)


class OpenAICompatVision(BaseVision):
    """OpenAI 兼容协议的多模态模型（qwen-vl / gpt-4o-mini 等）。

    缩略图以 base64 data URI 内联发送——符合"原图永不上云"的边界：
    上行的只是本地生成的缩略图，且可通过配置关闭。
    """

    _PROMPT = (
        "你是旅行照片标注助手。请用简体中文写一句不超过 40 字的画面描述，"
        "并给出 1~3 个主题标签（如 古镇 / 博物馆 / 自然风光 / 美食 / 夜景）。\n"
        '只输出 JSON：{"caption": "...", "tags": ["..."]}\n'
        "时间线索：{when}\n地点线索：{where}"
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._base_url = settings.resolved_vision_base_url.rstrip("/")
        self._api_key = settings.vision_api_key.strip() or settings.llm_api_key.strip()

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model_version(self) -> str:
        return self.settings.vision_model_version

    async def describe(self, payload: VisionInput) -> PhotoDescription:
        if not payload.thumb_path:
            raise VisionError("缺少本地缩略图路径（阶段 1 尚无导入链路 W6-1），无法调用视觉模型")
        path = Path(payload.thumb_path)
        if not path.exists():
            raise VisionError(f"缩略图不存在：{path}")

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        data_uri = f"data:{mime};base64,{encoded}"

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": self._PROMPT.format(
                            when=payload.taken_at_text or "未知",
                            where=payload.place_hint or payload.city or "未知",
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self.settings.vision_timeout_sec,
        ) as client:
            resp = await client.post(
                "/chat/completions",
                json={
                    "model": self.settings.vision_model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
            )
        if resp.status_code != 200:
            raise VisionError(f"视觉模型返回 {resp.status_code}: {resp.text[:200]}")

        content = resp.json()["choices"][0]["message"]["content"] or ""
        return _parse_description(content, resp.json().get("usage") or {})


def _parse_description(content: str, usage: dict) -> PhotoDescription:
    """解析模型输出（允许带代码块围栏）。"""
    import json
    import re

    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 模型没按格式输出时，退化成"整段当描述、无标签"，不要直接判任务失败
        return PhotoDescription(caption=content.strip()[:200], ai_tags=[], cost_cents=0.0)

    tags = data.get("tags") or []
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    # 视觉模型价格表里通常没有，按 LLM 量级粗估（元/百万 token）
    cost = (prompt_tokens * 12.0 + completion_tokens * 12.0) / 1_000_000 * 100
    return PhotoDescription(
        caption=str(data.get("caption", "")).strip(),
        ai_tags=[str(t).strip() for t in tags if str(t).strip()],
        cost_cents=round(cost, 4),
    )


def build_vision(settings: Settings | None = None) -> BaseVision:
    """工厂：auto = 有 key 走真实 VLM，否则走本地 mock。"""
    settings = settings or get_settings()
    provider = settings.vision_provider.lower()
    if provider == "auto":
        provider = "openai" if settings.vision_key_present else "mock"

    if provider == "openai":
        return OpenAICompatVision(settings)
    if provider == "mock":
        logger.warning("VISION_PROVIDER=mock：caption 由本地规则生成，仅用于验证链路")
        return MockVision(settings)
    raise VisionError(f"未知的 vision provider: {settings.vision_provider}")
