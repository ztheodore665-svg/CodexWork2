"""照片的**导入 / 查询 / 坐标补标**（W6-1 导入 + 阶段 7 的照片库）。

## 为什么是 multipart 而不是 base64 JSON

前端手里拿的是 `File` / `Blob`（相册选择或拖拽进来的原文件），
要塞进 JSON 就得先 base64 一遍，凭白多出 33% 体积和一次编码开销 ——
而照片是本项目体积最大的输入。multipart 是浏览器上传文件的原生形式，
`fetch` 直接 append 进 `FormData` 即可，后端拿到的就是原始字节。

代价是要装 `python-multipart`（见 `requirements.txt`）：
没有它 FastAPI **在进入 handler 之前**就抛
`Form data requires "python-multipart" to be installed`，
连 500 都拿不到业务堆栈，所以它是本接口的硬依赖而不是可选优化。

## 为什么 EXIF 在后端解析

前端拿不到 EXIF：浏览器为隐私默认剥离 `File` 里的大部分元数据，
`FileReader` 读到的字节与用户磁盘上的不同（iOS Safari 尤其明显）。
所以 GPS 与时间只能由后端从**原始字节**里挖 ——
这正是 `services/photo_import.parse_exif()` 存在的理由。

## 为什么开关关闭时返回 404 而不是 403

PROJECT.md §二、[8] 要求：阶段旁路开关默认全关。
未启用的能力对外应当表现为**不存在**（404），而不是"存在但你没权限"（403）。
403 会告诉外部"这里确有此功能"，等于把未来的攻击面先画在地图上。
（同一取舍见 `app/api/v1/privacy.py`。）

## 为什么导入完**不再**自动聚类（阶段 7 的架构调整）

照片是唯一的物理事实，行程只是用户手动组建的**视图**。
原先"导入 → 立刻全量重建行程"的做法有三个问题：

  1. **用户没有发言权**。他导入 3 张照片，后端按时间断层切出 2 趟，
     其中一趟只有 1 张 —— 而他知道那 3 张是同一次出游。自动聚类
     只能按算法看得见的信号（时间、坐标）猜，猜错时用户无法纠正。
  2. **重建会冲掉手工整理**。`rebuild_user_trips()` 按 `source == cluster`
     整批删了再插，用户手动挑进某趟的照片会被一起抹掉。
  3. **导入变重**。每次上传都要对全库照片跑一遍聚类，
     照片上千后这个接口会越来越慢，而用户只是想"存几张图"。

所以现在导入只做一件事：**写 `Photo` 表**。行程由用户显式创建、显式挑照片
（`POST /api/v1/trips/{id}/photos`），`trip_cluster` 仍然保留在服务层，
需要时可以由人类主动触发（`make recluster` 之类的入口），只是不再挂在导入上。

`ImportPhotosResult.trip_ids` 因此一并删除 —— 留一个恒为 `[]` 的字段，
前端就会继续写"导入后刷新行程"的死代码。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import ValidationError

from app.api.deps import ensure_flag
from app.db.session import SessionFactory
from app.models.photo import Photo
from app.schemas.trip import (
    ImportedPhoto,
    ImportPhotosResult,
    PhotoListItem,
    PhotoLocationUpdate,
    iso_or_none,
)
from app.services.photo_import import import_photo_batch
from app.services.photo_library import list_user_photos, update_photo_location
from app.services.trip_service import semantic_tag_for_photo
from app.services.user_service import get_or_create_demo_user

router = APIRouter(prefix="/photos", tags=["photos"])

#: Web 端没有平台侧稳定标识，`asset_id` 用内容指纹顶上（见 `to_photo_record`）
_PLATFORM_WEB = "web"

#: 照片库单次返回的上限。见 `photo_library.list_user_photos` 的说明
_PHOTO_LIST_LIMIT = 500


def _to_view(photo: Photo) -> ImportedPhoto:
    """`Photo` 行 → 导入回执契约（时间字段在这里折成 ISO 字符串）。

    `hash` 列可空（`Photo.hash` 是 `str | None`），而契约里它是必填的字符串：
    本接口的照片全部由 `to_photo_record` 造出来，指纹一定有值，
    所以这里的 `or ""` 只是类型上的收口，不是业务上的兜底路径。
    """
    tag, label = semantic_tag_for_photo(photo)
    return ImportedPhoto(
        id=str(photo.id),
        asset_id=photo.asset_id,
        hash=photo.hash or "",
        taken_at=iso_or_none(photo.taken_at),
        taken_at_local=iso_or_none(photo.taken_at_local),
        tz_name=photo.tz_name,
        lat=photo.lat,
        lng=photo.lng,
        coord_system=photo.coord_system,
        location_source=photo.location_source,
        location_confidence=photo.location_confidence,
        semantic_tag=tag,
        semantic_label=label,
    )


def _to_list_item(photo: Photo) -> PhotoListItem:
    """`Photo` 行 → 照片库列表项。

    与 `_to_view` 分开而不是复用：照片库要多两个导入回执用不到的字段
    （`has_location` / `caption`），也**不要** `asset_id` 与 `semantic_*`
    （那些是导入那一刻的上下文，翻照片库时没有意义）。理由见
    `schemas/trip.py::PhotoListItem` 的 docstring。
    """
    return PhotoListItem(
        id=str(photo.id),
        hash=photo.hash or "",
        taken_at=iso_or_none(photo.taken_at),
        taken_at_local=iso_or_none(photo.taken_at_local),
        tz_name=photo.tz_name,
        lat=photo.lat,
        lng=photo.lng,
        coord_system=photo.coord_system,
        location_source=photo.location_source,
        # 两个都有才算定位：只有 lat 没有 lng 是可能的脏数据，
        # 按单个字段判会让前端渲染出一个坐标不完整的点
        has_location=photo.lat is not None and photo.lng is not None,
        location_confidence=photo.location_confidence,
        caption=photo.caption,
        created_at=iso_or_none(photo.created_at) or "",
    )


@router.post(
    "/import",
    summary="批量导入本地照片（只写 Photo 表，不自动聚类）",
    response_model=ImportPhotosResult,
)
async def import_photos(
    files: Annotated[list[UploadFile], File(description="图片文件（可重复，逐个上传）")],
) -> ImportPhotosResult:
    """接收一批照片：解析 EXIF → 按内容指纹去重 → 落库。

    单张失败（非图片 / 损坏文件）**只计入 `failed`，不中断整批**，
    也不进响应体：一批里混着几十张截图是常态，用户要的是"剩下那些导完了"，
    细节进日志、体积留给数字。

    三个计数器互斥且完备：`imported + duplicates + failed == 上传的文件数`。

    ## 为什么不重建行程

    见模块 docstring「为什么导入完不再自动聚类」。
    行程现在是用户手动组建的视图，导入这一步不产生任何行程。
    """
    # 开关未开 = 能力不存在（404，不是 403）。详见模块 docstring。
    ensure_flag("photo_import")

    payloads = [await upload.read() for upload in files]

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        result = await import_photo_batch(
            session, user_id=user.id, payloads=payloads, platform=_PLATFORM_WEB
        )

        response = ImportPhotosResult(
            imported=result.imported,
            duplicates=result.duplicates,
            failed=result.failed,
            photos=[_to_view(photo) for photo in result.photos],
        )
        # 提交时机在最后：一批照片要么全进，要么全不进
        await session.commit()

    return response


@router.get(
    "",
    summary="当前用户的照片库（按拍摄时刻倒序）",
    response_model=list[PhotoListItem],
)
async def list_photos(
    limit: Annotated[int, Query(ge=1, le=_PHOTO_LIST_LIMIT)] = _PHOTO_LIST_LIMIT,
) -> list[PhotoListItem]:
    """照片库：`Photo` 是底层数据，导入后必须能在这里看到。

    ## 为什么按 `taken_at` 倒序而不是 `created_at`

    `created_at` 是入库时间，批量导入时几乎相同（同一批事务），用它排序等于随机。
    见 `photo_library.list_user_photos` 的完整说明。

    ## 为什么没有 EXIF 时间的照片排在最后而不是丢掉

    "不知道什么时候拍的"不代表这张照片不该出现在库里 ——
    用户可能是从微信存的图，元数据全被剥掉了。排末尾 + 允许补标，
    比直接藏起来更尊重用户的照片。
    """
    ensure_flag("photo_import")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        photos = await list_user_photos(session, user_id=user.id, limit=limit)

    return [_to_list_item(photo) for photo in photos]


@router.patch(
    "/{photo_id}/location",
    summary="给照片手动补标坐标",
    response_model=PhotoListItem,
)
async def patch_photo_location(photo_id: str, body: dict) -> PhotoListItem:
    """用户在地图上点选 / 搜索地点后，把坐标写回这张照片。

    ## 为什么入参是 `dict` 而不是 `PhotoLocationUpdate`

    直接用模型会让 FastAPI 在**校验失败时**返回 422， detail 是一个
    Pydantic 错误数组（`[{"loc": ["body","lat"], "msg": ...}]`）。
    本项目前端把 `detail` 原样显示给用户，那种形状对中文界面是一堆英文路径。
    所以在这里先 `model_validate`，把错误折成一句人话再抛 ——
    `PhotoLocationUpdate` 仍然导出，测试与内部调用方照常用它。

    ## 为什么坐标范围要校验

    纬度 ±90、经度 ±180 是地理常识。放一个 999 进来，高德会静默不渲染，
    用户看到的是"补标了但地图上什么都没有"，而这种失败没有任何报错。

    ## 为什么路径里的畸形 UUID 返回 404 而不是 422

    一个畸形 id 既可能是用户手滑，也可能是有人在枚举接口 ——
    两种情况下"不存在"都是最诚实也最安全的回答。
    """
    ensure_flag("photo_import")

    try:
        payload = PhotoLocationUpdate.model_validate(body)
    except ValidationError as exc:
        # 取第一条错误的 msg：一次说一件事，比把三条字段路径一起抛给用户更可行动。
        # 用下标而不是 .get()：Pydantic 的 ErrorDetails 是 TypedDict，msg 是必填键
        errors = exc.errors()
        message = errors[0]["msg"] if errors else "取值超出范围"
        raise HTTPException(status_code=422, detail=f"坐标不合法：{message}") from exc

    try:
        pid = uuid.UUID(photo_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="照片不存在") from exc

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        photo = await update_photo_location(
            session, user_id=user.id, photo_id=pid, lat=payload.lat, lng=payload.lng
        )
        if photo is None:
            raise HTTPException(status_code=404, detail="照片不存在")
        await session.commit()

    return _to_list_item(photo)
