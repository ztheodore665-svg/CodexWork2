"""Trip / TripItem —— **派生表**（由照片聚类生成，可安全重建）。

## 为什么这两张表是「派生数据」而不是数据源

`Photo` 才是数据源（设计文档 §4）：它由 EXIF 解析产出，携带原图指纹、拍摄时刻与
坐标，删掉就再也拿不回来。而 `Trip` / `TripItem` 里的每一行，都能在几秒内由
`Photo` 表重算出来 —— 它们是照片集合在「时间断层 + 空间跳变」两条规则下的
一种**物化视图**（算法见 `services/trip_cluster.py::cluster_trips_from_photos`）。

把可以重算的东西落表，理由只有一条：**读取路径要快且稳定**。
每次打开地图相册都对几千张照片重算一遍聚类，在 Web 请求里是不可接受的；
落表之后 `GET /api/v1/trips` 只是一次带索引的单表扫描 + 一次顺序取项。

代价是必须接受一条纪律，本模块把它写死在这里：

    **重建只能删 Trip / TripItem，绝不能删 Photo。**

`services/trip_service.py::rebuild_user_trips()` 是这条纪律的唯一执行者：
它先删掉该用户全部 `Trip`（`TripItem` 随外键 CASCADE / 显式删除），
再按当前照片集重算一遍。对同一批照片跑 N 次，语义上的行程划分完全相同
（`id` 会变，因为主键由数据库生成；行程的边界与簇内顺序不变）。
**正因为这个性质，"先删后插"才是安全的**，而不是一种冒险。

## 阶段 7 之后：自动聚类已下线

从"照片为底层、行程为视图"的产品调整起，导入照片**不再触发自动聚类**：
用户自己新建行程、自己挑照片。`rebuild_user_trips()` 与
`cluster_trips_from_photos()` 因此**没有任何生产调用方**，只剩两条用途：

  - 测试里验证派生表的不变量（同一批照片重建两次，语义结果相同）；
  - 将来若要提供"按时间/空间自动整理"的显式命令，它是现成的算法入口。

读到 `source == 'cluster'` 的历史行属于**旧数据**，不是当前写入路径 ——
阶段 16 保留了该枚举值，因为删掉它就要动历史数据，而历史数据不该被代码清理牵连。

## 为什么 `TripItem` 冗余存了一份 `taken_at` / `lat` / `lng`

这三个字段在 `Photo` 上都有。冗余一份不是为了省一次 join（本阶段仍要取
`Photo.caption` 算语义标签），而是为了让行程视图的**排序与打点不依赖 Photo 现状**：

  - `ord` 是聚类算法当时得出的簇内顺序，它是一个**结论**，不是可重算的派生值。
    照片后来被用户改了时间，重跑聚类才会得到新顺序 —— 在那之前，视图必须
    继续展示当初展示给用户的那个顺序，否则地图上的连线会在没有任何操作时
    自己动一下。
  - `taken_at` 同理：行程是"那时那刻"的一段连续经历，视图展示的是聚类当时的快照。

## 为什么列名是 `ord` 而不是 `order`

`ORDER` 是 SQL 保留字，用它当列名，每个查询都得写成 `"order"`（含 ORM 里的
order_by），漏一处就是语法错误，而且报出来的位置离现场很远。
`ord` 短、无歧义、不需要引号 —— 代价只是它在 Python 里与内置函数 `ord` 重名，
而它作为类属性出现时不会与内置函数产生任何调用冲突。

## 为什么 `source` 这一列现在只用得到一半

`cluster` 与 `manual` 的区分决定重建的**作用域**：手动建的行程是用户意图
（"这是我五一的自驾游"），聚类重建时绝不能顺手把用户手动合起来的一趟拆开。
本阶段只写 `cluster`，但列必须先留好 —— 否则将来加手动行程又要再走一次迁移。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, PrimaryKey, UpdatedAt

#: 行程来源：由照片聚类自动生成（本阶段唯一取值）
TRIP_SOURCE_CLUSTER = "cluster"
#: 行程来源：用户手工创建/合并（阶段 6 之后的能力，先留位）
TRIP_SOURCE_MANUAL = "manual"


class Trip(Base):
    """一段行程（照片集合上的一个时间连续片段）。

    它是**派生数据**：由 `services/trip_service.rebuild_user_trips()` 从 `Photo` 表
    重算而来。丢了不要紧，重跑一次聚类即可 —— 所以重建时删它是安全的；
    反过来，删 `Photo` 是绝对禁止的（那是数据源）。
    """

    __tablename__ = "trip"

    id: Mapped[PrimaryKey]
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 展示用标题，形如「2026-04-12 起的旅程」
    #:
    #: 日期取自簇内最早一张的 `taken_at_local`（**当地时间**，见
    #: `services/photo_import.py` 里"按天聚合一律以 taken_at_local 为准"的取舍），
    #: 而不是 `started_at`（UTC）。理由：相机没记录时区时 `taken_at` 是把当地
    #: 墙钟当 UTC 存的退化值，它与 `taken_at_local` 的日期部分一致；
    #: 记录了时区时，只有 `taken_at_local` 才是用户心里"那天"。
    title: Mapped[str] = mapped_column(String(128), nullable=False)

    #: 簇内最早一张的拍摄时刻（UTC，tz-aware）
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 簇内最晚一张的拍摄时刻（UTC，tz-aware）
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: "cluster" | "manual"（见模块 docstring：它决定重建的作用域）
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TRIP_SOURCE_CLUSTER
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Trip {self.title} source={self.source}>"


class TripItem(Base):
    """一张照片在某个行程里的归属（含当时的时间与坐标快照）。

    `uq_trip_item_photo` 保证同一张照片在同一趟行程里只出现一次：
    重复导入若没有这条约束，会把同一张照片挂两遍，行程的照片数就会虚高，
    而且这种脏数据**不会报错**，只在地图上看出来"这趟怎么多了一张"。
    """

    __tablename__ = "trip_item"
    __table_args__ = (
        UniqueConstraint("trip_id", "photo_id", name="uq_trip_item_photo"),
    )

    id: Mapped[PrimaryKey]

    trip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trip.id", ondelete="CASCADE"), nullable=False, index=True
    )
    photo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("photo.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 以下是聚类当时的**快照**（为什么不 join Photo 取，见模块 docstring）
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    #: 簇内顺序（时间升序）。列名刻意叫 `ord`，见模块 docstring
    ord: Mapped[int] = mapped_column("ord", Integer, nullable=False, default=0)

    created_at: Mapped[CreatedAt]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<TripItem trip={self.trip_id} photo={self.photo_id} ord={self.ord}>"


__all__ = [
    "TRIP_SOURCE_CLUSTER",
    "TRIP_SOURCE_MANUAL",
    "Trip",
    "TripItem",
]
