"""行程（Trip）的**落库与重建**（阶段 6 W6-2 的 IO 部分）。

## 算法与 IO 为什么要分家

`trip_cluster.py` 是纯函数：给它一组时间与坐标，它切出几趟，不碰数据库。
本模块只做它做不了的三件事 —— 取照片、把结果写进 `Trip` / `TripItem`、
按读取路径把结果取回来。

分家的收益是**可测试性**：切分的对与错由 `tests/test_trip_cluster.py` 在离线
钉死，本模块只需要测"读写有没有把算法的结果如实搬进库"。
混在一起的话，任何一条切分断言都得先连数据库。

## 为什么"先删后插"是安全的（本模块最重要的一条论证）

`Trip` / `TripItem` 是**派生表**：每一行的全部内容都能由 `Photo` 表在几秒内
重算出来（`models/trip.py` 的模块 docstring 有完整论证）。于是：

  - 重建 = 用当前照片集把同一个函数再跑一遍，**结果只由照片决定**；
  - 对同一批照片跑 N 次，得到的行程边界、簇内顺序、每簇照片数完全相同
    （只有 `id` 会变，因为主键由数据库生成）；
  - 因此删掉旧行不会造成"暂时看不到行程"以外的任何损失 —— 而这段窗口
    在同一次请求、同一个事务里，外部根本观察不到。

反过来，`Photo` 是数据源，**重建逻辑绝不删它**：本模块除了 `select` 之外
对 `photo` 表没有任何写操作（`rebuild_user_trips` 里删的两张表都姓 trip）。
这条纪律一旦破掉，"重跑一次聚类"就会变成"照片全没了"，那是不可恢复的事故。

## 为什么每个用户各自重建，而不是全表重建

重建的作用域是**一个用户**：行程归属用户，A 用户重新导入照片时没有理由
（也没有理由让）B 用户的行程跟着删一遍再插一遍。
按 `user_id` 收窄还有个直接的工程收益：删改范围与事务时长都只跟一个人的
照片量有关，不会因为某个用户导了两万张照片而把别人的请求拖慢。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.photo import Photo
from app.models.trip import TRIP_SOURCE_CLUSTER, TRIP_SOURCE_MANUAL, Trip, TripItem
from app.services.semantic_service import classify_place
from app.services.trip_cluster import ClusterConfig, cluster_trips_from_photos

logger = get_logger(__name__)


@dataclass(frozen=True)
class TripItemSnapshot:
    """一行 `TripItem` 加上它指向的 `Photo`。

    为什么要连 `Photo` 一起带出来：视图要给照片算语义标签，而标签的证据
    （`caption` / `ai_tags`）只存在于 `Photo` 上。一次查询把两样都取回来，
    读取路径才是**两次**查询（trips + items）而不是每行程一次。

    `photo` 允许为 None：照片理论上可能已被物理删除，
    而行程视图不该因为少一行就整趟不显示。
    """

    item: TripItem
    photo: Photo | None


@dataclass(frozen=True)
class TripSnapshot:
    """一段行程 + 它的全部条目（已按 `ord` 排好）。"""

    trip: Trip
    items: tuple[TripItemSnapshot, ...]


def semantic_tag_for_photo(photo: Photo | None) -> tuple[str | None, str | None]:
    """取一张照片的 `(语义标签, 中文名)`；无论据时为 `(None, None)`。

    用**未门控**的 `classify_place` 而不是 `semantic_tag_for_place`：
    后者受 `semantic` 开关控制、只返回 tag 不返回 label；而这里要的是展示用的
    `(tag, label)` 二元组，且 `classify_place` 是纯函数 —— 无 IO、无模型调用、
    无法命中时就交出 None。开关关着时两者都会给出 None，行为完全一致，
    所以没必要为了"看起来守规矩"去接一个会丢掉 label 的门控版。

    **导入那一刻它必然返回 (None, None)**：照片刚入库，还没有 caption
    （caption 由离线批处理 `jobs/caption_job` 补）。这是诚实的答案 ——
    猜一个标签写进阶段 7 的模式库，比少一条数据糟得多。
    """
    if photo is None:
        return (None, None)
    result = classify_place(ai_tags=photo.ai_tags, name=photo.caption)
    return (result.tag, result.label)


def _trip_title(
    cluster_start_photo_id: str,
    cluster_started_at: datetime,
    local_by_photo: dict[str, datetime | None],
) -> str:
    """行程标题：形如「2026-04-12 起的旅程」。

    日期取自簇内最早一张的 `taken_at_local`（**当地时间**）而不是 `started_at`（UTC）：
    EXIF 没记录时区时，`taken_at` 是把当地墙钟当 UTC 存的退化值
    （见 `services/photo_import.py` 的取舍说明），它与 `taken_at_local` 的日期相同；
    记录了时区时，只有 `taken_at_local` 才是用户心里的"那天"。

    `taken_at_local` 缺失时才退回 `started_at`：宁可给一个可能差几个小时的日期，
    也不给一个空标题 —— 标题缺失会让 UI 出现一个没有名字的行程卡片。
    """
    local = local_by_photo.get(cluster_start_photo_id)
    moment = local if local is not None else cluster_started_at
    return f"{moment.date().isoformat()} 起的旅程"


async def _delete_user_trips(session: AsyncSession, *, user_id: uuid.UUID) -> None:
    """删掉该用户的**聚类**行程（**只删 trip / trip_item，绝不碰 photo**）。

    为什么显式删 `trip_item` 而不是依赖外键的 ON DELETE CASCADE：
    级联是数据库的行为，而"重建会连带清掉条目"这句话应该由代码说出来。
    万一有人用 `create_all` 建了一张没有级联的表，依赖级联就会留下孤儿行，
    而且这种脏数据**不报错**，只在行程详情里表现为"照片数对不上"。

    ## 为什么只删 `source == cluster`（本函数最重要的一条约束）

    `manual` 行程是**用户意图**（"这是我五一的自驾游"），不是照片的派生结果。
    重建的作用域是"把派生数据按当前照片重算一遍"，把用户亲手建的东西一起删掉
    属于越界 —— 用户建完一趟空旅程、又导了一批照片，回头发现旅程没了，
    那是一次**不可恢复的数据丢失**（标题是他自己敲的，库里没有第二份）。

    代价是删除条件多了一个 `source` 过滤，且 `manual` 行程不参与重建后的
    照片归属：这里的选择是"保留用户意图、接受它暂时是空的"，
    而不是"为了数据整齐牺牲用户输入"。
    """
    trip_ids = select(Trip.id).where(
        Trip.user_id == user_id, Trip.source == TRIP_SOURCE_CLUSTER
    )
    await session.execute(delete(TripItem).where(TripItem.trip_id.in_(trip_ids)))
    await session.execute(
        delete(Trip).where(Trip.user_id == user_id, Trip.source == TRIP_SOURCE_CLUSTER)
    )
    await session.flush()


async def create_manual_trip(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    started_on: date | None = None,
) -> Trip:
    """手工创建一个**空**行程（`source = manual`）。

    为什么允许它是空的：`Trip` 存在的意义是"用户心里的一段旅程"，
    聚类行程只是它的一种**自动推导方式**。用户可以先建一趟「洛阳两日」、
    之后再把照片导进来 —— 反过来要求"先有照片才能有行程"，
    等于把产品的数据模型绑架在某一种推导算法上。

    两个时间字段刻意留 `null` 而不是填 `now()`：
    行程的起止是**照片决定的事实**，不是"创建时间"。填 `now()` 会让一趟
    还没开始的旅程在列表里显示为"从今天起"，而这个值将来被照片覆盖时
    又会突然跳到别的一天 —— 凭空多出一次"日期自己变了"的困惑。
    `null` 的语义是"还不知道"，这是唯一诚实的取值。

    `started_on` 是唯一的例外：从 journey 包导回时，包里的 `days[0].date`
    是**事实**而不是用户臆造的值，且缩略图没有 EXIF 可供反推。它按"当地日期
    的 00:00（UTC）"落库，只用于列表显示；照片一旦带上真实 `taken_at`，
    `assign_photos_to_trip` 会用照片的最早时间覆盖它。

    调用方负责提交事务（与 `import_photos` 的"显式管提交边界"同一套做法）。
    """
    trip = Trip(
        user_id=user_id,
        title=title,
        started_at=(
            datetime.combine(started_on, time.min, tzinfo=UTC)
            if started_on is not None
            else None
        ),
        ended_at=None,
        source=TRIP_SOURCE_MANUAL,
    )
    session.add(trip)
    # flush 拿到数据库生成的 id：调用方要把 id 回给前端，前端据此刷新列表
    await session.flush()
    return trip


async def rename_user_trip(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    title: str,
) -> Trip | None:
    """改一趟行程的标题。找不到（或不属于该用户）时返回 `None`。

    ## 为什么聚类行程也能改名

    聚合行程（`source = cluster`）的标题是后端按首张照片的日期**推导**出来的
    （见 `_trip_title`），用户大概率想把它改成「洛阳两日」这种自己能认的名字。
    所以这里**不按 source 区别对待** —— 那会让"能改名"变成一件说不清何时生效的事。

    ## 为什么改名要把 source 提升为 manual

    `rebuild_user_trips()` 重建时按 `user_id + source == cluster` **整批删了再插**
    （见 `_delete_user_trips`）。被改过名的 cluster 行正落在这个删除范围里 ——
    不改 source 的话，下次导入照片就会把用户敲的标题冲掉。
    提升为 manual 的代价是这趟行程从此不再随新导入的照片自动更新，
    收益是用户输入不丢。取舍依据同 `create_manual_trip`：
    **派生数据可以让位，用户输入不行。**

    调用方负责提交事务。
    """
    trip = await session.get(Trip, trip_id)
    if trip is None or trip.user_id != user_id:
        return None
    trip.title = title
    # 见 docstring：用户输入过的行程退出自动重建
    trip.source = TRIP_SOURCE_MANUAL
    await session.flush()
    return trip


async def rebuild_user_trips(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    config: ClusterConfig | None = None,
) -> list[Trip]:
    """用该用户当前的全部照片**重建**他的行程（幂等）。

    流程固定四步：删旧的（**只删聚类的**）→ 取照片 → 聚类 → 落库。
    `Photo` 表全程只读：它是数据源，重建无论如何都不许动它。

    返回重建后该用户的**全部**行程（手工的 + 新建的），顺序与
    `list_user_trips` 完全一致 —— 导入回执里的 `trip_ids` 因此恒等于
    `GET /api/v1/trips` 的 id 序列，前端不需要处理"两处顺序不一样"。

    只返回新建的那几条其实更省事，但那样导入回执与列表接口会给出**不同的答案**，
    而这种不一致不会报错，只在"导入后列表看起来少了一趟"时被人发现。

    没有照片时不清空旧行程吗？**聚类的会清空**。照片被删到一张不剩，聚类行程
    也就失去了存在的依据；留着它们的结果是"能看到行程但点进去是空的"，
    不如让视图与数据源保持一致。手工行程不受影响（见 `_delete_user_trips`）。
    """
    await _delete_user_trips(session, user_id=user_id)

    photos = (
        await session.execute(
            select(Photo).where(Photo.user_id == user_id, Photo.deleted_at.is_(None))
        )
    ).scalars().all()

    clusters = cluster_trips_from_photos(photos, config)

    # 标题要用当地时间，而 `ClusterItem` 只带 UTC —— 这里补一张 id → 当地的映射
    local_by_photo = {str(photo.id): photo.taken_at_local for photo in photos}

    trips: list[Trip] = []
    for cluster in clusters:
        trip = Trip(
            user_id=user_id,
            title=_trip_title(cluster.items[0].photo_id, cluster.started_at, local_by_photo),
            started_at=cluster.started_at,
            ended_at=cluster.ended_at,
            source=TRIP_SOURCE_CLUSTER,
        )
        session.add(trip)
        # flush 拿 id：下面每条 TripItem 都要引用它
        await session.flush()

        for position, item in enumerate(cluster.items):
            session.add(
                TripItem(
                    trip_id=trip.id,
                    photo_id=uuid.UUID(item.photo_id),
                    taken_at=item.taken_at,
                    lat=item.lat,
                    lng=item.lng,
                    ord=position,
                )
            )
        trips.append(trip)

    if trips:
        await session.flush()

    # 统一回读一次：手工行程也要出现在结果里，且顺序必须与 list_user_trips 一致
    # （同一个 order_by 表达式写两遍容易走样，所以两边用同一串排序键）
    return list(
        (
            await session.execute(
                select(Trip)
                .where(Trip.user_id == user_id)
                .order_by(Trip.started_at.asc().nulls_last(), Trip.id.asc())
            )
        ).scalars().all()
    )


async def assign_photos_to_trip(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    photo_ids: list[uuid.UUID],
) -> Trip | None:
    """把行程的照片集合设成目标快照（全量提交，不是增量 add）。

    ## 为什么是"设为目标状态"而不是"加这几张"

    前端持有这个行程的完整照片列表，它去 diff 出"该加谁该删谁"。
    后端只负责把结果落成目标状态 —— 猜增量的代价是幽灵条目：
    "我以为加了、其实没加"，而这种不一致在界面上没有任何异常表现，
    只在地图上表现为"这趟怎么多了一张"。

    ## 为什么照片可以来自别的行程

    一张照片同时属于两趟行程是**合法的**（用户在两个视图里都想看到它）。
    所以这里不检查 photo 是否已被别的行程占用，只按 `photo_id` 建立关联。

    ## 为什么顺手刷 `source`

    用户手动整理过的行程与聚类出来的已经是两回事，标成 `manual`
    让它退出 `rebuild_user_trips()` 的删除范围（那条只删 `cluster`）。
    否则下一次重建会把这些手动挑选的结果冲掉。

    ## 为什么照片不存在时跳过而不是报错

    调用方传来一个已删除的 photo_id 时，报 422 会让整个保存动作失败 ——
    用户只是想"把这趟存成现在这样"，不该因为列表里混了一条过期 id 就存不了。
    跳过 + 在日志里记下，比让用户重来一次更合理。

    调用方负责提交事务。
    """
    trip = await session.get(Trip, trip_id)
    if trip is None or trip.user_id != user_id:
        return None

    # 只保留真实存在且属于该用户的照片（顺序即传入顺序，前端已按需排好）
    valid = (
        (
            await session.execute(
                select(Photo.id).where(
                    Photo.id.in_(photo_ids), Photo.user_id == user_id, Photo.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    valid_set = {row for row in valid}
    dropped = [str(pid) for pid in photo_ids if pid not in valid_set]
    if dropped:
        logger.warning("assign_photos: 跳过 %d 个不存在/已删除的照片: %s", len(dropped), dropped)

    # 先删后插：与 rebuild_user_trips 同一套做法。trip_item 是**关联表**，
    # 它的全部内容都能由 (trip_id, photo_ids) 重算，删掉不损失任何信息。
    await session.execute(delete(TripItem).where(TripItem.trip_id == trip.id))

    photos = (
        (
            await session.execute(
                select(Photo).where(
                    Photo.id.in_(photo_ids), Photo.user_id == user_id, Photo.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {photo.id: photo for photo in photos}
    for position, photo_id in enumerate(photo_ids):
        photo = by_id.get(photo_id)
        if photo is None:
            continue
        session.add(
            TripItem(
                trip_id=trip.id,
                photo_id=photo.id,
                # 快照语义与聚类落库一致：存当时的值，而不是 join Photo 现取。
                # 用户补标坐标后，行程里的旧值要等他重新保存才更新 ——
                # 否则"地图上的点在自己没动的时候跳了一下"。
                taken_at=photo.taken_at,
                lat=photo.lat,
                lng=photo.lng,
                ord=position,
            )
        )

    trip.source = TRIP_SOURCE_MANUAL
    # **不要用 None 覆盖已知日期**：导入的缩略图没有 EXIF，`_earliest(photos)`
    # 恒为 None；如果无条件赋值，`create_manual_trip(started_on=...)` 刚写进去的
    # 行程日期会被紧接着的挂照片动作抹掉 —— 这正是阶段 15 那条 P1 的另一半。
    earliest = _earliest(photos)
    latest = _latest(photos)
    if earliest is not None:
        trip.started_at = earliest
    if latest is not None:
        trip.ended_at = latest
    await session.flush()
    return trip


async def remove_photo_from_trip(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    photo_id: uuid.UUID,
) -> bool:
    """把一张照片从行程里移除（只删 `TripItem` 关联，**绝不碰 `photo` 表**）。

    返回 False 表示行程不存在 / 不属于该用户 / 该照片本就不在这趟行程里。
    第三种情况也返回 False：调用方想让它不在，结果确实是"不在"，
    再分一个"本来就不在"的状态只会让前端多写一个用不上的分支。

    调用方负责提交事务。
    """
    trip = await session.get(Trip, trip_id)
    if trip is None or trip.user_id != user_id:
        return False

    # 先查在不在：既避开 `Result.rowcount` 在类型桩上的归属问题，
    # 也让"返回 False 的三种情况"有一个统一的判据。
    existing = await session.execute(
        select(TripItem.id).where(TripItem.trip_id == trip.id, TripItem.photo_id == photo_id)
    )
    if existing.scalar_one_or_none() is None:
        return False

    await session.execute(
        delete(TripItem).where(TripItem.trip_id == trip.id, TripItem.photo_id == photo_id)
    )
    await session.flush()
    return True


async def delete_user_trip(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
) -> bool:
    """删掉一趟行程（连同它的 `TripItem` 关联），**绝不触碰 `photo` 表**。

    ## 这是本模块最重要的一条纪律

    `Trip` / `TripItem` 是**派生表**：它们的全部内容都能由 `Photo` 重算。
    `Photo` 是数据源，删了不可恢复。所以本函数只 `delete(Trip)` ——
    `TripItem` 由 `trip_id` 外键的 `ondelete="CASCADE"` 自动带走，
    而 `photo` 表从头到尾没有任何写操作。

    `models/trip.py` 里 `photo_id` 的外键也是 `CASCADE`，方向是
    "删照片时带走关联"，与这里的"删行程不碰照片"是两个方向，不要看反。

    调用方负责提交事务。
    """
    trip = await session.get(Trip, trip_id)
    if trip is None or trip.user_id != user_id:
        return False

    await session.delete(trip)
    await session.flush()
    return True


def _earliest(photos: Sequence[Photo]) -> datetime | None:
    """一批照片里最早的拍摄时刻；全都没时间时为 None。"""
    values = [photo.taken_at for photo in photos if photo.taken_at is not None]
    return min(values) if values else None


def _latest(photos: Sequence[Photo]) -> datetime | None:
    """一批照片里最晚的拍摄时刻；全都没时间时为 None。"""
    values = [photo.taken_at for photo in photos if photo.taken_at is not None]
    return max(values) if values else None


async def list_user_trips(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[TripSnapshot]:
    """取该用户的全部行程（按 `started_at` 升序）及各自行程条目。

    两次查询（trips + items + photos 合成两次）：行程数与照片数都可能有几十条，
    每条一次查询就是典型的 N+1，在地图相册打开一次要几十个往返。

    `items` 的顺序由 `ord` 保证而不是由数据库行序保证：
    行序没有语义，同一趟的条目换个顺序就是一条乱掉的行程路线。
    """
    trips = (
        await session.execute(
            select(Trip)
            .where(Trip.user_id == user_id)
            .order_by(Trip.started_at.asc().nulls_last(), Trip.id.asc())
        )
    ).scalars().all()
    if not trips:
        return []

    trip_ids = [trip.id for trip in trips]
    rows: Sequence[TripItem] = (
        await session.execute(
            select(TripItem)
            .where(TripItem.trip_id.in_(trip_ids))
            .order_by(TripItem.trip_id.asc(), TripItem.ord.asc())
        )
    ).scalars().all()

    photos = await _photos_by_id(session, [row.photo_id for row in rows])

    grouped: dict[uuid.UUID, list[TripItemSnapshot]] = {trip.id: [] for trip in trips}
    for row in rows:
        grouped[row.trip_id].append(TripItemSnapshot(item=row, photo=photos.get(row.photo_id)))

    return [
        TripSnapshot(trip=trip, items=tuple(grouped[trip.id])) for trip in trips
    ]


async def _photos_by_id(
    session: AsyncSession, photo_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Photo]:
    """批量取照片（一次 IN 查询）。空 id 列表直接返回空字典，不发起查询。"""
    if not photo_ids:
        return {}
    rows = (
        await session.execute(select(Photo).where(Photo.id.in_(set(photo_ids))))
    ).scalars().all()
    return {row.id: row for row in rows}


__all__ = [
    "TripItemSnapshot",
    "TripSnapshot",
    "assign_photos_to_trip",
    "create_manual_trip",
    "delete_user_trip",
    "list_user_trips",
    "rebuild_user_trips",
    "remove_photo_from_trip",
    "rename_user_trip",
    "semantic_tag_for_photo",
]
