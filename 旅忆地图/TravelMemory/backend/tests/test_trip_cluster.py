"""`services/trip_cluster.py` 的行为断言（**全离线，无 skip**）。

## 为什么这个测试文件里一条 skip 都不许有

被断言的对象是**纯函数**：没有数据库、没有网络、没有模型调用、不读环境变量。
纯函数需要外部资源才能测，本身就说明它不纯（混进了 IO 或全局状态）——
那种情况该做的不是 skip，而是把 IO 拆出去。所以这里不做任何条件跳过。

## 为什么边界值（正好 12h / 正好 50km）必须单独钉

阈值判定用的是**严格大于**：正好等于阈值属于"还在容忍范围内"，不切。
`>` 写成 `>=` 是这类代码里最常见的一字之差，而且它**不会报错**，
只会让"隔了整整 12 小时的连拍"被莫名其妙拆成两趟。
所以两个阈值各配一对"等于不切 / 超一点就切"的用例，把这个符号焊死。
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.photo import Photo
from app.services.trip_cluster import (
    ClusterConfig,
    ClusterItem,
    cluster_trips,
    cluster_trips_from_photos,
)
from app.utils.coords import EARTH_RADIUS_KM, haversine_km

#: 一趟"2025-10-01 早上 8 点开始的江南行"的起点时刻
BASE = datetime(2025, 10, 1, 8, 0, tzinfo=UTC)

#: 北京 / 上海（WGS84 大致坐标，用于构造跨城跳变）
BEIJING = (39.9042, 116.4074)
SHANGHAI = (31.2304, 121.4737)


def _item(
    photo_id: str,
    taken_at: datetime,
    lat: float | None = None,
    lng: float | None = None,
) -> ClusterItem:
    """造一个参与聚类的条目（缺省无坐标 —— 无坐标是常态，不该是特例）。"""
    return ClusterItem(photo_id=photo_id, taken_at=taken_at, lat=lat, lng=lng)


def _north_of(base_lat: float, km: float) -> float:
    """把纬度往北推 `km` 公里（**同经度**：大圆距离就等于 `km`，边界值才好钉）。"""
    return base_lat + math.degrees(km / EARTH_RADIUS_KM)


def test_single_photo_forms_one_cluster() -> None:
    """只有一张照片也要成一簇：不能返回空列表，否则导入 1 张照片就没有行程。"""
    clusters = cluster_trips([_item("a", BASE)])

    assert len(clusters) == 1
    assert clusters[0].photo_ids == ("a",)
    assert clusters[0].index == 0


def test_empty_input_returns_empty_list() -> None:
    """空输入返回 `[]` 而不是抛错 / 返回含一个空簇的列表。"""
    assert cluster_trips([]) == []


def test_six_hour_gap_keeps_one_cluster() -> None:
    """6 小时（< 12h 阈值）不切：白天一整趟活动必须留在一趟里。"""
    items = [_item("a", BASE), _item("b", BASE + timedelta(hours=6))]

    clusters = cluster_trips(items)

    assert len(clusters) == 1
    assert clusters[0].photo_ids == ("a", "b")


def test_thirteen_hour_gap_splits_into_two_clusters() -> None:
    """13 小时（> 12h）切一刀，且 `index` 按时间先后从 0 递增。"""
    items = [_item("a", BASE), _item("b", BASE + timedelta(hours=13))]

    clusters = cluster_trips(items)

    assert len(clusters) == 2
    assert [c.index for c in clusters] == [0, 1]
    assert clusters[0].photo_ids == ("a",)
    assert clusters[1].photo_ids == ("b",)


def test_exactly_max_gap_hours_does_not_split() -> None:
    """正好 12h → **不切**：判定是「严格大于」，等于阈值仍在容忍范围内。"""
    items = [_item("a", BASE), _item("b", BASE + timedelta(hours=12))]

    assert len(cluster_trips(items)) == 1


def test_one_second_over_max_gap_hours_splits() -> None:
    """12h 零 1 秒 → 切。与上一条合起来钉住的是 `>` 而不是 `>=`。"""
    items = [_item("a", BASE), _item("b", BASE + timedelta(hours=12, seconds=1))]

    assert len(cluster_trips(items)) == 2


def test_distance_at_threshold_does_not_split() -> None:
    """距离**正好等于** 50km → 不切（空间判定同样是「严格大于」）。

    为什么把阈值对齐到实际距离而不是直接写 50.0：
    由"50km 反推纬度差"再算回来会差 1 个 ULP（实测 50.0000000000005），
    用 50.0 当阈值会把它判成"超过阈值"，测到的就不是"等于"这个边界了。
    取实际距离当阈值后，再把它往下拉 1 个 ULP 断言立刻切成两簇 —— 两侧都钉住。
    """
    lat, lng = BEIJING
    far_lat = _north_of(lat, 50.0)
    exact = haversine_km(lat, lng, far_lat, lng)
    assert exact == pytest.approx(50.0)  # 确认构造出来的确实是 50km 量级

    items = [_item("a", BASE, lat, lng), _item("b", BASE + timedelta(minutes=30), far_lat, lng)]

    assert len(cluster_trips(items, ClusterConfig(max_gap_km=exact))) == 1
    assert len(cluster_trips(items, ClusterConfig(max_gap_km=math.nextafter(exact, 0.0)))) == 2


def test_distance_just_over_threshold_splits() -> None:
    """50.1km（> 50km）→ 切。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        _item("b", BASE + timedelta(minutes=30), _north_of(lat, 50.1), lng),
    ]

    assert len(cluster_trips(items)) == 2


