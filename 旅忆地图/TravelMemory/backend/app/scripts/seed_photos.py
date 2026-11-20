"""演示照片导入（`make seed-photos`）。

阶段 1 还没有真实导入链路（W6-1 才做相册读取 + EXIF 解析），
但 caption_job / index_job 需要有照片才能验证，所以这里按演示行程**合成 Photo 行**：

```
demo_corpus.jsonl（行程：日期区间 + 城市 + 地点）
        ↓ 每天生成若干张
demo_places.jsonl（地点经纬度）
        ↓
Photo 行（platform=demo, asset_id=demo-t1-01, 时间 / 经纬度 / exif 线索）
```

合成的只是**元数据行**，不生成任何图片文件——"原图永不上云"的边界在本阶段就已经成立：
库里从头到尾没有原图，只有文本与时间位置元数据。

幂等：`unique(platform, asset_id)`，重复执行不会新增。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionFactory, dispose_engine, ping_database
from app.models.photo import AI_PENDING, Photo
from app.services.user_service import get_or_create_demo_user

logger = get_logger(__name__)

#: 一天里"拍照"的几个典型时刻（合成数据用，让日文档有时间层次）
_DAY_TIMES = ("09:12", "14:30", "19:40")


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_photo_rows(
    trips: list[dict], places: dict[str, dict], user_id, per_day: int = 3
) -> list[dict]:
    """按行程 × 日期 × 时刻合成照片行。"""
    rows: list[dict] = []
    for trip in trips:
        meta = trip.get("metadata") or {}
        start = _parse_date(meta["date_start"])
        end = _parse_date(meta["date_end"])
        city = str(meta.get("city") or "")
        trip_places = list(meta.get("places") or [])
        trip_id = str(trip["source_id"])

        day_count = (end - start).days + 1
        index = 0
        for offset in range(day_count):
            current = start + timedelta(days=offset)
            for slot in range(min(per_day, len(_DAY_TIMES))):
                index += 1
                place_name = trip_places[(index - 1) % len(trip_places)] if trip_places else ""
                coord = _coord_of(places, place_name)
                asset_id = f"demo-{trip_id}-{index:02d}"
                rows.append(
                    {
                        "user_id": user_id,
                        "platform": "demo",
                        "asset_id": asset_id,
                        # 内容指纹：用稳定字段算，重复导入同一条不会产生第二行
                        "hash": hashlib.sha1(asset_id.encode("utf-8")).hexdigest(),
                        "lat": coord[0],
                        "lng": coord[1],
                        "coord_system": "WGS84",
                        "location_source": "user",
                        "location_confidence": 0.6,
                        "taken_at_local": datetime.combine(
                            current, datetime.strptime(_DAY_TIMES[slot], "%H:%M").time()
                        ),
                        "tz_name": "Asia/Shanghai",
                        "tz_source": "user",
                        # exif_json 里的 place/city 是给 caption 模型的地点线索
                        "exif_json": {
                            "city": city,
                            "place": place_name,
                            "trip_id": trip_id,
                            "trip_title": meta.get("trip_title"),
                        },
                        "ai_status": AI_PENDING,
                    }
                )
    return rows


def _coord_of(places: dict[str, dict], place_name: str) -> tuple[float | None, float | None]:
    for item in places.values():
        meta = item.get("metadata") or {}
        if meta.get("name") == place_name and meta.get("lat") is not None:
            return float(meta["lat"]), float(meta["lng"])
    return None, None


async def seed(*, per_day: int = 3, data_dir: Path | None = None) -> int:
    settings: Settings = get_settings()
    data_dir = data_dir or (settings.backend_dir / "data")

    if not await ping_database():
        print("✗ 数据库不可达。请先执行：make up && make migrate")
        await dispose_engine()
        return 0

    trips = load_jsonl(data_dir / "demo_corpus.jsonl")
    place_items = load_jsonl(data_dir / "demo_places.jsonl")
    places = {item["source_id"]: item for item in place_items}

    async with SessionFactory() as session:
        user = await get_or_create_demo_user(session)
        rows = build_photo_rows(trips, places, user.id, per_day=per_day)

        # 幂等：冲突即跳过，重复执行不会堆积
        stmt = (
            insert(Photo)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["platform", "asset_id"])
        )
        await session.execute(stmt)
        await session.commit()

        total = len((await session.execute(select(Photo.id))).scalars().all())
        print(f"本次合成 {len(rows)} 张演示照片（platform=demo，仅元数据，无图片文件）")
        print(f"photo 表现有 {total} 行；接下来跑：python -m app.jobs.caption_job --limit 200")

    await dispose_engine()
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合成演示照片元数据")
    parser.add_argument("--per-day", type=int, default=3, help="每天生成几张（默认 3）")
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    setup_logging(get_settings().log_level)
    asyncio.run(seed(per_day=args.per_day, data_dir=args.data_dir))


if __name__ == "__main__":
    main()
