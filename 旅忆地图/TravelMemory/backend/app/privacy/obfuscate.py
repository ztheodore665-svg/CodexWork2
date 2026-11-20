"""基于拉普拉斯机制的坐标模糊：采样、米↔度换算全部是**纯函数**，零 IO。

## 一句话交代位置

这里是 W6-4 的**后端部分**：服务端拿到单点坐标，加噪后吐回加噪坐标 + 承诺半径，
由前端写进加密信物。为什么这么做（而不是前端自己模糊）写在
`app/privacy/__init__.py` 的包 docstring 里，本文件只讲算法本身。

## 三个容易被改坏的点

1. **`radius` 是承诺半径，不是本次实际偏移量。**
   它是常量（`MODE_RADIUS_M[mode]`），表示"我保证撒下了这个尺度的噪声"；
   前端若拿它当"这次实际偏了多少米"用，语义就错了 —— 实际偏移是随机的，
   同一个点调两次结果不同，期望值是 `radius / epsilon`。
2. **经度换算的分母必须 clamp。**
   `dlng = noise / (111_320 · cos(lat))`，而 `cos(±90°) = 0`：靠近极点时 1 米
   噪声能偏出几十度经度，lat 恰好为 ±90 时更会直接除零。这里夹到 `1e-6`。
3. **经度回绕用取模，不用加减。**
   经度是环形量，`+360` / `-360` 的写法叠加几次噪声就会漂出值域；
   取模一次到位，且天然给出半开区间 `[-180, 180)`。
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.schemas.privacy import ObfuscationMode

__all__ = [
    "MODE_RADIUS_M",
    "InvalidObfuscationInput",
    "ObfuscatedPoint",
    "ObfuscationMode",
    "PrivacyError",
    "laplace_noise_meters",
    "obfuscate_location",
    "obfuscate_many",
]

#: 每种模式对外**承诺**的模糊半径（米）。`exact` = 0 表示"一点都不偏移"。
#:
#: 这三个数是**产品语义**，不是调出来的超参：
#:   - `approx` 500 米 ≈ 一个步行街区的量级，"就在这附近"说得通，但定位不到具体门牌；
#:   - `city` 5000 米 ≈ 一个城区的量级，通常只剩"哪个区 / 哪个县城"的信息；
#:   - `exact` 0 米只用于用户**明确勾选**"分享精确位置"的场景（如自家备份同步），
#:     走这条路时应当在产品上把它当成一次显式授权。
MODE_RADIUS_M: dict[str, float] = {"exact": 0.0, "approx": 500.0, "city": 5000.0}

#: 米 → 纬度度数：经线上一度平均约 111_320 米（子午线周长 40 008 000 米 / 360）。
#: 用平均值而不是逐纬度的真实弧长：后面还有几百到几千米级的噪声，
#: 椭球修正（<0.5%）相比之下可以忽略，多出来的精度只会让读者误以为这里很精确。
_METERS_PER_DEGREE = 111_320.0

#: `cos(lat)` 的**下限**。lat 趋近 ±90°（极点）时 cos→0，拿它当除数会让经度度数
#: 发疯 —— 1 米噪声偏出几十度，lat 恰好为 ±90 时直接 ZeroDivisionError。
#: 夹到 1e-6 之后最坏情况是把点甩到近乎随机的经度上，
#: 而结果随后会被归一化回 `[-180, 180)`，既不会溢出也不会污染调用方。
_COS_LAT_FLOOR = 1e-6

#: epsilon 允许的最大值。超过这个数 `b = radius / epsilon` 小到噪声失去意义，
#: 与其让它静默地"看起来像精确坐标"，不如拦下来让调用方说清楚意图。
_EPSILON_MAX = 10.0


class PrivacyError(Exception):
    """隐私旁路内部的异常基类。

    为什么不全用 `app.core.exceptions.AppError`：那里挂的是"业务链路失败"
    （检索挂了、LLM 挂了）的语义，而这里是**调用方传错了参数**，处理方式不一样 ——
    后者在 API 层是 400，而不是降级。层级独立也顺带守住了
    "privacy 包只依赖 core / schemas / utils"这条线。
    """


class InvalidObfuscationInput(PrivacyError):
    """入参不合法：坐标越界、非有限数、epsilon 非正或过大、mode 未知。"""


@dataclass(frozen=True)
class ObfuscatedPoint:
    """一个被模糊过的点（frozen dataclass，纯值语义，可直接进 JSON / 缓存 key）。

    Attributes:
        lat: 加噪后的 **WGS84** 纬度（PROJECT.md §二、[2]：WGS84 存储 / GCJ-02 展示）
        lng: 加噪后的 **WGS84** 经度，已归一化到 `[-180, 180)`
        radius: **承诺半径**（米），即 `MODE_RADIUS_M[mode]`，
            **不是本次实际偏移量**，详见模块 docstring 第 1 条
        mode: 本次使用的模糊模式
        epsilon: 本次实际使用的隐私预算（`radius / epsilon` 才是拉普拉斯尺度 b）
    """

    lat: float
    lng: float
    radius: float
    mode: str
    epsilon: float


def laplace_noise_meters(b: float, rng: random.Random) -> float:
    """从尺度为 `b` 的零均值拉普拉斯分布里抽一个噪声值（单位：米）。

    逆变换采样（inverse transform sampling）：

    ```
    u ~ Uniform(-0.5, 0.5)
    noise = -b · sign(u) · ln(1 - 2·|u|)
    ```

    为什么这样写：u 关于 0 对称时，`sign(u)` 给出正负两翼、`ln(1-2|u|) ≤ 0`
    给出幅度，天然左右对称，不需要分支。和常见的 `-b·ln(u), u~U(0,1)` 等价，
    只是把对称性显式写在公式里便于复查。

    分布性质：均值 0、方差 `2b²`、`E|noise| = b`。
    最后一条是"期望偏移量 ≈ radius / epsilon"的来源，
    也是单测里"大 epsilon 的平均偏移更小"能成立的理论依据。

    `rng` 必须由调用方提供：焦点在可控性上 —— 这里不偷偷使用全局 random。
    """
    if not math.isfinite(b) or b < 0:
        raise InvalidObfuscationInput(f"拉普拉斯尺度 b 必须是非负有限数，收到 {b}")
    u = rng.uniform(-0.5, 0.5)
    return -b * math.copysign(1.0, u) * math.log(1.0 - 2.0 * abs(u))


def _reject_unless_finite(name: str, value: float) -> None:
    """非有限数（NaN / inf）一律拒绝。

    NaN 绝不能静默通过：`nan + 噪声 = nan`，它会一路 propagates 到数据库，
    最后变成"某条记忆的坐标是 NaN"，排查成本极高 —— 所以在这里炸出来。
    """
    if not math.isfinite(value):
        raise InvalidObfuscationInput(f"{name} 必须是有限数，收到 {value}")


def _validate(lat: float, lng: float, mode: str, epsilon: float) -> None:
    """入参校验：任何不合法都当作**调用方的 bug** 抛 `InvalidObfuscationInput`。

    为什么要显式校验而不是让错误自然发生：`MODE_RADIUS_M[mode]` 遇到未知 mode
    会抛没有上下文的 `KeyError`，epsilon 为 0 会让 `b = radius / 0` 变成
    `ZeroDivisionError` —— 两者都以崩溃收场且信息难懂，不如一次性说清楚。

    注意 `-0.0` 是被接受的（`-0.0 <= x <= 90` 成立），它等价于 0，不影响语义。
    """
    _reject_unless_finite("lat", lat)
    _reject_unless_finite("lng", lng)
    _reject_unless_finite("epsilon", epsilon)

    if not -90.0 <= lat <= 90.0:
        raise InvalidObfuscationInput(f"lat 必须在 [-90, 90]，收到 {lat}")
    if not -180.0 <= lng <= 180.0:
        raise InvalidObfuscationInput(f"lng 必须在 [-180, 180]，收到 {lng}")
    if mode not in MODE_RADIUS_M:
        raise InvalidObfuscationInput(
            f"未知的模糊模式 {mode!r}（可用: {', '.join(MODE_RADIUS_M)}）"
        )
    if not 0.0 < epsilon <= _EPSILON_MAX:
        raise InvalidObfuscationInput(f"epsilon 必须在 (0, {_EPSILON_MAX}]，收到 {epsilon}")


def _wrap_longitude(lng: float) -> float:
    """经度归一化到半开区间 `[-180, 180)`。

    为什么半开：-180 与 180 是同一条经线，允许两种写法会让同一地点出现两个 key，
    缓存 / 去重 / 聚类全都要跟着退化。取模给出唯一的规范写法。
    """
    wrapped = math.fmod(lng + 180.0, 360.0)
    if wrapped < 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def obfuscate_location(
    lat: float,
    lng: float,
    mode: str = "approx",
    epsilon: float = 1.0,
    *,
    rng: random.Random | None = None,
) -> ObfuscatedPoint:
    """给单个点打上拉普拉斯噪声，返回加噪点 + 承诺半径。

    Args:
        lat: 原始 WGS84 纬度
        lng: 原始 WGS84 经度
        mode: `exact` / `approx` / `city`，决定承诺半径（见 `MODE_RADIUS_M`）
        epsilon: 隐私预算，越大噪声越小；`b = radius / epsilon` 是拉普拉斯尺度
        rng: 随机数发生器。**不传则新建 `random.Random()`（取自操作系统随机源）**，
            输出不可复现；想要可复现（单测、回放）必须自己传固定种子的 `rng` ——
            同一种子 + 同样入参 → 输出逐位一致。

    Returns:
        `ObfuscatedPoint`，其中 `radius` 是**承诺半径**（`MODE_RADIUS_M[mode]`），
        表示本次机制的噪声尺度，**不是"这个点这次实际偏了多少米"**。

    Raises:
        InvalidObfuscationInput: 坐标越界 / 非有限数 / epsilon 越界 / mode 未知
    """
    _validate(lat, lng, mode, epsilon)
    radius = MODE_RADIUS_M[mode]

    # exact 是"用户明确要求分享精确位置"：不采样、不加噪，一个随机数也不消耗。
    # 这样调用方复用同一个 rng 时，exact 点不会打乱后续点的噪声序列。
    if mode == "exact":
        return ObfuscatedPoint(lat=lat, lng=lng, radius=radius, mode=mode, epsilon=epsilon)

    generator = rng if rng is not None else random.Random()
    b = radius / epsilon
    d_lat = laplace_noise_meters(b, generator) / _METERS_PER_DEGREE
    d_lng = laplace_noise_meters(b, generator) / (
        _METERS_PER_DEGREE * max(math.cos(math.radians(lat)), _COS_LAT_FLOOR)
    )

    noisy_lat = min(90.0, max(-90.0, lat + d_lat))
    noisy_lng = _wrap_longitude(lng + d_lng)
    return ObfuscatedPoint(
        lat=noisy_lat, lng=noisy_lng, radius=radius, mode=mode, epsilon=epsilon
    )


def obfuscate_many(
    points: Iterable[Sequence[float]],
    mode: str = "approx",
    epsilon: float = 1.0,
    *,
    rng: random.Random | None = None,
) -> list[ObfuscatedPoint]:
    """批量模糊：逐个点独立走 `obfuscate_location`。

    **每个点独立采样，不做整条轨迹的联合扰动** —— 这一点必须讲清楚：

    联合扰动需要服务端先拿到整条轨迹，才能计算"这些点彼此之间的相对关系"；
    而本接口的设计前提恰恰是**服务端只见单点**。独立采样意味着：

    - 服务端**无法**通过多次调用反推轨迹点之间的相对关系，
      每次都是同分布的独立抽样，看不到"第 3 个点在第 2 个点的东北方 30 米"；
    - 代价是**隐私预算逐点累加**：n 个点用掉 n·epsilon。
      这是设计上的诚实取舍 —— 宁可对外的预算清晰可算，也不要暗藏关联。

    Args:
        points: `(lat, lng)` 二元组序列
        mode / epsilon / rng: 同 `obfuscate_location`
    """
    generator = rng if rng is not None else random.Random()
    return [obfuscate_location(lat, lng, mode, epsilon, rng=generator) for lat, lng in points]