def test_eighty_km_jump_within_one_day_splits() -> None:
    """同一天、只隔 1 小时却相距 80km → 切：时间没断层，是空间跳变在起作用。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        _item("b", BASE + timedelta(hours=1), _north_of(lat, 80.0), lng),
    ]

    assert len(cluster_trips(items)) == 2


def test_twenty_km_jump_within_one_day_keeps_one_cluster() -> None:
    """同城内移动 20km（< 50km）不切：逛一天城不该被拆成好几趟。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        _item("b", BASE + timedelta(hours=1), _north_of(lat, 20.0), lng),
    ]

    assert len(cluster_trips(items)) == 1


def test_missing_coords_never_causes_spatial_cut() -> None:
    """中间那张没有坐标时，不能拿它当"离谁都远/都近"，只能退回时间判断。

    守的是 `北京 → (无坐标, 1h 后) → 上海(1h 后)` 必须仍是**一簇**：
    北京到上海实际相距 1000+ km，但两对相邻照片里各有一张没坐标，
    空间距离无从计算，此时只能按时间（都在 12h 内）判成一趟。
    若把缺坐标当"距离无穷大"，这趟会被静默切成三段。
    """
    items = [
        _item("a", BASE, *BEIJING),
        _item("b", BASE + timedelta(hours=1)),
        _item("c", BASE + timedelta(hours=2), *SHANGHAI),
    ]

    clusters = cluster_trips(items)

    assert len(clusters) == 1
    assert clusters[0].photo_ids == ("a", "b", "c")


