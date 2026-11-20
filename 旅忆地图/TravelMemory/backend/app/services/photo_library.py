"""照片库的**读取与坐标补标**（阶段 7：照片为底层、行程为视图）。

## 为什么单独一个模块，不塞进 `photo_import.py`

`photo_import.py` 的职责是"字节进 → Photo 行出"，它的 docstring、异常类型
（`PhotoImportError`）、测试全部围绕**导入**展开。照片库要做的是相反方向的事：
把已经落库的行读出来给人看，以及让用户事后改一个字段。
混在一起会让"导入失败该返回什么"和"补标越界该返回什么"共用一个错误类型，
而这两件事的调用方与处理方式完全不同。

## 为什么排序用 `taken_at` 而不是 `created_at`

`created_at` 是**入库时间**：一次性导入 200 张时它几乎相同（同一批事务），
用它排序等于随机。`taken_at` 是拍摄时刻，才是"翻照片"时用户期望的顺序
（最近的旅行在最上面）。

没有 EXIF 时间的照片 `taken_at` 为 null，排最后 —— 它们本来也没有
"什么时候拍的"可讲，硬塞到最前面会让用户以为刚导入的就是最新拍的。

## 为什么补标只改三个字段

`location_source` 置 `user`、`location_confidence` 置低一档（0.8 而非 EXIF 的 0.9）、
`lat` / `lng` 写新值。**不动 `coord_system`**（存储侧恒为 WGS84，前端与后端
同算法转换，见 `coords.ts`），也不动 `exif_json` —— 那是设备记录的原始事实，
用户补标是另一回事，两者混存会让"这张的坐标哪来的"再也说不清。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.photo import Photo
from app.services.photo_import import (
    COORD_SYSTEM_WGS84,
    LOCATION_CONFIDENCE_USER,
    LOCATION_SOURCE_USER,
)


async def list_user_photos(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 500,
) -> list[Photo]:
    """当前用户的照片，按拍摄时刻倒序（无时间的排在最后）。

    `deleted_at IS NULL`：软删除的行不进照片库 —— 用户已经"删了"它，
    再出现在列表里等于删除无效。

    `limit` 有默认值而不是无上限：照片库是**翻看型**界面，
    一次把几千行连缩略图路径一起送回，前端渲染和网络都要白等一轮。
    真需要更多时分页，届时再加 `offset`（现在没有调用方需要，不加）。
    """
    stmt = (
        select(Photo)
        .where(Photo.user_id == user_id, Photo.deleted_at.is_(None))
        # 两个 nulls_last：拍摄时间为空的照片排末尾，而不是最前
        .order_by(Photo.taken_at.desc().nullslast(), Photo.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def update_photo_location(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    photo_id: uuid.UUID,
    lat: float,
    lng: float,
) -> Photo | None:
    """给一张照片补标坐标。找不到（或不属于该用户）时返回 `None`。

    ## 为什么返回 None 而不是抛异常

    "照片不存在"与"这张不是你的"对调用方是同一件事，都不该让你改。
    用返回值区分比自定义异常轻，也让路由层的 404 转换只有一行。

    ## 为什么顺手改 `location_source` 与 `location_confidence`

    只写 lat/lng 而不标来源，这张照片在下游眼里与 EXIF 带 GPS 的**无法区分** ——
    而两者的置信度本该不同（设备记录 vs 用户回忆）。地理模糊的半径按置信度算，
    混淆会让"承诺半径"名不副实。

    调用方负责提交事务。
    """
    photo = await session.get(Photo, photo_id)
    if photo is None or photo.user_id != user_id:
        return None

    photo.lat = lat
    photo.lng = lng
    # 存储侧坐标系恒为 WGS84：补标拿到的就是高德 GCJ-02 的话，
    # 由**路由层**在调用本函数前转好（见 api/v1/photos.py 的说明）
    photo.coord_system = COORD_SYSTEM_WGS84
    photo.location_source = LOCATION_SOURCE_USER
    photo.location_confidence = LOCATION_CONFIDENCE_USER
    await session.flush()
    return photo


__all__ = [
    "list_user_photos",
    "update_photo_location",
]
