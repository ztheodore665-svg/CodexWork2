"""隐私旁路（地理模糊）的入参 / 出参契约（与 ORM 严格分离）。

`ObfuscationMode` 这个 Literal 定义在这里而不是 `app/privacy/obfuscate.py`，
是为了让依赖方向保持干净：`privacy` 包允许 import `schemas`（它是最底下的契约层），
反过来则会让契约层依赖实现层。`obfuscate` 模块会 re-export 它，
所以调用方从两侧 import 拿到的完全是同一个类型。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 三种对外暴露的模糊粒度。语义见 `MODE_RADIUS_M`（单位：米）。
ObfuscationMode = Literal["exact", "approx", "city"]

#: PROJECT.md §二、[2] 是红线：**WGS84 存储 / GCJ-02 展示**。
#: 本接口全程工作在 WGS84（存储口径）上 —— 模糊只是"往源头再加一点不确定性"，
#: 不该顺手把坐标系也换了。展示前由 `app.utils.coords.wgs84_to_gcj02()` 转换。
#: 响应里显式带上这个字段，是为了让前端没法"猜"：一旦忘了转换，几百米的偏移
#: 不会报错，只会安静地错下去。
COORD_SYSTEM_WGS84 = "WGS84"


class ObfuscateRequest(BaseModel):
    """一次地理模糊请求。

    范围约束写在 `Field` 里而不是 handler 里：非法入参由 FastAPI 统一转成 422，
    handler 只负责业务 —— 手写校验会把"契约"和"流程"搅在一起，
    而 Exception 语义还容易写错（400 vs 422）。

    `epsilon` 用 `gt=0` 而不是 `ge=0`：0 不是"很小的预算"，它是除零。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    lat: float = Field(ge=-90.0, le=90.0, description="原始 WGS84 纬度")
    lng: float = Field(ge=-180.0, le=180.0, description="原始 WGS84 经度")
    mode: ObfuscationMode = Field(default="approx", description="模糊粒度")
    epsilon: float = Field(default=1.0, gt=0.0, le=10.0, description="隐私预算，越大越接近原坐标")


class ObfuscateResponse(BaseModel):
    """一次地理模糊的结果。

    `radius` 是**承诺半径**（`MODE_RADIUS_M[mode]`），不是本次实际偏移距离 ——
    语义见 `app/privacy/obfuscate.py` 模块 docstring。
    """

    lat: float = Field(description="加噪后的 WGS84 纬度")
    lng: float = Field(description="加噪后的 WGS84 经度，已归一化到 [-180, 180)")
    radius: float = Field(description="承诺模糊半径（米），不是本次实际偏移量")
    mode: str = Field(description="实际使用的模糊模式")
    epsilon: float = Field(description="实际使用的隐私预算")
    coord_system: str = Field(default=COORD_SYSTEM_WGS84, description="坐标系，恒为 WGS84")
