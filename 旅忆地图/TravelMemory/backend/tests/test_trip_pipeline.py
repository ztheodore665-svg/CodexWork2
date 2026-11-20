"""阶段 6 导入闭环的**服务层**测试：照片导入 → 行程聚类 → 行程落库。

本文件盯的是"整条链路接上了"，与两个邻居的分工是：

| 文件 | 盯什么 |
| --- | --- |
| `tests/test_trip_cluster.py` | 切分算法本身（纯函数，全离线，一条 skip 都没有） |
| `tests/test_api_photos.py` | 接口契约（multipart、状态码、响应字段） |
| **本文件** | 算法与数据库之间的那段：导入 → 重建 → 落库的行与值对不对 |

## 为什么真库用例要把 `Photo` 表和 `Trip` 表分开断言

`Trip` 是**派生表**：它的全部内容都能由 `Photo` 重算出来。
本文件因此有一条贯穿始终的断言 —— **重建前后照片一条不少**。
少了它，"重建很方便"和"重建把照片也删了"在测试上就长得一模一样，
而后者是不可恢复的事故。

## 假图为什么运行时生成

与 `tests/test_api_photos.py` 同一套做法（造图技术抄自 `tests/test_photo_import.py`）：
不往仓库里塞二进制，且"这个时间、这个坐标"必须能从代码里一眼看出。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import db_reachable
from PIL import ExifTags, Image
from PIL.Image import Exif
from PIL.TiffImagePlugin import IFDRational
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.models.photo import Photo
from app.models.trip import Trip, TripItem
from app.models.user import User
from app.services.photo_import import import_photo_batch
from app.services.trip_cluster import cluster_trips_from_photos
from app.services.trip_service import rebuild_user_trips

#: 杭州西湖附近（北纬 30.2422 / 东经 120.1494）的度分秒表示
HZ_GPS = ("N", (30.0, 14.0, 31.92), "E", (120.0, 8.0, 57.84))

#: 北京 / 上海（WGS84 大致坐标，用于构造跨城跳变）
BEIJING = (39.9042, 116.4074)
SHANGHAI = (31.2304, 121.4737)

#: 一个"跨 13 小时"的基准时刻
BASE = datetime(2026, 4, 12, 9, 0, tzinfo=UTC)


def _rationals(values: tuple[float, float, float]) -> tuple[IFDRational, ...]:
    """浮点 → EXIF 有理数（与 `tests/test_photo_import.py` 一致：分母固定 10000）。"""
    return tuple(IFDRational(round(value * 10000), 10000) for value in values)


def _jpeg_bytes(
    *,
    dt_original: str | None = None,
    gps: tuple[str, tuple[float, float, float], str, tuple[float, float, float]] | None = None,
    color: tuple[int, int, int] = (120, 160, 200),
) -> bytes:
    """生成一张 JPEG 字节（时间与 GPS 都可缺省，用来造"信息缺失"的照片）。

    `color` 用来让不同的假图有不同像素：内容指纹按字节算，
    同一时间同一坐标的两张图若像素也一样，会被判成重复。
    """
    img = Image.new("RGB", (64, 48), color)
    exif = img.getexif()
    if dt_original is not None:
        exif[0x9003] = dt_original
    if gps is not None:
        lat_ref, lat_values, lng_ref, lng_values = gps
        gps_ifd = Exif()
        gps_ifd[1] = lat_ref
        gps_ifd[2] = _rationals(lat_values)
        gps_ifd[3] = lng_ref
        gps_ifd[4] = _rationals(lng_values)
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(gps_ifd)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def _photo(
    taken_at: datetime | None,
    lat: float | None = None,
    lng: float | None = None,
) -> Photo:
    """造一个只带聚类所需字段的 `Photo`（纯算法用例用不到落库）。"""
    return Photo(id=uuid.uuid4(), taken_at=taken_at, lat=lat, lng=lng)


# ---------------------------------------------------------------- 纯算法：切分


def test_thirteen_hour_gap_splits_a_trip_into_two() -> None:
    """相邻两张隔 13h（> 12h 阈值）→ 切一刀，两趟的照片归属与时间窗都对。

    更细的边界值（正好 12h 不切、12h 零 1 秒切）由 `tests/test_trip_cluster.py` 钉住；
    这里守的是**切一刀发生在相邻两张之间**：隔 13h 的是 #2 与 #3，
    而不是首尾两张（首尾隔 14h，光看首尾会把 #2 算错到第二趟去）。
    """
    photos = [
        _photo(BASE),
        _photo(BASE + timedelta(hours=1)),
        _photo(BASE + timedelta(hours=14)),
        _photo(BASE + timedelta(hours=15)),
    ]

    clusters = cluster_trips_from_photos(photos)

    assert len(clusters) == 2
    assert clusters[0].photo_ids == (str(photos[0].id), str(photos[1].id))
    assert clusters[1].photo_ids == (str(photos[2].id), str(photos[3].id))
    assert clusters[0].started_at == BASE
    assert clusters[0].ended_at == BASE + timedelta(hours=1)
    assert clusters[1].started_at == BASE + timedelta(hours=14)


def test_photos_without_coords_never_cause_a_spatial_cut() -> None:
    """缺坐标的照片**不传播**空间跳变：北京与上海之间隔着它们也不切。

    这里守的是"相邻对"这个口径：空间距离只在**都有坐标的相邻两张**之间算，
    不会被最近的已知坐标替补。于是 `北京 → (无坐标) → 上海 → (无坐标) → 北京`
    虽然几何上跨了两千公里，仍然是一趟。

    为什么不"替补"：替补出来的距离是真的（1067km），但它是**猜**的 ——
    用户可能真的坐了飞机，也可能那张没坐标的照片是室内拍的、人根本没动过。
    信息不足时只用已有的信息，宁可不切也不能猜着切。
    """
    photos = [
        _photo(BASE, *BEIJING),
        _photo(BASE + timedelta(hours=1)),
        _photo(BASE + timedelta(hours=2), *SHANGHAI),
        _photo(BASE + timedelta(hours=3)),
        _photo(BASE + timedelta(hours=4), *BEIJING),
    ]

    clusters = cluster_trips_from_photos(photos)

    assert len(clusters) == 1
    assert len(clusters[0].items) == 5


def test_photo_without_taken_at_belongs_to_no_trip() -> None:
    """没有拍摄时间的照片被聚类跳过：它在时间轴上无处安放，塞进任何一簇都是编造。"""
    photos = [_photo(None, *BEIJING), _photo(BASE), _photo(BASE + timedelta(hours=1))]

    clusters = cluster_trips_from_photos(photos)

    assert len(clusters) == 1
    assert clusters[0].photo_ids == (str(photos[1].id), str(photos[2].id))


# ---------------------------------------------------------------- 真库：导入 → 重建


async def _new_user(session: AsyncSession) -> User:
    """建一个本用例专属的用户（不碰 demo 用户的演示数据）。"""
    owner = User(name=f"pytest-{uuid.uuid4().hex[:8]}")
    session.add(owner)
    await session.flush()
    return owner


async def _item_photo_ids(session: AsyncSession, trip_id: uuid.UUID) -> list[str]:
    """一个行程里的照片 id（按 `ord` 升序 —— 视图顺序就是它）。"""
    rows = (
        await session.execute(
            select(TripItem.photo_id)
            .where(TripItem.trip_id == trip_id)
            .order_by(TripItem.ord.asc())
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _photo_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    total = (
        await session.execute(
            select(func.count()).select_from(Photo).where(Photo.user_id == user_id)
        )
    ).scalar()
    return int(total or 0)


async def _shape(session: AsyncSession, trips: list[Trip]) -> list[tuple[str, str]]:
    """一趟行程的"语义形状"：标题 + 起止 + 照片序列（**不含 id**）。"""
    return [
        (
            trip.title,
            f"{trip.started_at}|{trip.ended_at}|" + ",".join(await _item_photo_ids(session, trip.id)),
        )
        for trip in trips
    ]


@pytest.mark.db
async def test_import_then_rebuild_splits_trips_by_time_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """导入三张（两趟）→ 重建出 2 趟，簇内照片数与归属都对。

    这是本阶段的主链路：字节进 → 行程出。少了任何一环都会在这里露出来，
    而不是等到前端打开地图才发现。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            result = await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                    _jpeg_bytes(dt_original="2026:04:12 09:30:00", gps=HZ_GPS, color=(4, 5, 6)),
                    _jpeg_bytes(dt_original="2026:04:13 10:00:00", gps=HZ_GPS, color=(7, 8, 9)),
                ],
            )
            assert result.imported == 3
            assert result.failed == 0

            trips = await rebuild_user_trips(session, user_id=owner.id)

            assert len(trips) == 2
            assert [trip.title for trip in trips] == [
                "2026-04-12 起的旅程",
                "2026-04-13 起的旅程",
            ]
            assert await _item_photo_ids(session, trips[0].id) == [
                str(result.photos[0].id),
                str(result.photos[1].id),
            ]
            assert await _item_photo_ids(session, trips[1].id) == [str(result.photos[2].id)]
            assert all(trip.source == "cluster" for trip in trips)

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()


