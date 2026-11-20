"""阶段 6 导入闭环的接口层测试：`POST /api/v1/photos/import` + `GET /api/v1/trips`。

## 为什么这里用 `httpx.AsyncClient` 而不是 `TestClient`

`TestClient` 每一条请求都在**一个新的事件循环**里跑（它内部用 anyio 的
blocking portal），而 `app/db/session.py` 的 engine 是模块级单例、
连出来的 asyncpg 连接会被还进连接池等着复用。

于是第二条请求拿着上一条请求留下的、属于**已关闭循环**的连接去发 ping
（`pool_pre_ping=True`），结果是 `'NoneType' object has no attribute 'send'`
—— 一个与业务毫无关系的 `AttributeError`。实测：第一条请求 200，第二条必炸。

`httpx.AsyncClient(transport=ASGITransport(app))` 让请求跑在**用例自己的循环**里，
与真实 uvicorn 的"一个进程一个循环"一致，多条请求共用同一个连接池才成立。
代价是每个用例结束后要 `dispose_engine()`（把上一个循环里的连接清掉，
否则下一个用例的新循环会拿到死连接）—— 这正是 `tests/test_photo_import.py`
里已经在做的事，本文件沿用同一套写法。

## 为什么每个用例都要独占一个用户

`make seed-photos` 会在库里给 demo 用户（默认的 `我`）种上一百多张演示照片。
而导入接口是**按用户全量重建行程**的：直接拿 demo 用户跑，用例看到的行程里
会混进这些种子照片（行程数、照片数全都不对），清理时还得把演示数据一起删掉。

所以本文件把 `DEMO_USER_NAME` 指到一个**本用例专属**的用户上，
用例结束后把该用户删掉 —— `photo` / `trip` / `trip_item` 三张表的外键
都是 `ON DELETE CASCADE`，删用户一行就带走它的全部行李，不需要逐个表名去删。

## 假图为什么运行时生成

与 `tests/test_photo_import.py` 同一套做法（造图的工具函数也是从那里抄来的）：
仓库里不该出现二进制，且"纬度 30.2422 是怎么来的"必须能从测试代码里一眼看出。
本文件与它的区别只在于产出**字节**而不是文件 —— 接口收的是 multipart 字节。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from conftest import db_reachable
from PIL import ExifTags, Image
from PIL.Image import Exif
from PIL.TiffImagePlugin import IFDRational
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.main import app
from app.models.user import User

_IMPORT_URL = "/api/v1/photos/import"
_PHOTOS_URL = "/api/v1/photos"
_TRIPS_URL = "/api/v1/trips"

#: 杭州西湖附近（北纬 30.2422 / 东经 120.1494）的度分秒表示
#: 30.2422 = 30° + 14' + 31.92"；120.1494 = 120° + 8' + 57.84"
HZ_GPS = ("N", (30.0, 14.0, 31.92), "E", (120.0, 8.0, 57.84))

#: 拍摄时间（相机当地墙钟）。三个时刻的间隔经过设计：
#: #1 → #2 隔 30 分钟（同一趟），#2 → #3 隔 24.5 小时（> 12h，必须切一刀）
_T1 = "2026:04:12 09:00:00"
_T2 = "2026:04:12 09:30:00"
_T3 = "2026:04:13 10:00:00"


def _rationals(values: tuple[float, float, float]) -> tuple[IFDRational, ...]:
    """浮点 → EXIF 有理数（与 `tests/test_photo_import.py` 一致：分母固定 10000）。"""
    return tuple(IFDRational(round(value * 10000), 10000) for value in values)


def _jpeg_bytes(
    *,
    dt_original: str,
    gps: tuple[str, tuple[float, float, float], str, tuple[float, float, float]] | None = None,
    color: tuple[int, int, int] = (120, 160, 200),
) -> bytes:
    """生成一张带 EXIF 的 JPEG，返回**字节**（接口收的是字节而不是路径）。

    `color` 用来让不同的假图有不同像素：内容指纹按字节算，
    同一时间同一坐标的两张图若像素也一样，会被判成重复。
    """
    img = Image.new("RGB", (64, 48), color)
    exif = img.getexif()
    exif[0x9003] = dt_original
    if gps is not None:
        lat_ref, lat_values, lng_ref, lng_values = gps
        # GPS 必须挂在子 IFD 上，直接 exif[1] = "N" 会污染主 IFD 的 tag 空间
        gps_ifd = Exif()
        gps_ifd[1] = lat_ref
        gps_ifd[2] = _rationals(lat_values)
        gps_ifd[3] = lng_ref
        gps_ifd[4] = _rationals(lng_values)
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(gps_ifd)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def _upload(*payloads: bytes) -> list[tuple[str, tuple[str, bytes, str]]]:
    """拼 multipart 的 `files` 字段（可重复）。"""
    return [
        ("files", (f"photo-{index}.jpg", payload, "image/jpeg"))
        for index, payload in enumerate(payloads)
    ]


async def _purge_user(name: str) -> None:
    """删掉一个用户（外键 CASCADE 会带走它的 photo / trip / trip_item）。"""
    async with SessionFactory() as session:
        await session.execute(delete(User).where(User.name == name))
        await session.commit()


@pytest.fixture(autouse=True)
def isolated_demo_user(monkeypatch: pytest.MonkeyPatch) -> str:
    """把 `DEMO_USER_NAME` 指到一个本用例专属的用户上（不碰数据库）。

    为什么要用环境变量而不是给接口加一个 `user` 参数：
    本工程是单用户部署、没有账号体系（设计文档 §1.5），
    `get_or_create_demo_user()` 是全项目取用户的唯一入口。
    为了可测试性去给它加参数，等于把"谁在用"这个决定甩给调用方；
    改环境变量则是"换一个人来用这套系统"，正是单用户模型下的正确粒度。
    """
    name = f"pytest-api-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("DEMO_USER_NAME", name)
    get_settings.cache_clear()
    try:
        yield name
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def db_user(isolated_demo_user: str):
    """需要真实库的用例用它：**不可达则 skip**（skips 发生在建数据之前）。"""
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")
    yield isolated_demo_user
    # 删用户 → CASCADE 带走照片与行程；再释放连接池给下一个用例的新循环
    await _purge_user(isolated_demo_user)
    await dispose_engine()


@pytest.fixture
def photo_import_enabled(monkeypatch: pytest.MonkeyPatch):
    """开启 `photo_import` 开关（本机 `.env` 里它默认是关的）。"""
    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def photo_import_disabled(monkeypatch: pytest.MonkeyPatch):
    """显式关掉开关 —— 断言考的是"默认关闭"这件事本身，不是这台机器的配置。"""
    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def client():
    """跑在用自己事件循环里的 HTTP 客户端（原因见本文件顶部）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


