"""群体语义轨迹推荐（W7-2）的入参 / 出参契约（与 ORM 严格分离）。

## 为什么常量在 `schemas/policy.py` 而不是 `services`

`MAX_SEQUENCE_STEPS` / `K_ANONYMITY` 的真相源在 `app/schemas/policy.py`。
它在阶段 16 之前住在 `services/semantic_trajectory.py`，schemas 反向 import 它 ——
虽然那个模块本身是零依赖纯函数，但**方向是错的**：契约层不该依赖实现层。
现在方向改成 `services → schemas`，`semantic_trajectory` 从这里取常量并继续
对外暴露同名符号，调用方无需改动。

在 schema 里另写一个 `6` / `5` 也不行：将来改门禁时会出现
"前端传 8 步、服务端只认后 6 步"或"响应里写 k=5、实际按 3 判"的静默分叉。

## 为什么响应里没有 `support`

设计文档 §9.2：**前端展示 confidence，不展示精确 support**。
`support` 就是参与人数，把它放进 JSON 等于把匿名集的大小公开出去 ——
外部只要盯一个前缀反复调用，就能用 support 的增量反推"又有几个人走了这条线"。
所以 `SemanticRecommendResponse` 从字段定义上就不给它留位置。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.policy import K_ANONYMITY, MAX_SEQUENCE_STEPS
from app.utils.semantic_tags import SEMANTIC_TAG_VERSION

#: 序列里允许的最大标签数。与 `MAX_SEQUENCE_STEPS` 同源，见模块 docstring。
MAX_SEQUENCE_LENGTH = MAX_SEQUENCE_STEPS


class SemanticSequenceRequest(BaseModel):
    """一次"语义序列"的上报 / 查询请求。

    范围约束写在 `Field` 里而不是 handler 里：非法入参由 FastAPI 统一转成 422，
    handler 只处理业务 —— 手写校验会把"契约"和"流程"搅在一起，还容易把 422 写成 400。

    `sequence` 用 `min_length=1`：空序列既没法推荐也没法学，属于**请求非法**（422），
    而不是"数据不足"（200 + `tag=None`）——两者语义完全不同，见 `api/v1/recommend.py`。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    sequence: list[str] = Field(
        min_length=1,
        max_length=MAX_SEQUENCE_LENGTH,
        description="语义标签序列（六类之一），已按时间先后排列；非法标签会被静默丢弃",
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="该行程的评分（0~5）；低于 MIN_AVG_SCORE 的不计入 avg_score，不影响 support",
    )
    user_key: str | None = Field(
        default=None,
        max_length=64,
        description="客户端本地生成的匿名标识，用于 unique 去重（同一人同一模式只贡献一次）",
    )


class SemanticRecommendResponse(BaseModel):
    """推荐结果。**k-匿名未达标时 `tag` 为 `None`，但状态码仍是 200**。

    `confidence` 是相对占比（0~1），**不是概率、更不是样本量**：
    它只能用来排序与展示"多确定"，不能反推有多少人参与了这条模式。

    `k` 回显门禁阈值：前端拿到 `tag=None` 时要能自己解释"为什么没有结果"，
    而不是去猜服务端心里那个数字。
    """

    tag: str | None = Field(description="推荐的下一站语义标签；k-匿名未达标时为 None")
    confidence: float = Field(ge=0.0, le=1.0, description="相对占比（0~1），不代表样本量")
    avg_score: float | None = Field(default=None, description="该模式的平均评分；无评分样本时为 None")
    message: str = Field(description="人类可读说明（未达标时说明原因）")
    k: int = Field(default=K_ANONYMITY, description="k-匿名门禁阈值（support >= k 才发布）")
    version: str = Field(default=SEMANTIC_TAG_VERSION, description="语义词表版本")


class SemanticContributeResponse(BaseModel):
    """一次匿名上报的回执。

    `accepted` 是**本次新增**的条目数（去重命中或序列太短时为 0），
    它同时也是"这次上报有没有真的推进某条模式跨过门禁"的信号 ——
    客户端可以据此决定还要不要再贡献。

    `patterns` / `published` 只回计数：回执的作用是确认"收到了"，
    不需要把模式内容带出去（那会绕过推荐侧的 `support` 屏蔽）。
    """

    accepted: int = Field(ge=0, description="本次上报新增的 (prefix, next_tag) 条目数")
    patterns: int = Field(ge=0, description="聚合器当前的模式总数")
    published: int = Field(ge=0, description="已达标（support >= k）的模式数")