def test_shuffled_input_matches_sorted_input() -> None:
    """乱序输入与已排序输入的结果**完全相同**：结果写库，不能随数据库行序抖动。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        _item("b", BASE + timedelta(hours=1), _north_of(lat, 20.0), lng),
        _item("c", BASE + timedelta(hours=2), _north_of(lat, 21.0), lng),
        _item("d", BASE + timedelta(hours=15), _north_of(lat, 100.0), lng),
        _item("e", BASE + timedelta(hours=16), _north_of(lat, 101.0), lng),
    ]
    shuffled = [items[2], items[4], items[0], items[3], items[1]]

    expected = [c.photo_ids for c in cluster_trips(items)]
    actual = [c.photo_ids for c in cluster_trips(shuffled)]

    assert actual == expected
    assert expected == [("a", "b", "c"), ("d", "e")]


def test_center_averages_only_items_with_coords() -> None:
    """重心只对**有坐标**的照片求均值：无坐标的照片不能把重心拉向 (0, 0)。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        # 偏移 0.2°（约 22km，仍在 50km 内）—— 用 1° 会被判成空间跳变，切成两簇就测不到重心了
        _item("b", BASE + timedelta(hours=1), lat + 0.2, lng + 0.2),
        _item("c", BASE + timedelta(hours=2)),  # 无坐标
    ]

    clusters = cluster_trips(items)
    assert len(clusters) == 1

    cluster = clusters[0]
    assert cluster.center_lat == pytest.approx(lat + 0.1)
    assert cluster.center_lng == pytest.approx(lng + 0.1)


def test_center_is_none_when_no_item_has_coords() -> None:
    """整簇都没有坐标 → `center_lat` / `center_lng` 都是 `None`（不是 0.0）。

    返回 0.0 会让地图把行程打在几内亚湾（0°, 0°）——一个看起来"正常"的假位置。
    """
    items = [_item("a", BASE), _item("b", BASE + timedelta(hours=1))]

    cluster = cluster_trips(items)[0]

    assert cluster.center_lat is None
    assert cluster.center_lng is None


def test_started_at_and_ended_at_cover_the_whole_cluster() -> None:
    """`started_at` / `ended_at` 取簇内最早与最晚一张（输入乱序时也要对）。"""
    items = [
        _item("a", BASE),
        _item("b", BASE + timedelta(hours=5)),
        _item("c", BASE + timedelta(hours=2)),
    ]

    cluster = cluster_trips(items)[0]

    assert cluster.started_at == BASE
    assert cluster.ended_at == BASE + timedelta(hours=5)


def test_custom_config_thresholds_apply() -> None:
    """自定义 `ClusterConfig`（1h / 1km）生效：阈值不是写死的常量。"""
    lat, lng = BEIJING
    items = [
        _item("a", BASE, lat, lng),
        _item("b", BASE + timedelta(hours=2), _north_of(lat, 2.0), lng),
    ]

    # 默认阈值（12h / 50km）下显然是一簇
    assert len(cluster_trips(items)) == 1
    # 收紧到 1h / 1km 后，两个维度都超阈值 → 2 簇
    assert len(cluster_trips(items, ClusterConfig(max_gap_hours=1.0, max_gap_km=1.0))) == 2


def test_cluster_trips_from_photos_reads_photo_fields() -> None:
    """`Photo` → `ClusterItem` 的适配层取对字段：id / taken_at / lat / lng。"""
    pid_a, pid_b = uuid.uuid4(), uuid.uuid4()
    lat, lng = BEIJING
    photos = [
        Photo(id=pid_a, taken_at=BASE, lat=lat, lng=lng),
        Photo(id=pid_b, taken_at=BASE + timedelta(hours=13), lat=lat, lng=lng),
    ]

    clusters = cluster_trips_from_photos(photos)

    assert [c.photo_ids for c in clusters] == [(str(pid_a),), (str(pid_b),)]
    assert clusters[0].center_lat == pytest.approx(lat)


def test_cluster_trips_from_photos_skips_photos_without_taken_at() -> None:
    """没有拍摄时间的照片被跳过：它在时间轴上无处安放，硬塞进任何一簇都是编造。"""
    pid = uuid.uuid4()
    photos = [Photo(id=uuid.uuid4(), taken_at=None), Photo(id=pid, taken_at=BASE)]

    clusters = cluster_trips_from_photos(photos)

    assert [c.photo_ids for c in clusters] == [(str(pid),)]


def test_haversine_matches_known_city_distance() -> None:
    """球面距离先自检：京沪直线约 1067km（与公开值同量级），公式与单位别搞反。"""
    distance = haversine_km(*BEIJING, *SHANGHAI)

    assert distance == pytest.approx(1067.0, abs=5.0)