@pytest.mark.db
async def test_second_import_merges_into_the_existing_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二次导入的落点：新照片**并入**已成型的那趟，而不是新起一趟。

    这条守的是"重建看的是该用户的全部照片"而不是"只看本次导入的"：
    只看本次的话，第二次导入的一张照片会变成孤零零的一趟。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                    _jpeg_bytes(dt_original="2026:04:13 10:00:00", gps=HZ_GPS, color=(7, 8, 9)),
                ],
            )
            await rebuild_user_trips(session, user_id=owner.id)

            await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 11:00:00", gps=HZ_GPS, color=(11, 12, 13)),
                ],
            )
            trips = await rebuild_user_trips(session, user_id=owner.id)

            # 还是两趟，只是第一趟从 1 张变成 2 张
            assert len(trips) == 2
            assert [len(await _item_photo_ids(session, trip.id)) for trip in trips] == [2, 1]

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()


@pytest.mark.db
async def test_rebuilding_twice_is_idempotent_and_keeps_photos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重建跑两遍：行程的**语义**完全一致，且照片一条不少。

    这是"Trip 是派生数据、可安全重建"这条论证的可执行版本：
      - 边界与归属相同（只有 id 会变，因为主键由数据库生成）；
      - `Photo` 表行数不变 —— 重建只删 trip，绝不删 photo。
    少了第二条，"重建很方便"与"重建把数据源也清了"在测试上长得一模一样。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                    _jpeg_bytes(dt_original="2026:04:13 10:00:00", gps=HZ_GPS, color=(7, 8, 9)),
                ],
            )
            first = await rebuild_user_trips(session, user_id=owner.id)
            first_shape = await _shape(session, first)

            second = await rebuild_user_trips(session, user_id=owner.id)
            second_shape = await _shape(session, second)

            assert first_shape == second_shape
            assert len(second) == 2
            # 主键确实换了（重建插的是新行），但语义形状一致
            assert [trip.id for trip in first] != [trip.id for trip in second]
            assert await _photo_count(session, owner.id) == 2

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()


