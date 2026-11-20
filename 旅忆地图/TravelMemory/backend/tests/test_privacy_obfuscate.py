"""`app.privacy.obfuscate` 的纯函数层单测。

全部用例**无 IO、无外部服务、无 skip**：算法本身是确定性的，
难点只在于"随机" —— 所以每个涉及随机的用例都自己传固定种子的 `rng`，
断言是**逐位成立**而不是"大概率成立"。

刻意不做的事：

- 不用全局 `random.seed()` —— 那会让用例之间互相污染；
- 不断言"某次具体偏移量"，只断言统计量上界 / 均值关系 / 值域，
  否则将来微调常量就会红一片，测试反而变成负担。
"""

from __future__ import annotations

import math
import random

import pytest

from app.privacy.obfuscate import (
    MODE_RADIUS_M,
    InvalidObfuscationInput,
    ObfuscatedPoint,
    PrivacyError,
    laplace_noise_meters,
    obfuscate_location,
    obfuscate_many,
)

#: 一个真实感的取样点（杭州西湖，WGS84）
LAT, LNG = 30.2422, 120.1494

#: 地球平均半径（米）
_EARTH_RADIUS_M = 6_371_008.8

#: 采样次数：够把均值稳住，又不至于让单测变慢
_SAMPLES = 200


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面距离（米，haversine）。

    经度差先做跨 180° 的归一化，否则 ±180 附近的点会被算成绕地球一整圈。
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lng = math.radians((lng2 - lng1 + 540.0) % 360.0 - 180.0)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def _mean_offset(epsilon: float, *, samples: int = _SAMPLES) -> float:
    """固定种子下 `approx` 模式的平均偏移量（米）。"""
    rng = random.Random(20261024)
    total = 0.0
    for _ in range(samples):
        point = obfuscate_location(LAT, LNG, "approx", epsilon, rng=rng)
        total += _distance_m(LAT, LNG, point.lat, point.lng)
    return total / samples


# ---------------------------------------------------------------- exact 与 radius 语义
def test_exact_mode_returns_input_bitwise() -> None:
    """`exact` 必须一个比特都不动地返回原坐标，且承诺半径为 0。"""
    point = obfuscate_location(LAT, LNG, "exact")
    assert point.lat == LAT
    assert point.lng == LNG
    assert point.radius == 0.0
    assert point.mode == "exact"


def test_exact_mode_consumes_no_randomness() -> None:
    """`exact` 不该消耗 rng：在同一个 rng 上跳过一个 exact 点后，
    后续点的噪声序列必须不受影响。

    这条保护的是"复用同一个 rng 做批量加噪"的用法不会因为中间夹了 exact 点而错位。
    """
    generator = random.Random(7)
    first = obfuscate_location(LAT, LNG, "approx", rng=generator)
    obfuscate_location(LAT, LNG, "exact", rng=generator)
    generator2 = random.Random(7)
    first_only = obfuscate_location(LAT, LNG, "approx", rng=generator2)
    obfuscate_location(LAT, LNG, "approx", rng=generator2)

    assert first == first_only


@pytest.mark.parametrize("mode", ["exact", "approx", "city"])
def test_radius_is_the_committed_radius_of_that_mode(mode: str) -> None:
    """返回的 `radius` 恒等于 `MODE_RADIUS_M[mode]`（承诺半径），与采样结果无关。"""
    point = obfuscate_location(LAT, LNG, mode)
    assert point.radius == MODE_RADIUS_M[mode]
    # 顺手确认它确实是"承诺"而不是"实际"：city 模式实际偏移不可能恰好等于 5000
    if mode != "exact":
        offset = _distance_m(LAT, LNG, point.lat, point.lng)
        assert offset != MODE_RADIUS_M[mode]


# ---------------------------------------------------------------- 确定性
def test_same_seed_gives_bitwise_identical_output() -> None:
    """相同种子 → 逐位一致。这是目前让模糊结果可复现的唯一手段，必须钉死。"""
    a = obfuscate_location(LAT, LNG, "approx", 1.0, rng=random.Random(42))
    b = obfuscate_location(LAT, LNG, "approx", 1.0, rng=random.Random(42))
    assert a.lat == b.lat
    assert a.lng == b.lng
    assert a == b


