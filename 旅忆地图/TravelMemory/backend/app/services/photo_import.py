"""本地照片导入的 **EXIF 解析 + 内容指纹去重**（阶段 6 W6-1）。

放在 services 层而不是 agent 层，是为了守住依赖方向
`agent → nodes → tools → services → models`：导入链路只向下依赖 `models.photo`，
将来 agent 侧的相册工具 / native bridge（W6-3）再来调它，谁也不需要反向 import 谁。

## 三个必须注意的点

1. **原图不入库、不落盘、不上传。** 本模块只读文件字节、只产出一条 `Photo` 行：
   库里存的是 `platform + asset_id + hash`，原图始终留在设备上（设计文档 §4）。
   因此这里的解析是**一次性的**——字节读完算完就丢，不做缓存、不写临时文件。
2. **去重按内容而不是按文件名。** 同一张照片被微信 / 相册 / iCloud 转存过一遍后，
   文件名和 mtime 全变了，但像素字节往往一模一样。用文件字节的 sha256 做指纹，
   才能做到"换了个马甲也认得出来"；代价是要把整份字节读进内存，
   所以批量导入务必**按张串行**处理，别并发把内存打满。
3. **时间双写。** `taken_at`（UTC，tz-aware）与 `taken_at_local`
   （拍摄地当地时间，naive）两个字段一起写，因为 EXIF 的 `DateTimeOriginal`
   是**相机当地的墙钟时间**，本身不带时区。只有 `OffsetTimeOriginal` 存在时
   才能算出可信的 UTC 时刻；缺失时的落点见 `parse_exif` 的取舍说明。
   结论是：**按天聚合一律以 `taken_at_local` 为准**
   （`services/day_digest.py:group_photos_by_day` 就是这么做的）——
   这正是 `Photo` 同时存在两个时间字段的全部理由。
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.feature_flags import is_enabled
from app.core.logging import get_logger
from app.models.photo import Photo

logger = get_logger(__name__)

#: 位置来源：EXIF 里读到 GPS
LOCATION_SOURCE_EXIF = "exif"
#: 位置来源：用户在地图上手动补标（阶段 7 的位置补标闭环）
LOCATION_SOURCE_USER = "user"
#: 位置来源：完全没有位置信息
LOCATION_SOURCE_NONE = "none"

#: 位置置信度：EXIF GPS 是设备直接记录的一手证据，给 0.9 而不是 1.0
LOCATION_CONFIDENCE_EXIF = 0.9
#: 位置置信度：用户在地图上亲手点的，是**当事人陈述**而非设备记录。
#: 比 EXIF 低一档（0.8）：用户可能记错、可能手滑点到隔壁街区，
#: 而这个值会进地理模糊的半径计算，虚高会让"承诺半径"名不副实。
LOCATION_CONFIDENCE_USER = 0.8
#: 位置置信度：无位置信息
LOCATION_CONFIDENCE_NONE = 0.0

#: 坐标系：**存储侧恒为 WGS84**，展示时再转 GCJ-02（PROJECT.md §二、[2]）
COORD_SYSTEM_WGS84 = "WGS84"

#: 时区来源：EXIF 的 OffsetTimeOriginal
TZ_SOURCE_EXIF = "exif"
#: 时区来源：无法推断，只能退化处理
TZ_SOURCE_UNKNOWN = "unknown"

#: 拍摄时间（当地墙钟）—— 优先级最高
_TAG_DATETIME_ORIGINAL = 0x9003
#: 数字化时间
_TAG_DATETIME_DIGITIZED = 0x9004
#: 文件变更时间（IFD0），兜底
_TAG_DATETIME = 0x0132
#: 拍摄时间相对 UTC 的偏移，形如 "+08:00" / "-05:00"
_TAG_OFFSET_TIME_ORIGINAL = 0x9011
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110

#: EXIF 时间的固定格式（`datetime.strptime` 的写法）
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

#: UTC 偏移的严格匹配：只要 `±HH:MM`，其他一律按"未知"处理
_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


class PhotoImportError(AppError):
    """照片导入失败：文件不是合法图片，或导入功能未启用。

    继承 `AppError` 而不是裸 `Exception`，是为了让 API 层能用统一的
    `user_message` 兜底展示，而不是把 Pillow 的解码堆栈漏给用户。
    """

    user_message = "照片导入失败，请确认文件是有效的图片"


@dataclass(frozen=True)
class PhotoMeta:
    """一张照片解析出来的**全部结构化信息**（纯数据，不碰数据库）。

    它是 `parse_exif` 与 `to_photo_record` 之间的契约：前者只管从字节里
    挖信息，后者只管把信息映射成 ORM 行。中间隔着一层，两者就能各自单测。
    """

    #: 内容指纹（文件字节的 sha256 hex，64 位小写）—— 跨文件名的去重键
    sha256: str
    width: int | None
    height: int | None

    #: UTC，tz-aware，写入 `Photo.taken_at`；tz 未知时是退化值（见 parse_exif）
    taken_at: datetime | None
    #: 拍摄地当地时间（naive），写入 `Photo.taken_at_local`；**按天聚合只认它**
    taken_at_local: datetime | None
    #: 形如 "UTC+08:00"；无时区信息时为 None
    tz_name: str | None
    #: "exif" | "unknown"
    tz_source: str

    lat: float | None
    lng: float | None
    #: 恒为 "WGS84"
    coord_system: str
    #: "exif" | "none"
    location_source: str
    #: exif=0.9 / none=0.0
    location_confidence: float

    #: 解析出的原始 EXIF（键为人类可读的 tag 名，值已 JSON 序列化友好化）
    exif_json: dict[str, Any]


def sha256_bytes(data: bytes) -> str:
    """内容指纹：文件字节的 sha256 hex（64 位小写）。"""
    return hashlib.sha256(data).hexdigest()


def _to_float(value: object) -> float | None:
    """把 EXIF 里的有理数统一成 float，无法解释时返回 None。

    同一个"度分秒"字段，在不同相机 / 不同 Pillow 版本下可能是
    `IFDRational`、`(num, den)` 元组、裸 int 或 float。不统一处理，
    `deg + minute / 60` 这种表达式总会在某一种类型上炸掉。
    """
    if isinstance(value, IFDRational):
        return float(value)
    if isinstance(value, tuple):
        if len(value) != 2:
            return None
        num, den = value
        try:
            return float(num) / float(den)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    # bool 是 int 的子类，但 EXIF 里不会出现它，宁可判为未知
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _dms_to_decimal(values: object, ref: object) -> float | None:
    """度分秒 → 十进制度（WGS84）。

    `ref` 为 S / W 时取负：EXIF 用**无符号**的度分秒加一个方向字符表示半球，
    丢掉 ref 会把南半球、西半球的照片整体投到东北半球去。
    """
    if not isinstance(values, (tuple, list)) or len(values) < 3:
        return None

    deg = _to_float(values[0])
    minute = _to_float(values[1])
    second = _to_float(values[2])
    if deg is None or minute is None or second is None:
        return None

    decimal = abs(deg) + minute / 60.0 + second / 3600.0
    if isinstance(ref, str) and ref.strip().upper() in ("S", "W"):
        return -decimal
    return decimal


def _parse_exif_datetime(raw: object) -> datetime | None:
    """EXIF 时间字符串 → **naive** datetime（它表示的是拍摄地墙钟时间）。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, _EXIF_DATETIME_FORMAT)
    except ValueError:
        # 见过 "0000:00:00 00:00:00" 这种相机没设时间的填充值
        return None


