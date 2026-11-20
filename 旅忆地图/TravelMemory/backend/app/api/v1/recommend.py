"""W7-2 后端部分：**群体语义轨迹推荐 + k-匿名门禁**接口。

## 为什么未达标时返回 200 + `tag=None`，而不是 404 / 422

k-匿名门禁的结论是"**没有结论**"，不是"请求非法"。

- 404 会让前端把它当成"接口不存在/能力没开"来处理，而真正的能力开关另有语义（见下）；
- 422 会让它被当成参数错误去重试或弹红色报错，而正确行为是在页面上展示
  "数据还不够，多几个人走过这条线就能给出推荐了"；
- 用 404 表达"没有数据"还有一个隐蔽问题：它和"开关关闭"撞了码，
  前端没法区分"功能没开"和"数据不足"，而这是两种完全不同的运营状态。

所以：`tag=None` + `message` + `k`，状态码一律 200。门禁阈值回显在 `k` 里，
前端不需要猜。

## 为什么开关关闭时返回 404 而不是 403

PROJECT.md §二、[8] 要求阶段旁路开关默认全关。未启用的能力对外应当表现为
**不存在**（404），而不是"存在但你没权限"（403）—— 403 会告诉外部这里确有此功能，
等于把未来的攻击面先画在地图上。这与 `api/v1/privacy.py` 的处理完全一致。

## 为什么序列里的非法标签是"静默丢弃"而不是 422

匿名上报里混进未知标签是常态（新词表还没下发、旧版本客户端、上游抽取失败）。
为了一个脏标签拒绝整条上报，丢掉的是一条本可以贡献给匿名集的数据。
**但过滤完什么都不剩时必须 422**：空序列既没法推荐也没法学，那是请求非法，
与"数据不足"（200 + `tag=None`）是两回事。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import ensure_flag
from app.schemas.semantic import (
    SemanticContributeResponse,
    SemanticRecommendResponse,
    SemanticSequenceRequest,
)
from app.services.semantic_trajectory import (
    K_ANONYMITY,
    build_aggregator,
    truncate_sequence,
    validate_sequence,
)
from app.utils.semantic_tags import SEMANTIC_TAG_VERSION

router = APIRouter(prefix="/recommend", tags=["recommend"])

#: 未达标时给人类（经由前端）看的说明。**不含任何数字以外的内部信息**：
#: 不能说"还差 2 条"——那等于把 `support` 泄露出去了，外部能靠它数人头。
NO_PATTERN_MESSAGE = f"暂无足够匿名模式支撑（需要至少 {K_ANONYMITY} 次匿名观测）"

#: 达标时的说明。刻意与 `NO_PATTERN_MESSAGE` 保持同样的句式长度，
#: 前端不用为两种状态写两套布局。
PATTERN_FOUND_MESSAGE = "已匹配到满足 k-匿名门禁的群体模式"


def _checked_tags(req: SemanticSequenceRequest) -> tuple[str, ...]:
    """归一化入参序列：静默过滤非法标签；**过滤后为空 → 422**。"""
    tags = validate_sequence(req.sequence)
    if not tags:
        raise HTTPException(status_code=422, detail="序列中没有可识别的语义标签")
    return tags


@router.post("/semantic", summary="群体语义轨迹推荐", response_model=SemanticRecommendResponse)
async def recommend_semantic(req: SemanticSequenceRequest) -> SemanticRecommendResponse:
    """根据历史语义序列，推荐"下一步最可能去哪类地方"。

    只查**已发布**（`support >= k`）的模式 —— 见模块 docstring 与
    `app/services/semantic_trajectory.py` 里 `recommend()` 的红线说明。
    """
    ensure_flag("semantic")

    tags = _checked_tags(req)
    # 查询侧也要截断：长历史本身就是指纹，且模式库里的前缀最长也只有 6 步，
    # 不截断就永远查不到东西（而不是"查到不该查的"）。
    hit = build_aggregator().recommend(truncate_sequence(tags))

    if hit is None:
        return SemanticRecommendResponse(
            tag=None,
            confidence=0.0,
            avg_score=None,
            message=NO_PATTERN_MESSAGE,
            k=K_ANONYMITY,
            version=SEMANTIC_TAG_VERSION,
        )
    return SemanticRecommendResponse(
        tag=hit.tag,
        confidence=hit.confidence,
        avg_score=hit.avg_score,
        message=PATTERN_FOUND_MESSAGE,
        k=K_ANONYMITY,
        version=SEMANTIC_TAG_VERSION,
    )


@router.post(
    "/semantic/contribute",
    summary="上报一条匿名语义序列",
    response_model=SemanticContributeResponse,
)
async def contribute_semantic(req: SemanticSequenceRequest) -> SemanticContributeResponse:
    """客户端本地抽好语义序列后，只把**这条序列**上报给逻辑聚合器。

    注意上报的是原始语义序列，不是统计量：聚合器自己去重（`user_key`）、
    自己过滤低分、自己统计 `support`。让客户端先算一遍总数再上传，
    等于把聚合逻辑复制一份到不可信端，服务端无法校验。

    上报内容里**没有任何地点名、坐标或时间**：它们在上一步的
    `extract_sequences()` 里就已经被丢掉了。
    """
    ensure_flag("semantic")

    tags = _checked_tags(req)
    aggregator = build_aggregator()
    accepted = aggregator.submit(tags, score=req.score, user_key=req.user_key)
    stats = aggregator.stats()

    return SemanticContributeResponse(
        accepted=accepted,
        patterns=stats["patterns"],
        published=stats["published"],
    )
