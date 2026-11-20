"""search_poi：查地点（阶段 3 W3-2）。

出口条件要求**返回高德 POI ID**。这条看似只是"多返回一个字段"，实际上是设计上的关键：
POI ID 是工具证据的 `source_id`，也就是回答里 `[n]` 引用指向的目标。
没有它，工具查到的事实就**无法被引用**，引用率 100% 的红线会直接破掉。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.cache import TTL_POI_SEC
from app.services.map_service import MapService
from app.tools.base import BaseTool, ToolContext


class SearchPoiInput(BaseModel):
    """入参用 Pydantic 锁死：LLM 填出来的参数必须在这里被拦住。

    `frozen=True` 是因为它会被当作缓存 key 的一部分 —— 可变对象参与哈希是隐患。
    """

    model_config = ConfigDict(frozen=True)

    keyword: str = Field(description="要查询的地点名或类别，如「乌镇」「停车场」")
    city: str = Field(default="", description="限定城市，可为空")
    limit: int = Field(default=5, ge=1, le=10, description="最多返回几条")


class SearchPoiTool(BaseTool[SearchPoiInput]):
    """查地点。缓存 7 天：地点信息稳定，长缓存是安全的。"""

    name = "search_poi"
    description = "按关键词查询地点/景点/设施，返回名称、地址与 POI ID（用于地图高亮与引用）"
    input_model = SearchPoiInput
    cache_namespace = "poi"
    cache_ttl_sec = TTL_POI_SEC

    def __init__(self, service: MapService, context: ToolContext | None = None) -> None:
        super().__init__(context)
        self.service = service

    async def _run(self, validated: SearchPoiInput) -> dict:
        items = await self.service.search_poi(
            validated.keyword, city=validated.city, limit=validated.limit
        )
        return {
            # 一句话事实描述：模型只能引用它读到的文字，所以这里必须写成自然语言句子
            "text": "；".join(item.as_evidence_text() for item in items),
            # 每条 POI 各带一份 text：节点会把它们拆成独立证据（引用粒度 = 一个地点），
            # 如果只给合并后的整串，"引用 [3]"到底指向哪个地点就说不清了。
            "items": [{**item.to_dict(), "text": item.as_evidence_text()} for item in items],
            "count": len(items),
            "keyword": validated.keyword,
            "city": validated.city,
        }