# ---------------------------------------------------------------- 路由与开关


def test_routes_are_registered() -> None:
    """两条路由都要真的挂上：它们不会因为任何单测失败而自动显形。"""
    paths = app.openapi()["paths"]
    assert _IMPORT_URL in paths, f"导入路由没挂上：{list(paths)}"
    assert _TRIPS_URL in paths, f"行程路由没挂上：{list(paths)}"
    assert "post" in paths[_IMPORT_URL]
    assert "get" in paths[_TRIPS_URL]


@pytest.mark.parametrize("url", [_IMPORT_URL, _TRIPS_URL])
async def test_disabled_flag_returns_404(
    client: httpx.AsyncClient, photo_import_disabled, url: str
) -> None:
    """未启用 → **404**（能力不存在），不是 403（存在但没权限）。"""
    response = (
        await client.post(url, files=_upload(b"x")) if url == _IMPORT_URL else await client.get(url)
    )
    assert response.status_code == 404
    # 404 必须来自业务闸门而不是"压根没这条路由"
    assert response.json()["detail"] != "Not Found"


# ---------------------------------------------------------------- 导入：EXIF 解析


async def test_import_photo_with_exif_gps(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """带 GPS 的 JPEG：坐标解析成 WGS84 十进制度，来源标记为 exif。

    这条守的是"前端拿不到 EXIF 时后端兜底"这件事真的发生了 ——
    若解析出来是 None，接口照样 200，只有这里会红。
    """
    response = await client.post(
        _IMPORT_URL, files=_upload(_jpeg_bytes(dt_original=_T1, gps=HZ_GPS))
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 0
    assert body["failed"] == 0

    photo = body["photos"][0]
    assert photo["lat"] == pytest.approx(30.2422, abs=1e-3)
    assert photo["lng"] == pytest.approx(120.1494, abs=1e-3)
    assert photo["location_source"] == "exif"
    assert photo["coord_system"] == "WGS84"
    assert photo["location_confidence"] == 0.9
    # 时间是 ISO 字符串且能反解回同一时刻
    assert datetime.fromisoformat(photo["taken_at"]) == datetime(2026, 4, 12, 9, 0, tzinfo=UTC)
    assert photo["taken_at_local"] == "2026-04-12T09:00:00"
    # 时区未知的退化状态必须显式暴露，而不是让下游去猜
    assert photo["tz_name"] is None


async def test_import_photo_without_exif_gps(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """无 GPS 的照片：位置字段**整体**为空，但不影响时间与导入成功。"""
    response = await client.post(_IMPORT_URL, files=_upload(_jpeg_bytes(dt_original=_T1)))

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1

    photo = body["photos"][0]
    assert photo["lat"] is None
    assert photo["lng"] is None
    assert photo["location_source"] == "none"
    assert photo["location_confidence"] == 0.0
    assert datetime.fromisoformat(photo["taken_at"]) == datetime(2026, 4, 12, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------- 导入：失败与去重


async def test_non_image_bytes_count_as_failed(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """非图片字节 → **200 + failed=1**，而不是整批 500。

    一批里混着截图是常态，用户要的是"剩下那些导完了"：
    单张失败就地计数，整批继续。
    """
    response = await client.post(
        _IMPORT_URL, files=_upload(b"not an image", _jpeg_bytes(dt_original=_T1, gps=HZ_GPS))
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    assert body["imported"] == 1
    # 计数器互斥且完备：没有文件被静默吞掉
    assert body["imported"] + body["duplicates"] + body["failed"] == 2


async def test_same_bytes_twice_in_one_batch_counts_duplicate(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """同一批次里放两份**字节相同**的文件：第二份计入 duplicates，不重复插入。

    这条同时守住了"批次内去重"：只查库里会漏掉它，
    第二份插进去会撞 `unique(platform, asset_id)` 把整批事务炸成 500。
    """
    payload = _jpeg_bytes(dt_original=_T1, gps=HZ_GPS)

    response = await client.post(_IMPORT_URL, files=_upload(payload, payload))

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 1
    assert body["failed"] == 0
    # 两条回执指向同一行：去重是"认出同一张"，不是"丢掉一条"
    ids = {photo["id"] for photo in body["photos"]}
    assert len(ids) == 1
    assert len(body["photos"]) == 2


async def test_importing_twice_counts_duplicate_on_second_call(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """跨请求去重：第二次导入同一张 → 这次 `duplicates=1`、`imported=0`。

    与"批次内去重"是两条不同的路径，前者查库、后者查本批次，
    少实现任何一条都会让重复导入变成脏数据或 500。
    """
    payload = _jpeg_bytes(dt_original=_T1, gps=HZ_GPS)
    first = await client.post(_IMPORT_URL, files=_upload(payload))
    second = await client.post(_IMPORT_URL, files=_upload(payload))

    assert second.status_code == 200
    assert second.json()["imported"] == 0
    assert second.json()["duplicates"] == 1
    assert second.json()["photos"][0]["id"] == first.json()["photos"][0]["id"]


# ---------------------------------------------------------------- 导入不再聚类


async def test_import_creates_no_trips(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """**阶段 7 的架构调整**：导入只写 `Photo` 表，一趟行程都不产生。

    照片是唯一的物理事实，行程是用户手动组建的视图。原先"导入即全量重建"
    会让用户没有发言权、还会冲掉手工整理的结果，所以这条链被摘掉了。
    """
    response = await client.post(_IMPORT_URL, files=_upload(_jpeg_bytes(dt_original=_T1, gps=HZ_GPS)))

    body = response.json()
    assert body["imported"] == 1
    # 响应里不再有 trip_ids 字段：留一个恒为 [] 的键会让前端继续写死代码
    assert "trip_ids" not in body
    # 库里也确实没有行程
    assert (await client.get(_TRIPS_URL)).json()["trips"] == []


async def test_imported_photos_are_listable(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """导入完的照片必须能在 `GET /photos` 里看到 —— 照片是底层数据。"""
    await client.post(
        _IMPORT_URL,
        files=_upload(
            _jpeg_bytes(dt_original=_T2, gps=HZ_GPS, color=(10, 20, 30)),
            _jpeg_bytes(dt_original=_T1, gps=HZ_GPS, color=(20, 30, 40)),
        ),
    )

    photos = (await client.get(_PHOTOS_URL)).json()
    # 按拍摄时刻**倒序**：T2(09:30) 比 T1(09:00) 晚，所以 T2 在前
    assert [photo["taken_at_local"] for photo in photos] == [
        "2026-04-12T09:30:00",
        "2026-04-12T09:00:00",
    ]
    # 两张都有 GPS，has_location 必须为真
    assert all(photo["has_location"] for photo in photos)
    assert all(photo["location_source"] == "exif" for photo in photos)


# ---------------------------------------------------------------- 行程列表


async def test_trips_are_empty_before_any_import(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """一张照片都没有 → `{"trips": []}`，而不是 404：那是数据状态，不是接口不存在。"""
    response = await client.get(_TRIPS_URL)

    assert response.status_code == 200
    assert response.json() == {"trips": []}


async def test_trips_list_preserves_assigned_order(
    client: httpx.AsyncClient, db_user, photo_import_enabled
) -> None:
    """行程条目按**分配时给定的顺序**排列，且带坐标快照。

    ## 为什么顺序由前端定而不是后端排

    阶段 7 之前行程是聚类产物，顺序按 `taken_at` 排（上传顺序不该影响结果）。
    现在行程是用户手动组建的视图：他想让某张照片排第一，后端就该照办 ——
    再按时间重排一次，等于把用户的排列意图悄悄改掉。
    所以 `ord` 就是 `photo_ids` 的下标，后端不猜。

    顺序是行程路线本身：条目乱序 = 地图上一条乱走的线。
    """
    imported = (
        await client.post(
            _IMPORT_URL,
            files=_upload(
                _jpeg_bytes(dt_original=_T2, gps=HZ_GPS, color=(10, 20, 30)),
                _jpeg_bytes(dt_original=_T1, gps=HZ_GPS, color=(20, 30, 40)),
            ),
        )
    ).json()["photos"]
    by_time = {photo["taken_at_local"]: photo for photo in imported}
    later = by_time["2026-04-12T09:30:00"]
    earlier = by_time["2026-04-12T09:00:00"]

    created = (await client.post(_TRIPS_URL, json={"title": "顺序测试"})).json()["trip"]
    # 故意按"晚的在前"提交：后端必须照办，不能按时间重排
    await client.post(
        f"{_TRIPS_URL}/{created['id']}/photos",
        json={"photo_ids": [later["id"], earlier["id"]]},
    )

    trips = (await client.get(_TRIPS_URL)).json()["trips"]
    assert len(trips) == 1
    trip = trips[0]

    assert trip["item_count"] == 2
    assert [item["taken_at"] for item in trip["items"]] == [
        "2026-04-12T09:30:00+00:00",
        "2026-04-12T09:00:00+00:00",
    ]
    # 坐标快照：条目上带的是分配那一刻的值，不 join Photo 现取
    assert trip["items"][0]["lat"] == pytest.approx(30.2422, abs=1e-3)
    # 起止时间由分配进去的照片决定（而不是创建时的 null）
    assert datetime.fromisoformat(trip["started_at"]) == datetime(2026, 4, 12, 9, 0, tzinfo=UTC)
    assert datetime.fromisoformat(trip["ended_at"]) == datetime(2026, 4, 12, 9, 30, tzinfo=UTC)
    # 手动分配过的行程标成 manual，退出 rebuild_user_trips 的删除范围
    assert trip["source"] == "manual"
