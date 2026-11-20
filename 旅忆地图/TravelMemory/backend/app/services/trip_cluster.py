"""行程（Trip）的**规则聚类** —— 阶段 6 W6-2 的后端纯算法部分。

## 为什么是两条硬规则，而不是 DBSCAN

行程边界在工程上其实只由两件**可解释**的事定义：

1. **时间断层**：连着拍的一串照片属于同一趟；中间空了大半天，多半是回家了
2. **空间跳变**：一小时前在北京、一小时后已在上海，中间那段时间不是"待着"，是飞过去了

这两条规则**用户一眼就能看懂**：地图上这一刀切在哪里、为什么这两张照片不算一趟，
指着"隔了 13 小时"或"隔了 80 公里"就解释完了。

换成 DBSCAN 会引入 `eps` / `minPts` 两个参数，而它们：

- **不直观**：`eps=0.35` 到底是多大范围？没人有感觉，只能瞎试
- **互相耦合**：改 `eps` 会让 `minPts` 的最优值跟着变，调参是二维搜索
- **不可解释**：调出来的边界没法向用户说明"为什么你这两张照片被拆开了"

而本项目的数据量是"几十到几千张照片"。在这个量级上，DBSCAN 相对规则法
换不来任何可观测的收益，却把**可解释性**和**确定性**都赔进去了。
（真到了单用户十万张、需要发现"密度异常热点"的那天，再换成密度聚类也不迟，
那时输入规模的量级本身就说明规则法不够用了。）

## 为什么必须先排序再扫描（确定性）

聚类结果会写进 `Trip` / `TripItem` 表并参与检索。如果结果依赖数据库返回行的顺序，
同一批照片导入两次会得出两版行程，前端地图上的分组会"抖"，
用户会看到"昨天还是一趟，今天变成两趟"。

所以 `cluster_trips` 先按 `(taken_at, photo_id)` 升序排序，再做**单遍**扫描：
**输入顺序不影响输出**。排序键带上 `photo_id` 是因为同一秒连拍的照片很常见，
只按时间排会让同秒照片的顺序仍然由输入决定。

## 为什么缺坐标的照片不触发空间切分

EXIF 缺 GPS 是常态（室内、老照片、用户关了定位、截图）。
若把"没有坐标"当成"离谁都远"，一趟旅行会被随机切成许多段；
若当成"离谁都近"，又会跨城合并。两种都是**静默的错误切分**。

唯一诚实的做法是：**信息不足时就只用已有的信息**——
任一张缺坐标，这一对就只做时间判断，把空间留白留给相邻的有坐标照片去判。

## 为什么这个模块不带 feature flag

这里没有任何 IO、没有模型调用，切与不切**只由入参决定**。
开关应该放在"要不要调用它"的调用点（未来 W6-3 的导入链路），
而不是塞进纯函数里——塞进去会让同一个函数在不同开关下行为不同，单测就没法钉死。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.photo import Photo
from app.utils.coords import haversine_km


@dataclass(frozen=True)
class ClusterConfig:
    """聚类的两个阈值。

    两个字段都是**人类可直觉判断**的量（小时、公里），
    而不是 `eps` 这种需要反复试的参数 —— 详见模块 docstring。
    """

    #: 相邻两张照片时间差超过这个（小时）→ 切一刀。
    #: 默认 12h：过夜（晚上回酒店到第二天出门）会超过它，
    #: 而同一天内的正常活动间隔不会
    max_gap_hours: float = 12.0

    #: 相邻两张**都有坐标**的照片球面距离超过这个（公里）→ 切一刀。
    #: 默认 50km：同城/同景区内的移动远小于它，跨城高铁/航班远大于它
    max_gap_km: float = 50.0


@dataclass(frozen=True)
class ClusterItem:
    """参与聚类的一张照片。

    刻意只带聚类需要的四个字段（`services` 层不认识 ORM 之外的业务字段），
    这样纯算法与 `Photo` 表解耦：`Photo` 加字段不影响这里，单测也不用造 ORM 实例。
    """

    photo_id: str
    #: UTC，tz-aware。naive 会按 UTC 处理（见 `_as_utc`）
    taken_at: datetime
    lat: float | None
    lng: float | None


@dataclass(frozen=True)
class TripCluster:
    """一个行程簇：按时间先后排列的一段连续照片。"""

    #: 从 0 开始，按时间先后递增
    index: int
    #: 簇内按 `taken_at` 升序
    items: tuple[ClusterItem, ...]

    @property
    def started_at(self) -> datetime:
        """簇内最早一张的时间。"""
        return _as_utc(self.items[0].taken_at)

    @property
    def ended_at(self) -> datetime:
        """簇内最晚一张的时间。"""
        return _as_utc(self.items[-1].taken_at)

    @property
    def photo_ids(self) -> tuple[str, ...]:
        """簇内照片 id，顺序与 `items` 一致（时间升序）。"""
        return tuple(item.photo_id for item in self.items)

    @property
    def center_lat(self) -> float | None:
        """簇的纬度重心（只对有坐标的照片求均值，全无坐标则为 `None`）。"""
        return _center(self.items)[0]

    @property
    def center_lng(self) -> float | None:
        """簇的经度重心（只对有坐标的照片求均值，全无坐标则为 `None`）。"""
        return _center(self.items)[1]


def _as_utc(moment: datetime) -> datetime:
    """统一成 tz-aware UTC。

    naive 时间按 **UTC** 而不是本地时区处理：库里 `taken_at` 本来就是
    `DateTime(timezone=True)`，naive 只可能来自老数据或测试；
    若按本机时区解释，同一份数据在北京和在纽约跑会得出不同的行程切分。
    """
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _hours_between(earlier: datetime, later: datetime) -> float:
    """两个时刻相隔多少小时（允许小数；负数表示 `later` 在 `earlier` 之前）。"""
    return (_as_utc(later) - _as_utc(earlier)).total_seconds() / 3600.0


def _center(items: tuple[ClusterItem, ...]) -> tuple[float | None, float | None]:
    """有坐标照片的经纬度均值；一张都没有时返回 `(None, None)`。

    用**均值**而不是中位数/包围盒中心：地图打点要的是"这趟大概在哪"，
    均值对离群点虽敏感，但行程里真正的离群点（机场、途中）本来就该被算进来。
    """
    lats: list[float] = []
    lngs: list[float] = []
    for item in items:
        if item.lat is not None and item.lng is not None:
            lats.append(item.lat)
            lngs.append(item.lng)
    if not lats:
        return (None, None)
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def _should_cut(previous: ClusterItem, current: ClusterItem, config: ClusterConfig) -> bool:
    """`previous` 与 `current` 之间是否要切一刀。

    两个判定都是**严格大于**：阈值即"容忍上限"，正好等于阈值说明还在容忍范围内。
    """
    if _hours_between(previous.taken_at, current.taken_at) > config.max_gap_hours:
        return True
    # 任一张缺坐标 → 空间信息不足，只用时间判断（见模块 docstring）
    if previous.lat is None or previous.lng is None or current.lat is None or current.lng is None:
        return False
    return haversine_km(previous.lat, previous.lng, current.lat, current.lng) > config.max_gap_km


def cluster_trips(
    items: Sequence[ClusterItem],
    config: ClusterConfig | None = None,
) -> list[TripCluster]:
    """把照片按「时间断层 + 空间跳变」切成若干行程簇。

    纯函数：无 IO、无随机数，相同输入恒定得到相同输出（先排序再单遍扫描）。
    空输入返回 `[]`。
    """
    cfg = config or ClusterConfig()
    ordered = sorted(items, key=lambda item: (_as_utc(item.taken_at), item.photo_id))

    clusters: list[TripCluster] = []
    bucket: list[ClusterItem] = []
    previous: ClusterItem | None = None

    for item in ordered:
        if previous is None or _should_cut(previous, item, cfg):
            if bucket:
                clusters.append(TripCluster(index=len(clusters), items=tuple(bucket)))
            bucket = []
        bucket.append(item)
        previous = item
    if bucket:
        clusters.append(TripCluster(index=len(clusters), items=tuple(bucket)))
    return clusters


def cluster_trips_from_photos(
    photos: Sequence[Photo],
    config: ClusterConfig | None = None,
) -> list[TripCluster]:
    """`Photo` 序列 → 行程簇（`cluster_trips` 的 ORM 适配层）。

    单独留这个函数而不是让调用方自己拼 `ClusterItem`：
    从 `Photo` 取哪些字段、时间缺失时怎么办，这些判断只该存在一处。
    """
    items: list[ClusterItem] = []
    for photo in photos:
        # 没有拍摄时间的照片无法在时间轴上定位，只能跳过 ——
        # 它既不属于"上一趟"也不属于"下一趟"，硬塞进任何一簇都是编造
        if photo.taken_at is None:
            continue
        items.append(
            ClusterItem(
                photo_id=str(photo.id),
                taken_at=photo.taken_at,
                lat=photo.lat,
                lng=photo.lng,
            )
        )
    return cluster_trips(items, config)