def _parse_utc_offset(raw: object) -> timedelta | None:
    """`"+08:00"` / `"-05:00"` → timedelta；格式不对一律 None（视为未知）。"""
    if not isinstance(raw, str):
        return None
    match = _OFFSET_PATTERN.match(raw.strip())
    if match is None:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return -delta if sign == "-" else delta


def _jsonable(value: object) -> Any:
    """EXIF 值 → 可 JSON 序列化的值（`exif_json` 要写进 JSONB 列）。"""
    if isinstance(value, IFDRational):
        return float(value)
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_exif(data: bytes) -> PhotoMeta:
    """图片字节 → `PhotoMeta`（纯函数：不碰文件系统、不碰数据库、不落盘）。

    ## 无时区信息时 `taken_at` 怎么存（本模块最重要的一处取舍）

    EXIF 的 `DateTimeOriginal` 是**拍摄地的墙钟时间**，相机不记录时区；
    只有当 `OffsetTimeOriginal`（tag 0x9011）存在时，才能把它换算成
    可信的 UTC 时刻。偏移量缺失时，真实 UTC 时刻**无法可靠推断**：

      - 拿"拍摄地经纬度"反查时区需要一份时区边界数据库（tz 边界还随时间变化），
        这是本阶段不打算引入的重量级依赖；
      - 拿"导入时的系统时区"去补更糟：出差、旅拍、事后补导的照片会被整体错开
        好几个小时，而且错得**静悄悄**——没有任何日志会提示你它猜过。

    所以这里的落点是：

      - `taken_at` 退化为 `naive.replace(tzinfo=UTC)`，即"把当地时刻当 UTC 存"：
        它是一个**可比较、可排序**的时间戳，绝不是真实的 UTC 时刻；
      - `tz_name = None` 且 `tz_source = "unknown"`，把这个不可信状态**显式写进库里**，
        让下游（地图相册、行程聚类）能一眼看出来，而不是靠猜；
      - `taken_at_local` 始终保留原始的当地墙钟，因此**按天聚合不受这个退化影响**。

    换句话说：宁可让 `taken_at` 明确地"不精确"，也不让它"看起来精确但实际错了"。

    Raises:
        PhotoImportError: 字节不是 Pillow 能解码的图片。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            # 不 load() 的话，损坏文件要拖到后面取 exif 时才炸，报错信息会离现场很远
            img.load()
            width, height = img.size
            exif = img.getexif()
    except Exception as exc:  # noqa: BLE001 - 边界：任何解码失败都归一成 PhotoImportError
        raise PhotoImportError(f"无法解析图片：{exc}") from exc

    # ---------------------------------------------------------------- 位置
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo) if exif else {}
    lat = _dms_to_decimal(gps.get(2), gps.get(1))
    lng = _dms_to_decimal(gps.get(4), gps.get(3))
    # 只要有一半解析不出来就整体放弃：半个坐标比没有坐标更危险，
    # 它会被地图当成 (0, 0) 附近的合法点，落在大西洋几内亚湾里。
    has_gps = lat is not None and lng is not None
    if not has_gps:
        lat = lng = None

    # ---------------------------------------------------------------- 时间
    raw_datetime = (
        exif.get(_TAG_DATETIME_ORIGINAL)
        or exif.get(_TAG_DATETIME_DIGITIZED)
        or exif.get(_TAG_DATETIME)
    )
    local_naive = _parse_exif_datetime(raw_datetime)
    raw_offset = exif.get(_TAG_OFFSET_TIME_ORIGINAL)
    utc_offset = _parse_utc_offset(raw_offset)

    if local_naive is not None and utc_offset is not None:
        # 当地墙钟 - 偏移 = UTC（偏移为 -05:00 时即 +5h）
        taken_at: datetime | None = local_naive.replace(tzinfo=UTC) - utc_offset
        tz_name = f"UTC{raw_offset.strip()}" if isinstance(raw_offset, str) else None
        tz_source = TZ_SOURCE_EXIF
    elif local_naive is not None:
        taken_at = local_naive.replace(tzinfo=UTC)
        tz_name = None
        tz_source = TZ_SOURCE_UNKNOWN
    else:
        taken_at = None
        tz_name = None
        tz_source = TZ_SOURCE_UNKNOWN

    # ------------------------------------------------------------ 原始 EXIF
    exif_json: dict[str, Any] = {}
    for key, tag in (
        ("DateTimeOriginal", _TAG_DATETIME_ORIGINAL),
        ("OffsetTimeOriginal", _TAG_OFFSET_TIME_ORIGINAL),
        ("DateTimeDigitized", _TAG_DATETIME_DIGITIZED),
        ("DateTime", _TAG_DATETIME),
        ("Make", _TAG_MAKE),
        ("Model", _TAG_MODEL),
    ):
        value = exif.get(tag)
        if value is not None:
            exif_json[key] = _jsonable(value)
    if has_gps:
        exif_json["GPSLatitude"] = lat
        exif_json["GPSLongitude"] = lng
        exif_json["GPSLatitudeRef"] = _jsonable(gps.get(1))
        exif_json["GPSLongitudeRef"] = _jsonable(gps.get(3))

    return PhotoMeta(
        sha256=sha256_bytes(data),
        width=width,
        height=height,
        taken_at=taken_at,
        taken_at_local=local_naive,
        tz_name=tz_name,
        tz_source=tz_source,
        lat=lat,
        lng=lng,
        coord_system=COORD_SYSTEM_WGS84,
        location_source=LOCATION_SOURCE_EXIF if has_gps else LOCATION_SOURCE_NONE,
        location_confidence=(
            LOCATION_CONFIDENCE_EXIF if has_gps else LOCATION_CONFIDENCE_NONE
        ),
        exif_json=exif_json,
    )


def read_photo(path: str | Path) -> PhotoMeta:
    """读本地文件的字节后解析 EXIF。"""
    return parse_exif(Path(path).read_bytes())


def to_photo_record(
    user_id: uuid.UUID,
    meta: PhotoMeta,
    *,
    platform: str = "web",
    asset_id: str | None = None,
    local_thumb_path: str | None = None,
) -> Photo:
    """`PhotoMeta` → `Photo` 行（**纯函数，不 add 也不 commit**）。

    提交时机交给调用方：批量导入时攒一批再 flush，比每张一提交快一个数量级。

    `asset_id` 缺省取 sha256 前 32 位：Web 端没有平台侧稳定标识，
    就用品内容指纹凑一个（列上有 `unique(platform, asset_id)`，必须非空）。
    """
    return Photo(
        user_id=user_id,
        platform=platform,
        asset_id=asset_id or meta.sha256[:32],
        hash=meta.sha256,
        local_thumb_path=local_thumb_path,
        lat=meta.lat,
        lng=meta.lng,
        coord_system=COORD_SYSTEM_WGS84,
        location_source=meta.location_source,
        location_confidence=meta.location_confidence,
        taken_at=meta.taken_at,
        taken_at_local=meta.taken_at_local,
        tz_name=meta.tz_name,
        tz_source=meta.tz_source,
        exif_json=meta.exif_json,
        # ai_status 不显式传：由列的 default=AI_PENDING 在 INSERT 时补齐
    )


async def import_photo(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    path: str | Path,
    platform: str = "web",
) -> tuple[Photo, bool]:
    """导入一张本地照片，返回 `(photo, 是否新建)`。

    去重键是**内容指纹**而不是 `(platform, asset_id)` 唯一约束：后者只能挡住
    "同一平台重复导入"，挡不住"同一张照片从手机和电脑各导一次"。

    Raises:
        PhotoImportError: `photo_import` 开关关闭，或文件不是合法图片。
    """
    if not is_enabled("photo_import"):
        raise PhotoImportError("照片导入功能未启用（PHOTO_IMPORT_ENABLED=true 可开启）")

    meta = read_photo(path)
    stmt = select(Photo).where(Photo.user_id == user_id, Photo.hash == meta.sha256)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        logger.debug("照片已存在，跳过：sha256=%s", meta.sha256[:12])
        return existing, False

    photo = to_photo_record(user_id, meta, platform=platform)
    session.add(photo)
    # flush 而不是 commit：事务边界归调用方（API 层 / 批处理任务）
    await session.flush()
    return photo, True


@dataclass(frozen=True)
class ImportedPhotoRecord:
    """批量导入里的一张照片，以及它是不是本次新建的。

    为什么要连"是否新建"一起返回：`imported` / `duplicates` 两个计数不能由
    API 层再数一遍（数漏就是静默的账实不符），也不能让 API 层去比对对象身份
    （`Photo` 没有可比的 `__eq__`）。把它作为结果的一部分产出，计数就只有一个来源。
    """

    photo: Photo
    is_new: bool


@dataclass(frozen=True)
class BatchImportResult:
    """一次批量导入的结果。

    `records` **保持输入顺序**：API 层按这个顺序回 `photos`，
    前端就能把响应里的第 n 项对上它上传的第 n 个文件。
    换成"先新建后重复"的顺序，接口看起来更整齐，但响应与请求的位置对应关系就没了。
    """

    records: tuple[ImportedPhotoRecord, ...]
    failed: int

    @property
    def imported(self) -> int:
        """本次新建的条数（三个计数器之一，与另两个互斥且完备）。"""
        return sum(1 for record in self.records if record.is_new)

    @property
    def duplicates(self) -> int:
        """命中去重的条数（库里已有，或本批次内已经出现过）。"""
        return sum(1 for record in self.records if not record.is_new)

    @property
    def photos(self) -> tuple[Photo, ...]:
        """本次涉及的全部照片（含去重命中的旧记录），顺序与输入一致。"""
        return tuple(record.photo for record in self.records)


async def import_photo_batch(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    payloads: Sequence[bytes],
    platform: str = "web",
) -> BatchImportResult:
    """批量导入照片字节（**不清空、不删除任何既有数据**）。

    ## 为什么解析失败只计数不抛出

    一批 200 张里混着 30 张截图和损坏文件是常态。若第一张失败就整批抛出，
    用户就永远导不完那剩下的 170 张 —— 而他已经等了这次上传的全部时间。
    所以单张失败**就地记一笔 warning 并计入 `failed`**，整批继续。
    失败的细节进日志（按指纹前缀定位），响应的体积留给数量。

    ## 为什么去重要查两处（库里 + 本批次内）

    只查库里会漏掉"同一批次里放了两份"：第二份在 flush 之前库里查不到，
    插入时会在 `unique(platform, asset_id)` 上撞车，把整批事务炸掉 ——
    用户上传了两个同名文件，结果是 500，而不只是少导一张。
    所以本批次内已插入的指纹也要记一笔。

    ## 为什么既有记录的查询是**一次**而不是每张一次

    批量导入的往返次数必须是 O(1) 而不是 O(N)：一次
    `WHERE user_id = ? AND hash IN (...)` 拿全部已有行，
    再在 Python 里做字典查找。N 张照片 N 次往返在几千张的量级上
    会把导入时间从秒级拖到分钟级。

    Raises:
        PhotoImportError: `photo_import` 开关关闭。
    """
    if not is_enabled("photo_import"):
        raise PhotoImportError("照片导入功能未启用（PHOTO_IMPORT_ENABLED=true 可开启）")

    # ------------------------------------------------------------ 逐张解析
    metas: list[PhotoMeta] = []
    failed = 0
    for payload in payloads:
        try:
            metas.append(parse_exif(payload))
        except PhotoImportError as exc:
            failed += 1
            logger.warning("照片解析失败，已跳过：%s", exc)

    # ------------------------------------------------ 一次查出库里已有的行
    existing: dict[str, Photo] = {}
    fingerprints = [meta.sha256 for meta in metas]
    if fingerprints:
        rows = (
            await session.execute(
                select(Photo).where(
                    Photo.user_id == user_id, Photo.hash.in_(fingerprints)
                )
            )
        ).scalars().all()
        existing = {row.hash: row for row in rows if row.hash is not None}

    # ---------------------------------------------------------- 去重后插入
    records: list[ImportedPhotoRecord] = []
    inserted: dict[str, Photo] = {}
    for meta in metas:
        hit = existing.get(meta.sha256) or inserted.get(meta.sha256)
        if hit is not None:
            records.append(ImportedPhotoRecord(photo=hit, is_new=False))
            continue
        photo = to_photo_record(user_id, meta, platform=platform, asset_id=meta.sha256)
        session.add(photo)
        inserted[meta.sha256] = photo
        records.append(ImportedPhotoRecord(photo=photo, is_new=True))

    if inserted:
        # 一次 flush 拿全部 id，而不是每张 flush 一次
        await session.flush()

    return BatchImportResult(records=tuple(records), failed=failed)


__all__ = [
    "BatchImportResult",
    "COORD_SYSTEM_WGS84",
    "LOCATION_CONFIDENCE_EXIF",
    "LOCATION_CONFIDENCE_NONE",
    "LOCATION_CONFIDENCE_USER",
    "LOCATION_SOURCE_EXIF",
    "LOCATION_SOURCE_NONE",
    "LOCATION_SOURCE_USER",
    "TZ_SOURCE_EXIF",
    "TZ_SOURCE_UNKNOWN",
    "ImportedPhotoRecord",
    "PhotoImportError",
    "PhotoMeta",
    "import_photo",
    "import_photo_batch",
    "parse_exif",
    "read_photo",
    "sha256_bytes",
    "to_photo_record",
]
