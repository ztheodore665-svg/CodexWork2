"""阶段 6 导入闭环的**出参契约**（与 ORM 严格分离）。

## 为什么时间字段的类型是 `str` 而不是 `datetime`

后端这些字段在 ORM 里是 `datetime`（tz-aware UTC），直接 `model_dump()` 交给
FastAPI 也能被 JSON 编码，但编码出来的格式依赖 pydantic 版本与 `json_encoders`
配置（是 `2026-04-12T09:31:05Z` 还是 `2026-04-12T09:31:05+00:00`、带不带毫秒，
都可能随版本变）。

所以契约层**自己**把 `datetime` 折成 ISO 字符串（见 `iso_or_none`），
字段类型直接声明成 `str`：

  - 前端拿到的是能直接喂给 `new Date(...)` 与地图轴的字符串，只需解析一种格式；
  - 哪天 pydantic 改了默认编码，变的是契约层内部的一行，不是前端的解析逻辑；
  - 契约类型就是真正在网络上传输的类型 —— 看 schema 就知道线上会收到什么，
    不用去猜序列化器做了什么。

代价是 API 层多几处 `iso_or_none(...)` 调用。相对于"前后端对时间格式各猜一半"，
以及"类型声明与实际传输的不一致"，这个代价很划算。

## 为什么 `semantic_tag` 允许为 None

导入那一刻照片还没有 caption（`caption` 由离线批处理 `jobs/caption_job` 补），
`classify_place()` 拿不到任何证据，只能诚实地返回 None。
**"没有语义"而不是"猜一个语义"**：猜出来的标签会写进阶段 7 的模式库，
那是比缺一条数据糟得多的事（见 `services/semantic_service.py` 模块 docstring）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


def iso_or_none(value: datetime | None) -> str | None:
    """tz-aware datetime → ISO 8601 字符串。

    `None` 原样穿过而不是变成 `""` 或 `"null"`：**"没有"和"空"是两件事**。
    缺 GPS 的照片 `lat` 是 None，缺时间的照片 `taken_at` 也是 None，
    前端据此决定"这张不打点 / 不参与排序"；拿到空串只能靠猜。
    """
    return None if value is None else value.isoformat()


class ImportedPhoto(BaseModel):
    """一张被导入（或命中去重）的照片，导入接口的最小回执。

    只回**元数据**，不回原图字节：原图始终留在设备上（设计文档 §4），
    接口能给出的最强承诺就是"库里有这一条记录了"。

    `taken_at` 与 `taken_at_local` 两个都给，是因为它们各有用途：
    前者用来排序（UTC）、后者用来按天聚合（当地墙钟）。
    前端若只要一个，该用哪个是业务判断，不该由后端替它决定。
    """

    id: str = Field(description="Photo 主键（UUID 字符串）")
    asset_id: str = Field(description="平台侧稳定标识（Web 端即为内容指纹）")
    hash: str = Field(description="内容指纹 sha256 hex，导入链路的去重键")

    taken_at: str | None = Field(default=None, description="拍摄时刻（UTC，ISO 8601）")
    taken_at_local: str | None = Field(
        default=None, description="拍摄地当地墙钟时间（ISO 8601，无时区）——按天聚合一律用它"
    )
    tz_name: str | None = Field(default=None, description="形如 UTC+08:00；时区未知时为 null")
    lat: float | None = Field(default=None, description="WGS84 纬度；无 GPS 时为 null")
    lng: float | None = Field(default=None, description="WGS84 经度；无 GPS 时为 null")
    coord_system: str = Field(default="WGS84", description="坐标系，存储侧恒为 WGS84")
    location_source: str | None = Field(default=None, description="位置来源：exif / user / none")
    location_confidence: float | None = Field(
        default=None, description="位置置信度：EXIF 0.9，用户补标 0.8，无位置 0.0"
    )
    semantic_tag: str | None = Field(default=None, description="六类语义标签之一；无证据时为 null")
    semantic_label: str | None = Field(default=None, description="语义标签的中文名")


class PhotoListItem(BaseModel):
    """照片库里的一行（`GET /api/v1/photos`）。

    ## 为什么不复用 `ImportedPhoto`

    导入回执描述的是"**这一次导入**发生了什么"，照片库描述的是"**库里都有什么**"。
    两者的消费方不同：前者读完就丢，后者要长期展示并支持后续操作。
    硬合成一个类型，将来给照片库加"所属行程数""是否已生成 caption"这类字段时，
    会把导入回执一起撑大 —— 而回执是一次性的，撑大它纯属浪费。

    ## `has_location` 为什么单独一个布尔字段

    前端要的是"这张要不要显示未定位标签"，而不是拿 `lat` 判空再自己推一遍。
    更关键的是：**只有 lat 没有 lng**（或反过来）是可能的脏数据，
    `lat != null` 会让前端以为有坐标、渲染时又拿不到点。
    后端按"两个都有"判定，前端拿一个布尔值就行。
    """

    id: str = Field(description="Photo 主键（UUID 字符串）")
    hash: str = Field(description="内容指纹 sha256 hex")
    taken_at: str | None = Field(default=None, description="拍摄时刻（UTC，ISO 8601）")
    taken_at_local: str | None = Field(
        default=None, description="拍摄地当地墙钟时间（ISO 8601，无时区）"
    )
    tz_name: str | None = Field(default=None, description="形如 UTC+08:00")
    lat: float | None = Field(default=None, description="WGS84 纬度；未定位时为 null")
    lng: float | None = Field(default=None, description="WGS84 经度；未定位时为 null")
    coord_system: str = Field(default="WGS84", description="坐标系，存储侧恒为 WGS84")
    location_source: str | None = Field(default=None, description="位置来源：exif / user / none")
    has_location: bool = Field(description="是否已定位（lat 与 lng 都有值）")
    location_confidence: float | None = Field(
        default=None, description="位置置信度：EXIF 0.9，用户补标 0.8，无位置 0.0"
    )
    caption: str | None = Field(default=None, description="视觉模型生成的描述；未生成为 null")
    created_at: str = Field(description="入库时间（UTC，ISO 8601）")


class PhotoLocationUpdate(BaseModel):
    """`PATCH /api/v1/photos/{id}/location` 的入参：用户手动补标坐标。

    ## 为什么只收 lat/lng，不收 location_source

    来源是**后端根据接口路径决定的事实**：走到这个接口就是用户补标，
    写死 `user`。让客户端传 source，等于允许它自称 `exif` ——
    那会骗过下游按置信度分级处理的逻辑（地理模糊半径就按它算）。

    ## 为什么 lat/lng 有范围约束

    纬度 ±90、经度 ±180 是**地理常识**而不是业务规则。放一个 999 进来，
    地图会静默不渲染（高德对越界坐标的行为），用户看到的是"补标了但地图上什么都没有"。
    """

    lat: float = Field(ge=-90, le=90, description="WGS84 纬度")
    lng: float = Field(ge=-180, le=180, description="WGS84 经度")


class TripPhotosAssign(BaseModel):
    """`POST /api/v1/trips/{id}/photos` 的入参：把一批照片关联进行程。

    ## 为什么是"全量提交"而不是增量 add

    调用方（前端）拿到的是"这个行程现在该有哪些照片"的完整快照，
    由它去 diff 出"要删哪张、要加哪张"。后端只负责把结果设为目标状态，
    不负责猜增量 —— 猜错一次就出现"我以为加了、其实没加"的幽灵条目，
    而这种不一致在界面上看不出任何异常。
    """

    photo_ids: list[str] = Field(
        min_length=1, description="要关联进本行程的照片 id（Photo 主键，UUID 字符串）"
    )


class ImportPhotosResult(BaseModel):
    """一次批量导入的结果。

    ## 为什么 `failed` 是数量而不是错误列表

    一批 200 张里可能有 30 张是截图/损坏文件。把 30 条错误消息全塞进响应体，
    前端拿到也只会显示"30 张失败"。数量 + 日志（服务端按指纹前缀记下失败原因）
    是信息量与体积之间的正确取舍 —— 排障看日志，用户看数字。

    ## 三个计数器的完备性

    `imported + duplicates + failed == 上传的文件数`，且三者互斥。
    这是本契约最有用的一条保证：前端可以据此直接断言"没有文件被静默吞掉"。

    ## 为什么不再有 `trip_ids`

    行程从"导入时自动聚类出来的派生数据"改成"用户手动组建的视图"之后，
    导入这一步**不再产生任何行程**（见 `api/v1/photos.py` 的取舍说明）。
    保留一个恒为 `[]` 的字段只会让前端继续写"导入后刷新行程"的死代码，
    所以连同前端调用一起删掉。
    """

    imported: int = Field(description="本次新建的照片条数")
    duplicates: int = Field(description="命中内容去重（库里已有或本批次内重复）的条数")
    failed: int = Field(description="解析失败（非图片 / 损坏）的文件数")
    photos: list[ImportedPhoto] = Field(
        default_factory=list, description="本次涉及的全部照片（含去重命中的旧记录）"
    )


class TripItemView(BaseModel):
    """行程里的一张照片。

    `taken_at` 是聚类当时的**快照**（见 `models/trip.py` 的字段说明），
    不是实时从 `Photo` 读出来的值 —— 视图展示的是当初展示给用户的那一版。
    """

    id: str = Field(description="TripItem 主键")
    photo_id: str = Field(description="对应 Photo 主键")
    taken_at: str | None = Field(default=None, description="拍摄时刻（UTC，ISO 8601）")
    lat: float | None = Field(default=None, description="WGS84 纬度")
    lng: float | None = Field(default=None, description="WGS84 经度")
    semantic_tag: str | None = Field(default=None, description="六类语义标签之一；无证据时为 null")
    semantic_label: str | None = Field(default=None, description="语义标签的中文名")


class TripView(BaseModel):
    """一段行程（含它的照片列表）。

    `item_count` 与 `len(items)` 恒等，仍然单独给一个字段：
    将来若做分页（`items` 只回第一页），`item_count` 仍是总数，
    契约不会因此破裂。
    """

    id: str = Field(description="Trip 主键")
    title: str = Field(description="行程标题，形如「2026-04-12 起的旅程」")
    started_at: str | None = Field(default=None, description="行程起点（UTC，ISO 8601）")
    ended_at: str | None = Field(default=None, description="行程终点（UTC，ISO 8601）")
    source: str = Field(default="cluster", description="cluster（聚类生成）/ manual（手工）")
    item_count: int = Field(description="行程内照片数")
    items: list[TripItemView] = Field(default_factory=list, description="行程内照片，按簇内顺序")


class TripListResponse(BaseModel):
    """`GET /api/v1/trips` 的响应：当前用户的全部行程。"""

    trips: list[TripView] = Field(default_factory=list, description="按 started_at 升序")


class TripCreateRequest(BaseModel):
    """`POST /api/v1/trips` 的入参：手工创建一个**空**行程。

    ## 为什么只收 `title`，不收时间 / 照片

    手工行程在这一步是**空的**：它还没有照片，所以 `started_at` / `ended_at`
    无从谈起（`null` 由后端自己填，不是让用户传一个假时间）。
    照片通过后续导入进来，行程的边界再由后端按时间与空间重算 ——
    让用户在这一步就指定"从哪天到哪天"，等于让他手工维护一份
    后端随时会用照片重算出来的派生值，两边必然打架。

    ## 为什么 `title` 有长度上限且不能是纯空白

    `Trip.title` 是 `String(128)`：数据库会在超长时报错（有些数据库是截断），
    而"标题太长"是**用户输入问题**，该在入口层就说清楚，而不是变成一次 500。

    空白标题（`title="   "`）会被 Pydantic 原样接受，落库后就是一个
    看起来为空的行程卡片。所以在校验里剥掉首尾空白再判空 ——
    **存进去的是剥好的值**，不是"存原值、显示时再剥"。
    """

    title: str = Field(
        min_length=1,
        max_length=128,
        description="行程标题，如「洛阳两日」。首尾空白会被剥掉，剥完为空则 422",
    )
    #: 行程起始日（**当地日期**，不是时间戳）。
    #:
    #: 只在"从 journey 包导回"时使用：包里的 `days[0].date` 是事实，
    #: 而缩略图没有 EXIF，后端无法从照片反推。手工新建时传 None 即可 ——
    #: 不传和传 null 都表示"还不知道"，不会填 now()。
    started_on: date | None = Field(
        default=None,
        description="行程起始日（YYYY-MM-DD，当地日期）；未知则不传",
    )


class TripRenameRequest(BaseModel):
    """`PATCH /api/v1/trips/{id}` 的入参：重命名一趟行程。

    ## 为什么不复用 `TripCreateRequest`

    两者的**失败语义不同**：创建时标题为空是"这次没建成"，
    重命名时标题为空是"把名字改没了"。后者更糟 —— 用户会看到一行
    没有标题的行程卡片，而且不知道刚才那次点击生效了没有。
    分开两个类型，路由层的校验文案就能各说各话，不用在一个 `if` 里猜语境。
    """

    title: str = Field(
        min_length=1,
        max_length=128,
        description="新的行程标题。首尾空白会被剥掉，剥完为空则 422",
    )


class TripCreateResponse(BaseModel):
    """`POST /api/v1/trips` 的响应：新建出来的那一条行程。

    只回一条而不是整个列表：调用方刚发过 `title`，它知道自己建了什么；
    要刷新列表就再调一次 `GET`。把全量行程塞进创建响应，
    会让"创建成功"和"列表刷新"两件事在前端共用一个状态位，
    将来做乐观更新时反而不好拆。

    `source` 恒为 `"manual"` —— 这是本接口存在的全部意义。
    """

    trip: TripView = Field(description="新建的行程（source 恒为 manual）")


__all__ = [
    "ImportPhotosResult",
    "ImportedPhoto",
    "PhotoListItem",
    "PhotoLocationUpdate",
    "TripCreateRequest",
    "TripCreateResponse",
    "TripItemView",
    "TripListResponse",
    "TripPhotosAssign",
    "TripRenameRequest",
    "TripView",
    "iso_or_none",
]