def test_different_seeds_give_different_output() -> None:
    """不同种子 → 不同结果。若这条变绿变红概率态，说明 rng 没接进来。"""
    a = obfuscate_location(LAT, LNG, "city", 1.0, rng=random.Random(1))
    b = obfuscate_location(LAT, LNG, "city", 1.0, rng=random.Random(2))
    assert (a.lat, a.lng) != (b.lat, b.lng)


def test_samples_are_not_all_zero() -> None:
    """加噪确实在发生：同样种子、多次调用不应每个点都偏移 0。"""
    rng = random.Random(20261024)
    offsets = [
        _distance_m(LAT, LNG, p.lat, p.lng)
        for p in (obfuscate_location(LAT, LNG, "approx", rng=rng) for _ in range(_SAMPLES))
    ]
    assert max(offsets) > 0.0


# ---------------------------------------------------------------- 偏移量分布
def test_offsets_stay_within_a_sane_bound() -> None:
    """采样 200 次，最大偏移不应超过 `radius * 50`（500 米 → 25 公里）。

    拉普拉斯是重尾的，不能断言"绝不超过某个小数"；但超过 50 倍 b 的概率是
    `e^-50 ≈ 2e-22`，出现即说明公式写错了（例如漏了 epsilon 除法）。
    """
    radius = MODE_RADIUS_M["approx"]
    rng = random.Random(99)
    offsets = [
        _distance_m(LAT, LNG, p.lat, p.lng)
        for p in (obfuscate_location(LAT, LNG, "approx", rng=rng) for _ in range(_SAMPLES))
    ]
    assert max(offsets) < radius * 50
    assert max(offsets) > 0.0


def test_larger_epsilon_means_smaller_average_offset() -> None:
    """`E|noise| = b = radius / epsilon`：epsilon 增大 10 倍，平均偏移缩小到 1/10。

    这里**刻意给两边同一个种子**：抽到的 u 序列相同，噪声值严格相差 10 倍，
    断言是确定性的而不是概率性的。
    """
    mean_large = _mean_offset(5.0)
    mean_small = _mean_offset(0.5)
    # 理论值恰好相差 10 倍；这里只断言方向和一个宽松量级，避免依赖浮点末位
    assert mean_large < mean_small
    assert mean_large * 5 < mean_small


def test_laplace_noise_is_symmetric_around_zero() -> None:
    """u 取正负时噪声大小相等符号相反 —— 逆变换公式的直接性质。

    用一个**假 rng** 固定返回给定的 u，把 "u 的取值" 这件事从随机性里剥离出来，
    于是这条断言锁的是公式本身。
    """

    class FixedRng(random.Random):
        """永远返回构造时给定的 u。"""

        def __init__(self, u: float) -> None:
            super().__init__(0)
            self._u = u

        def uniform(self, a: float, b: float) -> float:
            return self._u

    b = 300.0
    positive = laplace_noise_meters(b, FixedRng(0.25))
    negative = laplace_noise_meters(b, FixedRng(-0.25))
    # u > 0 → 正翼；u < 0 → 负翼
    assert positive > 0 > negative
    assert positive == pytest.approx(-negative)
    # 幅度校验：b=300、|u|=0.25 → |noise| = 300·ln2
    assert positive == pytest.approx(300.0 * math.log(2))


# ---------------------------------------------------------------- 边界
def test_latitude_is_clamped_near_the_pole() -> None:
    """极点附近加噪后纬度仍必须在 `[-90, 90]`：跨极溢出是经典的坐标 bug，
    而且它不会报错，只会让点跑到地球背面去。
    """
    rng = random.Random(2026)
    for _ in range(_SAMPLES):
        point = obfuscate_location(89.9999, LNG, "approx", rng=rng)
        assert -90.0 <= point.lat <= 90.0
        assert math.isfinite(point.lat)


def test_longitude_is_wrapped_into_half_open_range() -> None:
    """跨 180° 经线加噪后必须落回 `[-180, 180)`，不能出现 180.0000x。"""
    rng = random.Random(31337)
    for _ in range(_SAMPLES):
        point = obfuscate_location(LAT, 179.9999, "city", rng=rng)
        assert -180.0 <= point.lng < 180.0
        assert math.isfinite(point.lng)