@pytest.mark.db
async def test_photo_without_exif_time_is_imported_but_not_clustered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有拍摄时间的照片：**照样入库**，但不进任何行程。

    两个方向都要守住：
      - 不能因为它算不出时间就整张拒收（它仍是一条有效的照片记录）；
      - 也不能把它硬塞进某趟（那样会让行程的起止时间变成编造）。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            result = await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                    _jpeg_bytes(gps=HZ_GPS, color=(4, 5, 6)),  # 没有 DateTimeOriginal
                ],
            )
            assert result.imported == 2

            trips = await rebuild_user_trips(session, user_id=owner.id)

            assert len(trips) == 1
            assert await _item_photo_ids(session, trips[0].id) == [str(result.photos[0].id)]
            assert await _photo_count(session, owner.id) == 2

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()


@pytest.mark.db
async def test_trip_item_is_unique_per_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    """`uq_trip_item_photo` 真的在库里：同一张照片挂两遍会被数据库拒绝。

    没有这条约束时脏数据**不报错**，只在地图上表现为"这趟怎么多了一张"。
    所以约束的存在性要绕过程序直接插第二遍来验 —— 程序本身永远不会这么插。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            result = await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                ],
            )
            trips = await rebuild_user_trips(session, user_id=owner.id)
            photo = result.photos[0]

            session.add(
                TripItem(
                    trip_id=trips[0].id,
                    photo_id=photo.id,
                    taken_at=photo.taken_at,
                    lat=photo.lat,
                    lng=photo.lng,
                    ord=99,
                )
            )
            with pytest.raises(Exception, match="uq_trip_item_photo"):
                await session.flush()

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()


@pytest.mark.db
async def test_rebuild_clears_orphan_trips_when_photos_are_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """照片被清空后重建 → 行程也清空：视图必须与数据源一致。

    留着旧行程的结果是"能看到行程但点进去是空的"，
    那是把派生数据当成了独立事实存着。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = await _new_user(session)
            await import_photo_batch(
                session,
                user_id=owner.id,
                payloads=[
                    _jpeg_bytes(dt_original="2026:04:12 09:00:00", gps=HZ_GPS, color=(1, 2, 3)),
                ],
            )
            await rebuild_user_trips(session, user_id=owner.id)

            await session.execute(delete(Photo).where(Photo.user_id == owner.id))
            trips = await rebuild_user_trips(session, user_id=owner.id)

            assert trips == []
            trip_total = (
                await session.execute(
                    select(func.count()).select_from(Trip).where(Trip.user_id == owner.id)
                )
            ).scalar()
            assert trip_total == 0

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()
