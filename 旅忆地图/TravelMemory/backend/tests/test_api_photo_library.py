"""阶段 7：照片为底层、行程为视图 —— 接口层测试。

## 这个文件守什么

上一轮（阶段 6）的模型是"导入即自动聚类"，行程是**派生数据**。
本轮把它翻过来：`Photo` 是唯一的物理事实，`Trip` 只是用户手动组建的**视图**。
于是有四条新纪律必须有测试钉死：

  1. **删除行程绝不级联删照片**。这是本文件最重要的一条 ——
     它一旦破掉，"删一趟旅程"会变成"照片全没了"，而那是不可恢复的事故。
     纯内存测不出来（外键的 `ondelete` 在 SQL 里），所以这条连真库。
  2. **导入不再产生行程**。否则用户导入 3 张就被算法切成 2 趟，
     而他无法纠正 —— 行程现在归用户管。
  3. **照片库能看见全部照片**，且明确标出谁没定位。
  4. **补标坐标**要改来源与置信度，而不只是写 lat/lng。

## 为什么坐标补标也连真库测

`location_source` / `location_confidence` 是**两列**，Mock 一个 session
只能验证"代码调用了赋值"，验证不了"这两列真的落库了"。
而下游的地理模糊半径按置信度算，列没写对时界面看不出任何异常。

## 开关断言

`photo_import` 关着时全部接口 **404**（能力不存在），与阶段 6 一致。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from conftest import db_reachable
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.main import app
from app.models.photo import Photo
from app.models.trip import Trip, TripItem
from app.models.user import User
from app.services.photo_import import LOCATION_CONFIDENCE_USER, LOCATION_SOURCE_USER
from app.services.trip_service import create_manual_trip
from app.services.user_service import get_or_create_demo_user

_PHOTOS_URL = "/api/v1/photos"
_TRIPS_URL = "/api/v1/trips"

#: 杭州西湖附近（与 test_api_photos.py 同一组坐标，便于对照）
HZ_LAT = 30.2422
HZ_LNG = 120.1494


async def _purge_user(name: str) -> None:
    """删掉一个用户（外键 CASCADE 会带走它的 photo / trip / trip_item）。"""
    async with SessionFactory() as session:
        await session.execute(delete(User).where(User.name == name))
        await session.commit()


@pytest.fixture(autouse=True)
def isolated_demo_user(monkeypatch: pytest.MonkeyPatch) -> str:
    """把 `DEMO_USER_NAME` 指到本用例专属的用户上（不碰演示数据）。"""
    name = f"pytest-library-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("DEMO_USER_NAME", name)
    get_settings.cache_clear()
    try:
        yield name
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def db_user(isolated_demo_user: str):
    """需要真实库的用例用它：**不可达则 skip**。"""
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")
    yield isolated_demo_user
    await _purge_user(isolated_demo_user)
    await dispose_engine()


@pytest.fixture
def photo_import_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def photo_import_disabled(monkeypatch: pytest.MonkeyPatch):
    """显式关掉开关 —— 断言考的是"默认关闭"这件事本身。"""
    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


# ---------------------------------------------------------------- 路由与开关


def test_photo_library_routes_are_registered() -> None:
    """照片库的两个路由必须真的挂上。"""
    paths = app.openapi()["paths"]
    assert _PHOTOS_URL in paths, f"照片路由没挂上：{list(paths)}"
    assert "get" in paths[_PHOTOS_URL]
    location_path = f"{_PHOTOS_URL}/{{photo_id}}/location"
    assert location_path in paths, f"补标路由没挂上：{list(paths)}"
    assert "patch" in paths[location_path]


def test_trip_photo_routes_are_registered() -> None:
    """行程的删除 / 分配 / 移除三个路由必须真的挂上。"""
    paths = app.openapi()["paths"]
    trip_path = f"{_TRIPS_URL}/{{trip_id}}"
    assert trip_path in paths
    assert "delete" in paths[trip_path], f"缺 DELETE：{list(paths[trip_path])}"

    assign_path = f"{trip_path}/photos"
    assert assign_path in paths
    assert "post" in paths[assign_path], f"缺 POST：{list(paths[assign_path])}"

    remove_path = f"{trip_path}/photos/{{photo_id}}"
    assert remove_path in paths
    assert "delete" in paths[remove_path], f"缺 DELETE：{list(paths[remove_path])}"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", _PHOTOS_URL),
        ("patch", f"{_PHOTOS_URL}/{uuid.uuid4()}/location"),
        ("delete", f"{_TRIPS_URL}/{uuid.uuid4()}"),
        ("post", f"{_TRIPS_URL}/{uuid.uuid4()}/photos"),
        ("delete", f"{_TRIPS_URL}/{uuid.uuid4()}/photos/{uuid.uuid4()}"),
    ],
)
async def test_disabled_flag_returns_404(
    client: httpx.AsyncClient, photo_import_disabled, method: str, url: str
) -> None:
    """开关未开 → 全部 404（能力不存在），不是 403。"""
    if method == "patch":
        response = await client.patch(url, json={"lat": HZ_LAT, "lng": HZ_LNG})
    elif method == "post":
        response = await client.post(url, json={"photo_ids": [str(uuid.uuid4())]})
    else:
        response = await client.request(method, url)
    assert response.status_code == 404
    assert response.json()["detail"] != "Not Found"


# ---------------------------------------------------------------- 坐标补标


async def test_patch_location_rejects_out_of_range(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """纬度 999 / 经度 999 → 422，且错误信息是中文（前端原样显示给用户）。"""
    response = await client.patch(
        f"{_PHOTOS_URL}/{uuid.uuid4()}/location", json={"lat": 999, "lng": 120}
    )
    assert response.status_code == 422
    assert "坐标不合法" in response.json()["detail"]

    response = await client.patch(
        f"{_PHOTOS_URL}/{uuid.uuid4()}/location", json={"lat": 30, "lng": 999}
    )
    assert response.status_code == 422


async def test_patch_location_rejects_missing_fields(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """缺 lat / lng → 422，而不是写进一个 null 坐标。"""
    assert (
        await client.patch(f"{_PHOTOS_URL}/{uuid.uuid4()}/location", json={"lat": 30})
    ).status_code == 422
    assert (
        await client.patch(f"{_PHOTOS_URL}/{uuid.uuid4()}/location", json={"lng": 120})
    ).status_code == 422


async def test_patch_location_unknown_photo_returns_404(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """不存在的照片 → 404（与"不属于你"同一处理，不送枚举接口）。"""
    response = await client.patch(
        f"{_PHOTOS_URL}/{uuid.uuid4()}/location", json={"lat": HZ_LAT, "lng": HZ_LNG}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "照片不存在"


async def test_patch_location_malformed_uuid_returns_404(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """畸形 UUID → 404 而不是 422：手滑与枚举接口都该得到"不存在"。"""
    response = await client.patch(
        f"{_PHOTOS_URL}/不是UUID/location", json={"lat": HZ_LAT, "lng": HZ_LNG}
    )
    assert response.status_code == 404


async def test_patch_location_writes_source_and_confidence(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """补标必须连来源与置信度一起改，而不只是写 lat/lng。

    地理模糊的半径按 `location_confidence` 算，混淆来源会让"承诺半径"名不副实。
    """
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        photo = Photo(
            user_id=user.id,
            platform="web",
            asset_id=f"lib-{uuid.uuid4().hex[:8]}",
            hash=uuid.uuid4().hex,
            ai_status="done",
        )
        session.add(photo)
        await session.commit()
        photo_id = photo.id

    response = await client.patch(
        f"{_PHOTOS_URL}/{photo_id}/location", json={"lat": HZ_LAT, "lng": HZ_LNG}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lat"] == pytest.approx(HZ_LAT)
    assert body["lng"] == pytest.approx(HZ_LNG)
    assert body["has_location"] is True
    assert body["location_source"] == LOCATION_SOURCE_USER
    assert body["location_confidence"] == LOCATION_CONFIDENCE_USER

    # 落库的值也要对（响应可能是内存中间态）
    async with SessionFactory() as session:
        reloaded = await session.get(Photo, photo_id)
        assert reloaded is not None
        assert reloaded.location_source == LOCATION_SOURCE_USER
        assert reloaded.location_confidence == LOCATION_CONFIDENCE_USER
        assert reloaded.coord_system == "WGS84", "存储侧坐标系恒为 WGS84"


# ---------------------------------------------------------------- 删除行程


async def test_delete_trip_keeps_photos(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """**本文件最重要的一条**：删行程绝不级联删照片。

    外键的 `ondelete=CASCADE` 方向很容易看反：
    `TripItem.photo_id` 的 CASCADE 是"删照片时带走关联"，
    而删行程只该带走 `TripItem`。这条用例连真库钉死那个方向。
    """
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(session, user_id=user.id, title="待删的旅程")
        photos = [
            Photo(
                user_id=user.id,
                platform="web",
                asset_id=f"del-{uuid.uuid4().hex[:8]}-{i}",
                hash=uuid.uuid4().hex,
                ai_status="done",
            )
            for i in range(3)
        ]
        session.add_all(photos)
        await session.flush()
        for position, photo in enumerate(photos):
            session.add(TripItem(trip_id=trip.id, photo_id=photo.id, ord=position))
        await session.commit()
        trip_id = trip.id
        photo_ids = [photo.id for photo in photos]

    response = await client.delete(f"{_TRIPS_URL}/{trip_id}")
    assert response.status_code == 204, response.text

    async with SessionFactory() as session:
        assert (await session.get(Trip, trip_id)) is None, "行程没删掉"
        # 关联没了，但照片一条不少
        remaining = (
            (await session.execute(select(func.count()).select_from(Photo).where(Photo.id.in_(photo_ids))))
            .scalar()
        )
        assert remaining == 3, "删行程把照片也带走了 —— 这是不可恢复的事故"
        items = (
            (await session.execute(select(func.count()).select_from(TripItem).where(TripItem.trip_id == trip_id)))
            .scalar()
        )
        assert items == 0


async def test_delete_trip_unknown_returns_404(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """不存在的行程 → 404，不是 403（不送枚举接口）。"""
    response = await client.delete(f"{_TRIPS_URL}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "行程不存在"


# ---------------------------------------------------------------- 行程照片分配


async def _make_photos(session, user_id: uuid.UUID, count: int) -> list[Photo]:
    photos = [
        Photo(
            user_id=user_id,
            platform="web",
            asset_id=f"assign-{uuid.uuid4().hex[:8]}-{i}",
            hash=uuid.uuid4().hex,
            ai_status="done",
            taken_at=None,
        )
        for i in range(count)
    ]
    session.add_all(photos)
    await session.flush()
    return photos


async def test_assign_photos_sets_the_snapshot(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """分配是**全量快照**：第二次提交会替换第一次的结果，而不是叠加。"""
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(session, user_id=user.id, title="洛阳两日")
        photos = await _make_photos(session, user.id, 3)
        await session.commit()
        trip_id = trip.id
        first_two = [photos[0].id, photos[1].id]
        all_three = [photo.id for photo in photos]

    response = await client.post(
        f"{_TRIPS_URL}/{trip_id}/photos", json={"photo_ids": [str(pid) for pid in first_two]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["trip"]["item_count"] == 2

    # 再提交一次只含第 3 张：结果应当是"只有第 3 张"，而不是三张都在
    await client.post(
        f"{_TRIPS_URL}/{trip_id}/photos", json={"photo_ids": [str(all_three[2])]}
    )

    async with SessionFactory() as session:
        assigned = (
            (await session.execute(select(TripItem.photo_id).where(TripItem.trip_id == trip_id)))
            .scalars()
            .all()
        )
        assert list(assigned) == [all_three[2]], "分配不是全量快照，出现了叠加"

        reloaded = await session.get(Trip, trip_id)
        assert reloaded is not None
        assert reloaded.source == "manual", "手动整理过的行程要退出自动重建范围"

        # 三张照片都还在库里：分配只是建关联
        count = (
            (await session.execute(select(func.count()).select_from(Photo).where(Photo.id.in_(all_three))))
            .scalar()
        )
        assert count == 3


async def test_assign_photos_skips_unknown_ids(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """混进不存在的 photo_id 时**跳过而不是整体失败**。

    用户只是想"把这趟存成现在这样"，不该因为列表里混了一条过期 id 就存不了。
    """
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(session, user_id=user.id, title="混入脏 id")
        photos = await _make_photos(session, user.id, 2)
        await session.commit()
        trip_id = trip.id
        real = [photos[0].id]

    response = await client.post(
        f"{_TRIPS_URL}/{trip_id}/photos",
        json={"photo_ids": [str(real[0]), str(uuid.uuid4())]},
    )
    assert response.status_code == 200, response.text

    async with SessionFactory() as session:
        assigned = (
            (await session.execute(select(TripItem.photo_id).where(TripItem.trip_id == trip_id)))
            .scalars()
            .all()
        )
        assert list(assigned) == real


async def test_assign_photos_empty_list_returns_422(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """空列表 → 422。传空数组的意图是"清空"，而那应该走删除单张的接口。"""
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(session, user_id=user.id, title="空列表")
        await session.commit()
        trip_id = trip.id

    response = await client.post(f"{_TRIPS_URL}/{trip_id}/photos", json={"photo_ids": []})
    assert response.status_code == 422


async def test_assign_photos_unknown_trip_returns_404(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        photos = await _make_photos(session, user.id, 1)
        await session.commit()
        photo_id = photos[0].id

    response = await client.post(
        f"{_TRIPS_URL}/{uuid.uuid4()}/photos", json={"photo_ids": [str(photo_id)]}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------- 移除单张


async def test_remove_photo_keeps_the_photo_row(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    """从行程里移除一张照片：关联没了，照片还在库里，也还能再分配回去。"""
    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(session, user_id=user.id, title="移除测试")
        photos = await _make_photos(session, user.id, 2)
        await session.flush()
        for position, photo in enumerate(photos):
            session.add(TripItem(trip_id=trip.id, photo_id=photo.id, ord=position))
        await session.commit()
        trip_id = trip.id
        target = photos[0].id

    response = await client.delete(f"{_TRIPS_URL}/{trip_id}/photos/{target}")
    assert response.status_code == 204, response.text

    async with SessionFactory() as session:
        # 关联没了
        left = (
            (await session.execute(select(func.count()).select_from(TripItem).where(TripItem.trip_id == trip_id)))
            .scalar()
        )
        assert left == 1, "移除后这趟不该还有 2 条关联"
        # 照片还在
        assert await session.get(Photo, target) is not None, "移除关联把照片也删了"

    # 还能再分配回去（证明关联是可重建的，照片本身没被动过）
    again = await client.post(
        f"{_TRIPS_URL}/{trip_id}/photos", json={"photo_ids": [str(target)]}
    )
    assert again.status_code == 200


async def test_remove_photo_from_unknown_trip_returns_404(
    client: httpx.AsyncClient, photo_import_enabled, db_user: str
) -> None:
    response = await client.delete(f"{_TRIPS_URL}/{uuid.uuid4()}/photos/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "行程不存在"
