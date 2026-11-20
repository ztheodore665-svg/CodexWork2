"""中文时间表达 → 日期范围（纯函数，W2-1 的核心）。

## 为什么必须自己写规则解析器，而不是全交给 LLM

| 方案 | 问题 |
| --- | --- |
| 全交给 LLM | ① LLM 不知道"今天"是哪天（prompt 里注入了也常算错一两年）；② 每次输出可能不同，**评测无法归因**；③ 离线占位模式（mock LLM）下整条链路没法验证 |
| 全靠规则 | 认不出"上次那趟""看完冰雕的那次"这类指代性表达 |
| **规则 + LLM 双路（本项目）** | 规则负责**确定性的日历换算**，LLM 负责**从句子里认出时间词**；命中冲突时以规则为准 |

一句话概括分工：**LLM 负责"认字"，规则负责"算数"。**
数学题交给 LLM 做，等于每次运行掷一次骰子。

## 关键取舍：算不准就别过滤

设计文档 §7.3 的原则是"解析不确定时退化为不过滤（宁可多召回，不要漏召回）"。
因此本模块有两类**主动放弃**：

- **农历节日**（春节 / 清明 / 端午 / 中秋）公历日期年年不同，规则算不准 → 直接返回 None
- **整段时间落在未来**且年份是推断出来的 → 往前挪一年（你要回忆的肯定是已经发生过的）

返回 `None` 不是失败，而是"这一维不加过滤条件"，是完全合法的结果。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# ------------------------------------------------------------------ 词表

#: 季节 → (起始月, 结束月)。冬天跨年，单独处理
SEASON_MONTHS: dict[str, tuple[int, int]] = {
    "春": (3, 5),
    "夏": (6, 8),
    "秋": (9, 11),
    "冬": (12, 2),
}

#: 相对年份偏移（"今年"是 0）
RELATIVE_YEAR_WORDS: dict[str, int] = {
    "今年": 0,
    "本年": 0,
    "这一年": 0,
    "去年": -1,
    "上一年": -1,
    "过去的那年": -1,
    "前年": -2,
    "大前年": -3,
    "明年": 1,
    "下一年": 1,
    "后年": 2,
}

#: 节假日 → (起始月, 起始日, 结束月, 结束日)。**只收公历固定的**
HOLIDAY_RANGES: dict[str, tuple[int, int, int, int]] = {
    "元旦": (1, 1, 1, 3),
    "跨年": (12, 30, 1, 2),
    "五一": (5, 1, 5, 5),
    "劳动节": (5, 1, 5, 5),
    "国庆": (10, 1, 10, 7),
    "十一": (10, 1, 10, 7),
    "暑假": (7, 1, 8, 31),
    "寒假": (1, 15, 2, 28),
}

#: 明确不做换算的农历节日：命中就放弃过滤，并在 trace 里说明原因
LUNAR_HOLIDAYS = ("春节", "过年", "除夕", "元宵", "清明", "端午", "中秋", "重阳")

#: 中文数字 → 阿拉伯数字（够用到 99）
_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_CN_MONTH_ALIAS = {"正月": 1, "腊月": 12, "冬月": 11}

#: 月份内的粗粒度分段
_MONTH_PARTS = {
    "月初": (1, 10),
    "上旬": (1, 10),
    "月中": (11, 20),
    "中旬": (11, 20),
    "月末": (21, 31),
    "月底": (21, 31),
    "下旬": (21, 31),
}


# ------------------------------------------------------------------ 输出
@dataclass(frozen=True)
class TimeRange:
    """一个闭区间日期范围。

    `label` 记下命中的原文（如"去年秋天"），写进 trace 后一眼能看出
    "这条过滤条件是从哪句话来的"——排查误过滤时比看日期本身有用得多。
    """

    start: date
    end: date
    label: str = ""
    #: 年份是显式给出的（"2023 年""去年"）还是推断出来的（"七月" → 最近那个七月）
    year_explicit: bool = False

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.start.isoformat()}~{self.end.isoformat()}"


# ------------------------------------------------------------------ 入口
def parse_time_range(text: str, today: date | None = None) -> TimeRange | None:
    """把一句话里的时间表达换算成日期范围。

    返回 `None` 表示"没有识别到时间信息"或"识别到但不敢确定" —— 调用方应当
    **不加时间过滤条件**，而不是猜一个范围。

    候选按优先级从高到低尝试，命中即返回：
    相对时段（上个月）> ISO 日期 > 节假日 > 年×季 > 年×月 > 年 > 季 > 月
    """
    if not text or not text.strip():
        return None
    today = today or date.today()
    raw = _normalize(text)

    # 1. 明确放弃：农历节日
    for word in LUNAR_HOLIDAYS:
        if word in raw:
            return None

    # 2. 相对时段（今天 / 昨天 / 上周 / 上个月 / 最近 N 天 …）
    relative = _match_relative(raw, today)
    if relative is not None:
        return relative

    # 3. ISO 日期（2025-10-03 / 2025/10/3）
    iso = _match_iso(raw)
    if iso is not None:
        return TimeRange(iso, iso, iso.isoformat(), year_explicit=True)

    # 4. 节假日（五一 / 国庆 / 暑假 …）
    holiday = _match_holiday(raw, today)
    if holiday is not None:
        return holiday

    year, year_explicit = _match_year(raw, today)

    # 5. 年 × 季
    season = _match_season(raw)
    if season is not None:
        return _from_month_span(year, season[0], season[1], today, year_explicit, raw)

    # 6. 年 × 月（可再叠"月初 / N 日"）
    month = _match_month(raw)
    if month is not None:
        day_from, day_to = _match_month_part(raw, month, today)
        resolved_year = year or _infer_year(today, month)
        last = _last_day(resolved_year, month)
        return TimeRange(
            date(resolved_year, month, day_from),
            date(resolved_year, month, min(day_to, last)),
            _label(raw),
            year_explicit=year_explicit,
        )

    # 7. 只有"年"（或相对年）
    if year is not None:
        return TimeRange(date(year, 1, 1), date(year, 12, 31), _label(raw), year_explicit)

    return None


# ------------------------------------------------------------------ 归一化
def _normalize(text: str) -> str:
    """全角数字 → 半角，去掉多余空白（中文里夹的全角数字很常见）。"""
    table = str.maketrans("０１２３４５６７８９／－　", "0123456789/- ")
    return " ".join(text.translate(table).split())


def _label(text: str) -> str:
    """给 trace 用的命中原文摘要。"""
    return text[:40]


# ------------------------------------------------------------------ 各类匹配
def _match_relative(raw: str, today: date) -> TimeRange | None:
    """今天 / 昨天 / 本周 / 上个月 / 最近 N 天 这类"锚定在今天"的表达。"""
    if re.search(r"今天|今日|当天", raw):
        return TimeRange(today, today, "今天")
    if re.search(r"昨天|昨日", raw):
        day = today - timedelta(days=1)
        return TimeRange(day, day, "昨天")
    if re.search(r"前天", raw):
        day = today - timedelta(days=2)
        return TimeRange(day, day, "前天")

    if re.search(r"本周|这周|这个星期|这星期", raw):
        monday = today - timedelta(days=today.weekday())
        return TimeRange(monday, today, "本周")
    if re.search(r"上周|上个星期|上星期|上一周", raw):
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        return TimeRange(start, start + timedelta(days=6), "上周")

    if re.search(r"本月|这个月|这月|当月", raw):
        return TimeRange(today.replace(day=1), today, "本月")
    if re.search(r"上上个月", raw):
        start = _shift_month(today, -2)
        return TimeRange(start, _month_end(start), "上上个月")
    if re.search(r"上个月|上月", raw):
        start = _shift_month(today, -1)
        return TimeRange(start, _month_end(start), "上个月")
    if re.search(r"下个月|下月", raw):
        start = _shift_month(today, 1)
        return TimeRange(start, _month_end(start), "下个月")

    if re.search(r"上半年", raw):
        year = _match_year(raw, today)[0] or today.year
        return TimeRange(date(year, 1, 1), date(year, 6, 30), "上半年")
    if re.search(r"下半年", raw):
        year = _match_year(raw, today)[0] or today.year
        return TimeRange(date(year, 7, 1), date(year, 12, 31), "下半年")

    # 最近 N 天 / 周 / 个月 / 年
    match = re.search(
        r"(?:最近|近|过去)\s*([一二三四五六七八九十两]|\d+)?\s*(天|日|周|个?月|年)", raw
    )
    if match:
        amount = _to_int(match.group(1)) if match.group(1) else 1
        unit = match.group(2).lstrip("个")
        days = {"天": 1, "日": 1, "周": 7, "月": 30, "年": 365}[unit] * amount
        return TimeRange(today - timedelta(days=days), today, f"最近{amount}{unit}")

    return None


def _match_iso(raw: str) -> date | None:
    """2025-10-03 / 2025/10/3 / 2025.10.03"""
    match = re.search(r"(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", raw)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    day = int(match.group(3)) if match.group(3) else 1
    if not (1 <= month <= 12):
        return None
    return date(year, month, min(day, _last_day(year, month)))


def _match_holiday(raw: str, today: date) -> TimeRange | None:
    """五一 / 国庆 / 暑假 这类公历固定的区间。"""
    for word, (m1, d1, m2, d2) in HOLIDAY_RANGES.items():
        if word not in raw:
            continue
        year, explicit = _match_year(raw, today)
        if year is None:
            # 没说哪一年就取"最近一次已经过去的"
            year = _infer_year(today, m2)
        if m1 > m2:  # 跨年（如 12/30 ~ 1/2）
            return TimeRange(date(year, m1, d1), date(year + 1, m2, d2), word, explicit)
        return TimeRange(date(year, m1, d1), date(year, m2, d2), word, explicit)
    return None


def _match_year(raw: str, today: date) -> tuple[int | None, bool]:
    """返回 (年份, 是否显式给出)。相对词也算显式 —— "去年"是确定的。"""
    match = re.search(r"(20\d{2})\s*年", raw)
    if match:
        return int(match.group(1)), True
    for word, offset in RELATIVE_YEAR_WORDS.items():
        if word in raw:
            return today.year + offset, True
    # 口语里"23 年"这种两位的，按 20xx 处理
    match = re.search(r"(?<!\d)(\d{2})\s*年", raw)
    if match:
        return 2000 + int(match.group(1)), True
    return None, False


def _match_season(raw: str) -> tuple[int, int] | None:
    for word, span in SEASON_MONTHS.items():
        if re.search(rf"{word}(?:天|季|日)?", raw):
            return span
    return None


def _match_month(raw: str) -> int | None:
    """识别月份：10 月 / 十月 / 正月 / 腊月 / 年底。"""
    # "去年年底"这类表达里没有月份数字，但语义上就是 12 月
    if re.search(r"年底|年末|年终|岁末", raw):
        return 12
    if re.search(r"年初|年头|开年", raw):
        return 1
    for alias, value in _CN_MONTH_ALIAS.items():
        if alias in raw:
            return value
    match = re.search(r"(\d{1,2})\s*月", raw)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 12 else None
    match = re.search(r"([一二三四五六七八九十]+)\s*月", raw)
    if match:
        value = _to_int(match.group(1))
        return value if 1 <= value <= 12 else None
    return None


def _match_month_part(raw: str, month: int, today: date) -> tuple[int, int]:
    """月内的细分：N 日 / N 号 / 月初 / 月末。没提到就整月。"""
    match = re.search(r"(\d{1,2}|[一二三四五六七八九十]+)\s*[日号]", raw)
    if match:
        day = _to_int(match.group(1))
        return day, day
    for word, span in _MONTH_PARTS.items():
        if word in raw:
            return span
    return 1, 31


# ------------------------------------------------------------------ 组合
def _from_month_span(
    year: int | None,
    month_from: int,
    month_to: int,
    today: date,
    year_explicit: bool,
    raw: str,
) -> TimeRange:
    """把 (起始月, 结束月) 变成日期区间，并处理跨年与"落在未来"的回退。"""
    if year is None:
        # "秋天""冬天"没带年份：取最近一次**已经发生过**的该季节
        year = _infer_year(today, month_to)
    if month_from > month_to:  # 冬天：12 月 ~ 次年 2 月
        start = date(year, month_from, 1)
        end = _month_end(date(year + 1, month_to, 1))
    else:
        start = date(year, month_from, 1)
        end = _month_end(date(year, month_to, 1))

    # 推断出来的年份如果整段都在未来，往前挪一年 —— 回忆的一定是过去
    if not year_explicit and start > today:
        start = start.replace(year=start.year - 1)
        end = end.replace(year=end.year - 1)
    return TimeRange(start, end, _label(raw), year_explicit)


def _infer_year(today: date, end_month: int) -> int:
    """推断年份：取"最后一次已经发生的"该月份所在年。"""
    return today.year if end_month <= today.month else today.year - 1


# ------------------------------------------------------------------ 小工具
def _to_int(text: str) -> int:
    """中文数字 / 阿拉伯数字 → int（支持到 99）。"""
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(text, 0)


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_end(value: date) -> date:
    return value.replace(day=_last_day(value.year, value.month))


def _shift_month(value: date, offset: int) -> date:
    """按月平移（返回该月 1 号）。"""
    total = (value.year * 12 + value.month - 1) + offset
    return date(total // 12, total % 12 + 1, 1)
