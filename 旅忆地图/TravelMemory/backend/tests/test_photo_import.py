"""W6-1 本地照片导入链路的断言（EXIF 解析 + SHA256 去重）。

## 为什么假图在运行时生成，而不是提交二进制 fixture

1. **仓库里不该出现二进制**：一张 64×48 的 JPEG 只有 1KB 左右，但它是**不可
   审查、不可 diff、不可合并**的产物，一旦 EXIF 需要调整就得重新生成再提交一遍；
   用 Pillow 在 `tmp_path` 里生成，改断言和改素材是同一处代码。
2. **素材即断言的一部分**：纬度 30.2422 是怎么来的，看
   `_write_jpeg(..., gps=("N", (30.0, 14.0, 31.92), ...))` 就一目了然——
   度 / 分 / 秒三个数直接写在测试里，不用去二进制里"考古"。
3. **离线可跑**：不依赖任何外部素材、不依赖 PostgreSQL（真库用例按
   `conftest.db_reachable()` 自动跳过，写法照抄 `test_document_service.py`）。

## 五张假图各自覆盖的分支

| 文件 | 覆盖分支 |
| --- | --- |
| `with_gps.jpg` | 有 GPS + 有时间 + **无时区偏移** → `tz_source="unknown"` 的退化路径 |
| `no_gps.jpg` | 无 GPS → `location_source="none"` 的位置降级路径 |
| `tz_offset.jpg` | GPS + 时间 + `OffsetTimeOriginal="-05:00"` → 跨时区换算路径 |
| `duplicate.jpg` | 与 #1 **字节完全相同** → SHA256 内容去重路径 |
| `corrupt.jpg` | 非法字节 → `PhotoImportError` 异常路径 |
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import db_reachable
from PIL import ExifTags, Image
from PIL.Image import Exif
from PIL.TiffImagePlugin import IFDRational

from app.core.config import get_settings
from app.db.session import SessionFactory, dispose_engine
from app.models.photo import Photo
from app.models.user import User
from app.services.photo_import import (
    COORD_SYSTEM_WGS84,
    LOCATION_CONFIDENCE_EXIF,
    LOCATION_CONFIDENCE_NONE,
    LOCATION_SOURCE_EXIF,
    LOCATION_SOURCE_NONE,
    TZ_SOURCE_EXIF,
    TZ_SOURCE_UNKNOWN,
    PhotoImportError,
    import_photo,
    parse_exif,
    read_photo,
    sha256_bytes,
    to_photo_record,
)

#: 杭州西湖附近（北纬 30.2422 / 东经 120.1494）的度分秒表示
#:
#: 30.2422 = 30° + 14' + 31.92"；120.1494 = 120° + 8' + 57.84"。
#: 故意保留秒的小数部分，用来验证有理数（IFDRational）也能被正确除开。
HZ_LAT_DMS = (30.0, 14.0, 31.92)
HZ_LNG_DMS = (120.0, 8.0, 57.84)

#: 拍摄时间（相机当地墙钟）
LOCAL_TAKEN_AT = "2026:04:12 09:31:05"
LOCAL_TAKEN_AT_NAIVE = datetime(2026, 4, 12, 9, 31, 5)


def _rationals(values: tuple[float, float, float]) -> tuple[IFDRational, ...]:
    """浮点 → EXIF 有理数。

    分母固定 10000：1/10000 秒 ≈ 3e-6 度，比断言用的 `abs=1e-3` 高三个数量级，
    既不会引入精度噪声，也避免了浮点直接写进有理数时出现的长尾小数。
    """
    return tuple(IFDRational(round(value * 10000), 10000) for value in values)


def _write_jpeg(
    path: Path,
    *,
    dt_original: str | None = None,
    offset_time: str | None = None,
    gps: tuple[str, tuple[float, float, float], str, tuple[float, float, float]] | None = None,
) -> bytes:
    """用 Pillow 生成一张带 EXIF 的 JPEG，返回文件字节。

    格式选 JPEG 而不是 PNG：Pillow 对 JPEG 的 EXIF 读写（含 GPS 子 IFD）
    支持最完整，PNG 走的是 eXIf chunk，Round-trip 行为在版本间差异较大。
    """
    img = Image.new("RGB", (64, 48), (120, 160, 200))
    exif = img.getexif()
    if dt_original is not None:
        exif[0x9003] = dt_original
    if offset_time is not None:
        exif[0x9011] = offset_time
    exif[0x010F] = "TravelMemory"
    exif[0x0110] = "FakeCam"
    if gps is not None:
        lat_ref, lat_values, lng_ref, lng_values = gps
        # GPS 必须挂在子 IFD 上：直接 exif[1] = "N" 会污染主 IFD 的 tag 空间
        gps_ifd = Exif()
        gps_ifd[1] = lat_ref
        gps_ifd[2] = _rationals(lat_values)
        gps_ifd[3] = lng_ref
        gps_ifd[4] = _rationals(lng_values)
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(gps_ifd)
    img.save(path, "JPEG", exif=exif)
    return path.read_bytes()


@pytest.fixture
def photos(tmp_path: Path) -> dict[str, Path]:
    """五张运行时生成的假图（见本文件顶部表格）。"""
    with_gps = tmp_path / "with_gps.jpg"
    _write_jpeg(with_gps, dt_original=LOCAL_TAKEN_AT, gps=("N", HZ_LAT_DMS, "E", HZ_LNG_DMS))

    no_gps = tmp_path / "no_gps.jpg"
    _write_jpeg(no_gps, dt_original=LOCAL_TAKEN_AT)

    tz_offset = tmp_path / "tz_offset.jpg"
    _write_jpeg(
        tz_offset,
        dt_original=LOCAL_TAKEN_AT,
        offset_time="-05:00",
        gps=("N", HZ_LAT_DMS, "E", HZ_LNG_DMS),
    )

    duplicate = tmp_path / "duplicate.jpg"
    # 整份字节复制：文件名不同但内容一致，正是去重要处理的情形
    duplicate.write_bytes(with_gps.read_bytes())

    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"\x89PNG not really an image")

    return {
        "with_gps": with_gps,
        "no_gps": no_gps,
        "tz_offset": tz_offset,
        "duplicate": duplicate,
        "corrupt": corrupt,
    }


def test_parse_exif_reads_gps_as_wgs84_decimal(photos: dict[str, Path]) -> None:
    """守 GPS 解析：度分秒必须正确折成十进制度，坐标系恒为 WGS84、置信度 0.9。

    这条挂了意味着地图上的点会整体偏移——而偏移量往往小到肉眼看不出来，
    只能靠断言守住。
    """
    meta = read_photo(photos["with_gps"])

    assert meta.lat == pytest.approx(30.2422, abs=1e-3)
    assert meta.lng == pytest.approx(120.1494, abs=1e-3)
    assert meta.location_source == LOCATION_SOURCE_EXIF
    assert meta.coord_system == COORD_SYSTEM_WGS84
    assert meta.location_confidence == LOCATION_CONFIDENCE_EXIF
    assert meta.width == 64
    assert meta.height == 48


def test_southern_and_western_refs_become_negative(tmp_path: Path) -> None:
    """守半球符号：S / W 必须取负。

    丢了 ref 的话，南半球西半球的照片会被镜像到东北半球——新西兰的行程
    会显示在北大西洋上，而且是"看起来正常"的错误。
    """
    path = tmp_path / "south_west.jpg"
    _write_jpeg(
        path,
        dt_original=LOCAL_TAKEN_AT,
        gps=("S", HZ_LAT_DMS, "W", HZ_LNG_DMS),
    )

    meta = read_photo(path)
    assert meta.lat == pytest.approx(-30.2422, abs=1e-3)
    assert meta.lng == pytest.approx(-120.1494, abs=1e-3)


def test_missing_gps_degrades_location_but_keeps_time(photos: dict[str, Path]) -> None:
    """守位置降级：无 GPS 时 lat/lng/source/confidence 必须**整体**为空。

    只清掉 lat 而留下 `location_source="exif"` 是典型的半吊子降级，
    下游会因为"有来源"而信任一个空坐标。
    """
    meta = read_photo(photos["no_gps"])

    assert meta.location_source == LOCATION_SOURCE_NONE
    assert meta.lat is None
    assert meta.lng is None
    assert meta.location_confidence == LOCATION_CONFIDENCE_NONE
    # 位置缺失不该连累时间
    assert meta.taken_at is not None
    assert meta.taken_at_local == LOCAL_TAKEN_AT_NAIVE


def test_offset_time_original_shifts_local_time_to_utc(photos: dict[str, Path]) -> None:
    """守跨时区换算：当地 09:31:05 且偏移 -05:00 → UTC 14:31:05。

    `taken_at_local` 必须是 naive 的当地墙钟，两个字段分工不能串。
    """
    meta = read_photo(photos["tz_offset"])

    assert meta.taken_at_local == LOCAL_TAKEN_AT_NAIVE
    assert meta.taken_at_local is not None
    assert meta.taken_at_local.tzinfo is None

    assert meta.taken_at is not None
    assert meta.taken_at.tzinfo is not None
    assert meta.taken_at.astimezone(UTC) == datetime(2026, 4, 12, 14, 31, 5, tzinfo=UTC)

    assert meta.tz_name == "UTC-05:00"
    assert meta.tz_source == TZ_SOURCE_EXIF


def test_unknown_timezone_stores_local_wallclock_as_utc(photos: dict[str, Path]) -> None:
    """守"无时区"取舍的落点：不可信必须**显式标记**，且墙钟数字不许被改动。

    `tz_name=None` + `tz_source="unknown"` 是下游判断"这个 taken_at 不可信"
    的唯一依据；`taken_at` 的 naive 部分保持 09:31:05 则保证按天聚合
    （用 taken_at_local）和按时刻排序（用 taken_at）的口径一致。
    """
    meta = read_photo(photos["with_gps"])

    assert meta.tz_name is None
    assert meta.tz_source == TZ_SOURCE_UNKNOWN

    assert meta.taken_at is not None
    assert meta.taken_at.utcoffset() == timedelta(0)
    assert meta.taken_at.replace(tzinfo=None) == LOCAL_TAKEN_AT_NAIVE
    assert meta.taken_at_local == LOCAL_TAKEN_AT_NAIVE


def test_sha256_fingerprint_detects_duplicate_content(photos: dict[str, Path]) -> None:
    """守去重：字节完全相同（只换了文件名）必须得到同一个指纹。

    这是"原图不上云、只留指纹"能成立的前提——指纹必须稳定且只由内容决定。
    """
    original = read_photo(photos["with_gps"])
    copy = read_photo(photos["duplicate"])

    assert original.sha256 == copy.sha256
    assert len(original.sha256) == 64
    assert original.sha256 == original.sha256.lower()
    # 顺带守住纯函数：同一份字节直接喂给 parse_exif 结果一致
    assert sha256_bytes(photos["with_gps"].read_bytes()) == original.sha256


def test_corrupt_file_raises_photo_import_error(photos: dict[str, Path]) -> None:
    """守异常归一：非法图片必须抛本模块的 `PhotoImportError`。

    否则 Pillow 的 `UnidentifiedImageError` 会一路漏到 API 层，
    变成 500 + 一段用户看不懂的英文堆栈。
    """
    with pytest.raises(PhotoImportError):
        read_photo(photos["corrupt"])

    with pytest.raises(PhotoImportError):
        parse_exif(b"")


def test_to_photo_record_maps_meta_to_photo_fields(
    user_id: uuid.UUID, photos: dict[str, Path]
) -> None:
    """守字段映射：`PhotoMeta` → `Photo` 不能漏字段、不能串字段。

    经纬度必须来自 `lat`/`lng`（不是反过来），时间必须双写，
    坐标系必须显式写 WGS84 而不是依赖列默认值。
    """
    meta = read_photo(photos["tz_offset"])
    photo = to_photo_record(
        user_id, meta, platform="web", local_thumb_path=str(photos["with_gps"].parent / "t.jpg")
    )

    assert isinstance(photo, Photo)
    assert photo.user_id == user_id
    assert photo.platform == "web"
    assert photo.hash == meta.sha256
    assert photo.asset_id == meta.sha256[:32]
    assert photo.lat == pytest.approx(30.2422, abs=1e-3)
    assert photo.lng == pytest.approx(120.1494, abs=1e-3)
    assert photo.coord_system == COORD_SYSTEM_WGS84
    assert photo.location_source == LOCATION_SOURCE_EXIF
    assert photo.location_confidence == LOCATION_CONFIDENCE_EXIF
    assert photo.taken_at == meta.taken_at
    assert photo.taken_at_local == meta.taken_at_local
    assert photo.tz_name == "UTC-05:00"
    assert photo.tz_source == TZ_SOURCE_EXIF
    assert photo.local_thumb_path is not None


def test_to_photo_record_keeps_explicit_asset_id(user_id: uuid.UUID, photos: dict[str, Path]) -> None:
    """守 asset_id 覆盖：端侧有自己的稳定标识（如 MediaStore id）时不能被指纹顶掉。"""
    meta = read_photo(photos["no_gps"])
    photo = to_photo_record(user_id, meta, platform="android", asset_id="media-store-42")

    assert photo.platform == "android"
    assert photo.asset_id == "media-store-42"
    assert photo.hash == meta.sha256


async def test_import_photo_is_gated_by_feature_flag(
    user_id: uuid.UUID, photos: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """守门控：开关关闭时导入必须**先于任何 IO** 就失败。

    门控放在函数第一行，关着的时候连文件都不会去读——这样"功能没开"
    和"文件坏了"是两种可区分的错误，排障时不会互相误导。
    """
    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with pytest.raises(PhotoImportError, match="未启用"):
            await import_photo(None, user_id=user_id, path=photos["with_gps"])  # type: ignore[arg-type]
    finally:
        # 配置是 lru_cache 的，不清理会把 false 泄漏给后续用例
        get_settings.cache_clear()


@pytest.mark.db
async def test_import_photo_dedupes_by_content_hash(
    photos: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """守真实落库 + 去重：同一字节第二次导入必须返回 `(同一行, False)`。

    这条需要真库（`photo` 表 + `user` 外键）。没容器时自动跳过，
    不影响 `make test` 的整体结果 —— 跳过写法照抄 `test_document_service.py`。
    """
    if not await db_reachable():
        pytest.skip("未检测到 PostgreSQL，跳过（make up && make migrate 后可运行）")

    monkeypatch.setenv("PHOTO_IMPORT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with SessionFactory() as session:
            owner = User(name=f"pytest-{uuid.uuid4().hex[:8]}")
            session.add(owner)
            await session.flush()

            first, created = await import_photo(
                session, user_id=owner.id, path=photos["with_gps"]
            )
            assert created is True
            assert first.id is not None
            assert first.hash is not None
            assert first.coord_system == COORD_SYSTEM_WGS84
            assert first.taken_at_local == LOCAL_TAKEN_AT_NAIVE

            second, created_again = await import_photo(
                session, user_id=owner.id, path=photos["duplicate"]
            )
            assert created_again is False
            assert second.id == first.id

            await session.rollback()
    finally:
        get_settings.cache_clear()
        await dispose_engine()
