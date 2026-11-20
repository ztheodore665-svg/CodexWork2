"""行程的读写接口：`GET /api/v1/trips` 与 `POST /api/v1/trips`（阶段 6 收尾）。

`GET` 来自 W6-2 的导入闭环；`POST` 是把"新建旅程"按钮从**禁用**解开的那一半
（前端 `CreateTripDialog` 原来只能解释"为什么不能点"，现在能真的建出一趟空行程）。

## 为什么行程要单独有一个列表接口

地图相册打开时要的是"我有过哪几趟、每趟有哪些照片、按什么顺序"，
这是一次**成型的读取**，不是拼装出来的副产品。
`POST /api/v1/photos/import` 的响应里只有 `trip_ids`：那是导入的回执，
它的职责是告诉调用方"你这次导入影响了哪些行程"，
而不是把行程详情连着每次导入一起吐出来（每次上传都回全量行程是纯浪费）。

## 为什么它和导入接口共用 `photo_import` 这一个开关

行程是导入链路的产物：没有导入就没有照片，没有照片就没有行程。
把 `GET /trips` 和 `POST /photos/import` 挂在同一个开关下，
"这个闭环开没开"就只有一个答案，而不是两个开关各说各话。
（地图相册是前端的**展示**能力，后端不设开关。）

开关关闭时返回 **404**（能力不存在），理由与取舍写在 `api/v1/photos.py` 里。

## 为什么读接口也会"顺手创建用户"

本工程是单用户部署、没有账号体系（设计文档 §1.5），
`get_or_create_demo_user()` 是全项目取用户的统一入口（`api/v1/agent.py` 也这么干）。
空库上第一次调用会创建 demo 用户，然后返回空列表 ——
这是"确实还没有任何行程"，不是错误。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import ensure_flag
from app.db.session import SessionFactory
from app.models.trip import TripItem
from app.schemas.trip import (
    TripCreateRequest,
    TripCreateResponse,
    TripItemView,
    TripListResponse,
    TripPhotosAssign,
    TripRenameRequest,
    TripView,
    iso_or_none,
)
from app.services.trip_service import (
    TripItemSnapshot,
    TripSnapshot,
    assign_photos_to_trip,
    create_manual_trip,
    delete_user_trip,
    list_user_trips,
    remove_photo_from_trip,
    rename_user_trip,
    semantic_tag_for_photo,
)
from app.services.user_service import get_or_create_demo_user

router = APIRouter(prefix="/trips", tags=["trips"])


def _to_item_view(entry: TripItemSnapshot) -> TripItemView:
    """`TripItemSnapshot` → 出参契约（时间在这里折成 ISO 字符串）。

    时间与坐标取 `TripItem` 上的**快照**而不是实时读 `Photo`：
    视图展示的是聚类当时展示给用户的那一版，照片后来被改了时间，
    要等下次重建才反映到行程里 —— 否则地图上的连线会在没有任何操作时自己动一下。
    """
    tag, label = semantic_tag_for_photo(entry.photo)
    return TripItemView(
        id=str(entry.item.id),
        photo_id=str(entry.item.photo_id),
        taken_at=iso_or_none(entry.item.taken_at),
        lat=entry.item.lat,
        lng=entry.item.lng,
        semantic_tag=tag,
        semantic_label=label,
    )


def _to_trip_view(snapshot: TripSnapshot) -> TripView:
    """`TripSnapshot` → 出参契约。"""
    items = [_to_item_view(entry) for entry in snapshot.items]
    return TripView(
        id=str(snapshot.trip.id),
        title=snapshot.trip.title,
        started_at=iso_or_none(snapshot.trip.started_at),
        ended_at=iso_or_none(snapshot.trip.ended_at),
        source=snapshot.trip.source,
        item_count=len(items),
        items=items,
    )


@router.get("", summary="当前用户的全部行程（按开始时间升序）", response_model=TripListResponse)
async def list_trips() -> TripListResponse:
    """返回当前用户的全部行程，每个行程带它的照片列表（按簇内顺序）。

    没有照片时返回 `{"trips": []}`，而不是 404：
    "还没有行程"是**数据状态**，不是"这个接口不存在"。
    """
    # 开关未开 = 能力不存在（404，不是 403）。详见模块 docstring。
    ensure_flag("photo_import")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        snapshots = await list_user_trips(session, user_id=user.id)

    return TripListResponse(trips=[_to_trip_view(snapshot) for snapshot in snapshots])


@router.post(
    "",
    summary="手工创建一个空行程",
    response_model=TripCreateResponse,
    status_code=201,
)
async def create_trip(req: TripCreateRequest) -> TripCreateResponse:
    """创建一个还没有照片的行程（`source = "manual"`）。

    ## 为什么允许"空的"

    行程是用户心里的一段旅程，聚类只是**推导**它的一种方式。用户可以先建
    「洛阳两日」再补照片；反过来要求"先有照片才能有行程"，等于把数据模型
    绑死在某一种推导算法上 —— 而恰恰是这种要求，让 UI 上那个"新建旅程"按钮
    从一开始就是禁用的（按钮禁用却说不清什么时候能解开，比能点更让人困惑）。

    ## 为什么手工行程不会被后续导入冲掉

    即便将来重新接入自动整理，`rebuild_user_trips()` 重建时也**只删 `source == cluster` 的行**
    （见 `services/trip_service.py::_delete_user_trips` 的取舍说明）。
    用户敲下的标题是库里唯一的副本，删了就找不回来，所以这里守的是
    "派生数据可以让位，用户输入不行"这条线。

    ## 为什么剥掉标题首尾空白

    纯空白标题落库后是一个**看起来为空**的行程卡片，用户会以为"创建没生效"。
    在入口剥掉并判空，比在渲染时再处理更早也更省事。
    """
    ensure_flag("photo_import")

    title = req.title.strip()
    if not title:
        # Pydantic 的 min_length 挡不住 "   "（它是 3 个字符），只能在这里判
        raise HTTPException(status_code=422, detail="行程标题不能为空")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await create_manual_trip(
            session, user_id=user.id, title=title, started_on=req.started_on
        )
        await session.commit()

    return TripCreateResponse(
        trip=TripView(
            id=str(trip.id),
            title=trip.title,
            started_at=iso_or_none(trip.started_at),
            ended_at=iso_or_none(trip.ended_at),
            source=trip.source,
            item_count=0,
            items=[],
        )
    )


@router.patch(
    "/{trip_id}",
    summary="重命名一趟行程",
    response_model=TripCreateResponse,
)
async def rename_trip(trip_id: uuid.UUID, req: TripRenameRequest) -> TripCreateResponse:
    """改一趟行程的标题。

    ## 为什么 404 而不是 403

    找不到的行程与"不属于你"的行程对调用方是**同一件事**：都不该让你改。
    用 403 会告诉外部"这个 id 确实存在，只是不属于你"—— 那是免费的枚举接口。

    ## 为什么改名后 source 会变成 manual

    见 `services/trip_service.py::rename_trip` 的取舍说明：不改 source 的话，
    下次导入照片触发的重建会把用户敲的标题冲掉。
    """
    ensure_flag("photo_import")

    title = req.title.strip()
    if not title:
        # 同 create_trip：pydantic 的 min_length 挡不住 "   "
        raise HTTPException(status_code=422, detail="行程标题不能为空")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await rename_user_trip(session, user_id=user.id, trip_id=trip_id, title=title)
        if trip is None:
            raise HTTPException(status_code=404, detail="行程不存在")
        # 单独数一次，而不是回显 0：`item_count` 是给前端直接渲染的字段，
        # 给个假 0 会让列表里的行程卡片在改名后突然显示"0 个地点"。
        item_count = await session.scalar(
            select(func.count()).select_from(TripItem).where(TripItem.trip_id == trip.id)
        )
        await session.commit()

    return TripCreateResponse(
        trip=TripView(
            id=str(trip.id),
            title=trip.title,
            started_at=iso_or_none(trip.started_at),
            ended_at=iso_or_none(trip.ended_at),
            source=trip.source,
            item_count=int(item_count or 0),
            items=[],
        )
    )


@router.delete(
    "/{trip_id}",
    summary="删除一趟行程（不触碰照片）",
    status_code=204,
)
async def delete_trip(trip_id: uuid.UUID) -> None:
    """删掉这趟行程，**照片一条不少**。

    ## 为什么这是 204 而不是带响应体的 200

    删除成功后调用方要做的只有"把它从列表里去掉"，没有任何需要回传的值。
    带一个 `{"deleted": true}` 的响应体，前端就多一处要解析的形状，
    而那个布尔值它本来就知道（它刚发起了删除）。

    ## 为什么照片绝对安全

    `Trip` / `TripItem` 是**派生表**，`Photo` 是数据源。
    本接口只 `delete(Trip)`，`TripItem` 由 `trip_id` 外键的 `ondelete=CASCADE`
    自动带走；`photo` 表从头到尾没有任何写操作。
    这是 `models/trip.py` 模块 docstring 里那条纪律的接口层体现：
    "重跑一次聚类"与"删掉一趟行程"都绝不应该变成"照片全没了"。

    注意外键方向不要看反：`TripItem.photo_id` 的 CASCADE 是
    "删照片时带走关联"，与这里的"删行程不碰照片"是两个方向。

    ## 404 的理由同 PATCH

    找不到的与"不属于你"的对调用方是同一件事，都该让你删不了。
    用 403 会免费送一个枚举接口。
    """
    ensure_flag("photo_import")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        deleted = await delete_user_trip(session, user_id=user.id, trip_id=trip_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="行程不存在")
        await session.commit()


@router.post(
    "/{trip_id}/photos",
    summary="把一批照片关联进行程（全量快照）",
    response_model=TripCreateResponse,
)
async def assign_trip_photos(trip_id: uuid.UUID, req: TripPhotosAssign) -> TripCreateResponse:
    """把这个行程的照片集合设成目标快照。

    ## 为什么是"设为目标状态"而不是"加这几张"

    前端持有这个行程的完整照片列表，由它 diff 出该加谁该删谁。
    后端猜增量的代价是幽灵条目（"我以为加了、其实没加"），
    而这种不一致在界面上没有任何异常表现。详见
    `services/trip_service.py::assign_photos_to_trip`。

    ## 为什么响应只回一条精简行程

    调用方刚提交完整个列表，它知道自己设成了什么。
    要刷新展示就再调一次 `GET /trips` —— 把全量行程塞进保存响应，
    会让"保存成功"和"列表刷新"在前端共用一个状态位。

    ## 为什么 source 会变成 manual

    用户手动整理过的行程与聚类出来的已经是两回事，标成 manual
    让它退出 `rebuild_user_trips()` 的删除范围。代价是这趟不再随
    导入自动更新，收益是用户挑的结果不被冲掉。
    """
    ensure_flag("photo_import")

    photo_ids: list[uuid.UUID] = []
    for raw in req.photo_ids:
        try:
            photo_ids.append(uuid.UUID(raw))
        except ValueError as exc:
            # 畸形 id 当不存在处理，理由同 PATCH 的 404 取舍
            raise HTTPException(status_code=404, detail="照片不存在") from exc

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        trip = await assign_photos_to_trip(
            session, user_id=user.id, trip_id=trip_id, photo_ids=photo_ids
        )
        if trip is None:
            raise HTTPException(status_code=404, detail="行程不存在")
        await session.commit()

    return TripCreateResponse(
        trip=TripView(
            id=str(trip.id),
            title=trip.title,
            started_at=iso_or_none(trip.started_at),
            ended_at=iso_or_none(trip.ended_at),
            source=trip.source,
            item_count=len(photo_ids),
            items=[],
        )
    )


@router.delete(
    "/{trip_id}/photos/{photo_id}",
    summary="把一张照片从行程里移除（不删照片）",
    status_code=204,
)
async def remove_trip_photo(trip_id: uuid.UUID, photo_id: uuid.UUID) -> None:
    """解除这张照片与这趟行程的关联，**照片本身留在库里**。

    ## 为什么"照片本就不在这趟"也返回 204

    调用方想让它不在，结果确实是"不在"—— 目的达成。
    再分一个"本来就不在"的状态，只会让前端多写一个永远走不到的分支。

    ## 为什么不需要重算行程的起止时间

    移除一张照片不会让"这趟从哪天到哪天"变成另一个答案
    （边界由剩下的照片决定，下次保存时会重算）。为了一个
    下次就会被覆盖的中间值在这里多做一次全量扫描，不值得。
    """
    ensure_flag("photo_import")

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        removed = await remove_photo_from_trip(
            session, user_id=user.id, trip_id=trip_id, photo_id=photo_id
        )
        if not removed:
            raise HTTPException(status_code=404, detail="行程不存在")
        await session.commit()
