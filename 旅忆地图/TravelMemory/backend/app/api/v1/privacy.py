"""W6-4 后端部分：**拉普拉斯地理模糊**接口。

## 为什么这里是接收单点而不是整条轨迹

前端在打包加密信物之前，先把要写进信物的那个坐标单独 POST 过来。
服务端于是**每次只看到一个孤立的点**：既没有周围的时间上下文，
也看不到它前后的其它点 —— 这是整套设计里唯一能有效阻止"轨迹关联"的做法。

## 为什么开关关闭时返回 404 而不是 403

PROJECT.md §二、[8] 要求：阶段旁路开关默认全关。
未启用的能力对外应当表现为**不存在**（404），而不是"存在但你没权限"（403）。
403 会告诉外部"这里确有此功能"，等于把未来的攻击面先画在地图上。

## 为什么不在 handler 里手写参数校验

范围 / 类型 / 枚举的约束已经写在 `ObfuscateRequest` 的 `Field` 里，
由 FastAPI/pydantic 统一翻译成 422 —— handler 只处理**业务异常**：
这里只有 `PrivacyError` 一种，翻译成 400。
手写一遍校验会让"契约"和"流程"搅在一起，且很容易把 400 和 422 写反。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import ensure_flag
from app.privacy.obfuscate import PrivacyError, obfuscate_location
from app.schemas.privacy import ObfuscateRequest, ObfuscateResponse

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.post("/obfuscate", summary="单坐标的拉普拉斯地理模糊", response_model=ObfuscateResponse)
async def obfuscate(req: ObfuscateRequest) -> ObfuscateResponse:
    """把单个 WGS84 坐标加噪后返回，附带承诺半径。

    注意每次请求的噪声都是独立抽样：**同样的输入调用两次会得到两个不同的点**。
    这是刻意的 —— 若对同一坐标总是返回同一个结果，外部就能通过重放校准出真实位置。
    """
    # 开关未开 = 能力不存在（404，不是 403）。详见模块 docstring。
    ensure_flag("privacy")

    try:
        point = obfuscate_location(
            req.lat,
            req.lng,
            mode=req.mode,
            epsilon=req.epsilon,
        )
    except PrivacyError as exc:
        # pydantic 已经拦掉了绝大多数非法入参（422）；这里是契约之外的兜底：
        # 一旦将来有人改错 `MODE_RADIUS_M` 或放宽了 schema 的范围约束，
        # 错误会在这里变成可解读的 400，而不是 500 + 一段 KeyError 堆栈。
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ObfuscateResponse(
        lat=point.lat,
        lng=point.lng,
        radius=point.radius,
        mode=point.mode,
        epsilon=point.epsilon,
    )