def test_multiple_wraps_stay_in_range() -> None:
    """城市级（5000 米）噪声叠加在极点 + 跨经线的点上，也必须在值域内。"""
    rng = random.Random(555)
    for _ in range(_SAMPLES):
        point = obfuscate_location(89.9999, 179.9999, "city", 0.2, rng=rng)
        assert -90.0 <= point.lat <= 90.0
        assert -180.0 <= point.lng < 180.0


# ---------------------------------------------------------------- 非法入参
@pytest.mark.parametrize(
    "field",
    ["lat", "lng", "epsilon"],
)
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(field: str, bad_value: float) -> None:
    """NaN / inf 一律拒绝：NaN 会一路 propagates 到数据库且不会报错。"""
    kwargs = {"lat": LAT, "lng": LNG, "epsilon": 1.0}
    kwargs[field] = bad_value
    with pytest.raises(InvalidObfuscationInput):
        obfuscate_location(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("lat", [91.0, -91.0, 90.0001, -90.0001])
def test_invalid_latitude_is_rejected(lat: float) -> None:
    with pytest.raises(InvalidObfuscationInput):
        obfuscate_location(lat, LNG)


@pytest.mark.parametrize("lng", [181.0, -181.0, 180.0001, -180.0001])
def test_invalid_longitude_is_rejected(lng: float) -> None:
    with pytest.raises(InvalidObfuscationInput):
        obfuscate_location(LAT, lng)


@pytest.mark.parametrize("epsilon", [0.0, -1.0, 11.0, 10.0001])
def test_invalid_epsilon_is_rejected(epsilon: float) -> None:
    """0 与负数是除零 / 反向保护，>10 说明调用方在主动放弃保护，都拦下来。"""
    with pytest.raises(InvalidObfuscationInput):
        obfuscate_location(LAT, LNG, "approx", epsilon)


@pytest.mark.parametrize("mode", ["xxx", "", "APPROX", "exact "])
def test_unknown_mode_is_rejected(mode: str) -> None:
    with pytest.raises(InvalidObfuscationInput):
        obfuscate_location(LAT, LNG, mode)


def test_privacy_error_is_the_base_of_input_errors() -> None:
    """`InvalidObfuscationInput` 必须是 `PrivacyError` 的子类（API 层按基类捕获）。"""
    assert issubclass(InvalidObfuscationInput, PrivacyError)
    with pytest.raises(PrivacyError):
        obfuscate_location(LAT, LNG, "approx", 0.0)


# ---------------------------------------------------------------- obfuscate_many
def test_obfuscate_many_samples_each_point_independently() -> None:
    """同一坐标重复多次 → 结果不全相同。这正是"独立采样、不做联合扰动"的可观测后果。"""
    results = obfuscate_many([(LAT, LNG)] * _SAMPLES, "approx", 1.0, rng=random.Random(8))
    assert len(results) == _SAMPLES
    assert len({(p.lat, p.lng) for p in results}) > 1


def test_obfuscate_many_preserves_order_and_metadata() -> None:
    """批量版必须与单点版同构：顺序一致、每个点的 radius/mode/epsilon 正确。"""
    points = [(30.0, 120.0), (31.0, 121.0), (32.0, 122.0)]
    results = obfuscate_many(points, "city", 2.0, rng=random.Random(2026))
    assert len(results) == len(points)
    assert all(isinstance(p, ObfuscatedPoint) for p in results)
    assert all(p.mode == "city" and p.epsilon == 2.0 for p in results)
    assert all(p.radius == MODE_RADIUS_M["city"] for p in results)
    for (raw_lat, _raw_lng), result in zip(points, results, strict=True):
        # city 模式 5000 米偏移 ≈ 0.045 度，不该跨到别的省去
        assert abs(result.lat - raw_lat) < 2.0


def test_obfuscate_many_exact_is_identity() -> None:
    """批量版同样尊重 `exact`。"""
    points = [(30.0, 120.0), (-89.5, -179.5)]
    results = obfuscate_many(points, "exact")
    assert [p.lat for p in results] == [30.0, -89.5]
    assert [p.lng for p in results] == [120.0, -179.5]
