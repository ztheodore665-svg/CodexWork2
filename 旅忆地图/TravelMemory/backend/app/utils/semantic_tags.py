"""六类语义标签的**唯一真相源**（阶段 7 W7-1 / W7-2 共用）。

## 为什么放在 `app/utils/` 而不是某个 service 里

依赖红线（PROJECT.md §二、[4]）：`agent → nodes → tools → services → models`。
语义词汇会被 `app/services/semantic_service.py`（抽取）与
`app/services/semantic_trajectory.py`（模式挖掘）**同时**用到，还可能被将来的
agent 工具引用。放在任一个 service 里都会让另一个反向依赖它 —— 那是红线禁止的方向。
`app/utils/` 是最底层的纯函数层，谁都可以向下依赖。

## 为什么是确定性规则而不是让 LLM 分类

1. **可复现**：同一条 POI 在 A 机器和 B 机器上必须得到同一个标签，否则两地的
   语义序列对不上，群体模式库就是噪声。LLM 做不到逐位一致。
2. **可审计**：规则表能直接读、能单测、能手工改一个词；LLM 的分类边界不可见。
3. **零成本**：语义抽取发生在导入链路里，每条照片/地点都调一次 LLM 是不可接受的。

## 平票的两级裁决（阶段 16 统一）

票数相同时，先比**该类的命中词长度**（越长越具体），长度也相同才按
`SEMANTIC_TAGS` 顺序。第二级是契约：

`SEMANTIC_TAGS` 的**顺序本身就是契约**：它与前端 `src/types/share.ts` 的 `ALL_TAGS`
逐项一致，而 `ALL_TAGS` 是 TrailShare 规范里"评分打平时谁在前面"的依据。
改顺序会让两端的推荐结果分叉，**只能追加，不能重排**。

第一级的存在理由见 `平江路` 的例子：单字"江"让 nature 与 watertown 各得一票，
但"平江路"这个整名更长、更具体，必须压过单字。阶段 16 之前这条规则只写在
`services/semantic_service.py` 里，而本模块的 `classify_semantic_tag` 用的是
纯顺序裁决 —— 同一个概念两套算法。现在只有 `match_semantic_tag` 一份实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: 六类语义标签。**顺序即平票排序依据**，与前端 `ALL_TAGS` 一致，禁止重排。
SEMANTIC_TAGS: tuple[str, ...] = (
    "heritage",
    "nature",
    "watertown",
    "metropolis",
    "food",
    "leisure",
)

SEMANTIC_TAG_LABELS: dict[str, str] = {
    "heritage": "人文历史",
    "nature": "自然风光",
    "watertown": "水乡古镇",
    "metropolis": "都市风光",
    "food": "美食探索",
    "leisure": "休闲度假",
}

#: 语义标签版本号。变更关键词表**必须**同步升它 —— 已落库的语义序列是按旧版抽的，
#: 拿新版规则去解释旧序列会得到错误的模式（设计文档 §12.2 `make resemantic` 的由来）。
SEMANTIC_TAG_VERSION = "sem-2026.2"

#: 关键词 → 语义标签。命中即计 1 票，多命中累加，平票按 `SEMANTIC_TAGS` 顺序取。
#:
#: 词表来源：设计文档 §9.1 的示例行程（龙门石窟→heritage、老君山→nature、
#: 拙政园/平江路→watertown、外滩/陆家嘴→metropolis），其余按同类常见 POI 补齐。
#:
#: ## 为什么既有单字也有整名（两类词各有分工）
#:
#: - **单字/后缀词**（`寺` / `山` / `江` / `湖`）负责**泛化**：`老君山`、`雁荡山`
#:   这类长尾地名不可能逐个收录，靠后缀兜住 —— 这是词表能覆盖未见 POI 的唯一机制。
#: - **整名**（`清明上河园` / `故宫` / `东方明珠`）负责**纠偏**：单字泛化必然误伤。
#:   `清明上河园` 里有 `河`，只靠单字会被判成 nature，而它是人文景区；
#:   `王府井` 里有 `王府`（heritage），只靠单字会被判成 heritage，而它是商圈。
#:
#: 纠偏之所以生效，靠的是 `semantic_service._break_tie()` 的**平票修正**：
#: 整名与单字各得 1 票形成平票，命中词更长的那一类胜出（4 字 > 1 字）。
#: 所以**整名必须比它可能误伤的那个单字更长** —— 这条约束是本词表的维护纪律。
#:
#: 词表是有限的，收录不到的地名会诚实返回 `None`。**不要为了"覆盖率好看"把
#: 边界词塞进来**：一个猜错的语义会顺着模式库污染群体推荐，而"没有语义"
#: 只是少一条数据。
TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "heritage": (
        "石窟", "寺庙", "寺院", "寺", "庙", "观", "庵", "遗址", "古迹", "文物",
        "博物馆", "博物院", "纪念馆", "故居", "陵园", "陵", "墓", "碑", "塔",
        "古城", "城楼", "城墙", "关隘", "宫殿", "王府", "书院", "教堂", "清真寺",
        "历史", "人文", "文化", "非遗", "民俗", "考古", "石窟寺", "造像",
        "科教文化服务", "文化场馆",
        # 整名纠偏：这些是人文景点，但名字里带着会被判成别的类的字
        # （"清明上河园" 有"河"、"颐和园" 是皇家园林、"兵马俑" 只有"俑"）
        "故宫", "天坛", "颐和园", "圆明园", "兵马俑", "清明上河园",
        "夫子庙", "大雁塔", "钟楼", "鼓楼", "太和殿", "御花园", "皇城",
    ),
    "nature": (
        "山", "山峰", "峰", "岭", "谷", "峡谷", "湖", "海", "江", "河", "溪",
        "瀑", "瀑布", "森林", "林场", "草原", "草甸", "沙漠", "戈壁", "岛",
        "湿地", "公园", "地质公园", "国家公园", "自然", "风景", "名胜",
        "云海", "日出", "日落", "星空", "梯田", "花海", "竹海", "溶洞", "天池",
        "风景区", "风景名胜",
        # 整名：著名自然景区（多数已能靠"山/湖/江"命中，补的是那些不含通名的）
        "九寨沟", "张家界", "玉龙雪山", "苍山", "洱海", "漓江", "太湖",
        "千岛湖", "纳木错", "喀纳斯",
    ),
    "watertown": (
        "古镇", "古村", "水乡", "水镇", "乌镇", "西塘", "周庄", "同里", "南浔",
        "平江路", "山塘街", "拙政园", "留园", "网师园", "园林", "水巷", "弄堂",
        "巷", "运河", "码头", "拱桥", "石桥", "摇橹", "乌篷",
        # 整名纠偏：名字里含"古城"（heritage）或"江"（nature）的水乡，
        # 靠 4 字整名在平票修正里压过 2 字/1 字的误伤词
        "丽江古城", "凤凰古城", "大理古城", "阆中古城", "宽窄巷子", "锦里",
    ),
    "metropolis": (
        "外滩", "陆家嘴", "cbd", "商圈", "金融", "写字楼", "摩天", "天际线",
        "都市", "夜景", "广场", "步行街", "购物中心", "商场", "百货",
        "观景台", "电视塔", "观光塔", "地铁", "机场", "车站", "港区",
        "购物服务", "商务",
        # 整名纠偏：含"王府"（heritage）、"城"（heritage）却实为商圈/地标的地方
        "东方明珠", "王府井", "南京路", "三里屯", "国贸", "解放碑",
        "洪崖洞", "环球金融中心", "中央商务区",
    ),
    "food": (
        "美食", "小吃", "餐厅", "饭店", "饭馆", "食街", "夜市", "菜市场",
        "茶楼", "茶馆", "咖啡", "烘焙", "甜品", "火锅", "烧烤", "海鲜",
        "米其林", "老字号", "特产", "农家菜", "大排档", "食堂",
        "餐饮服务", "饮食",
        # 整名：著名美食街区（"街"本身不是关键词，避免把任意街道判成美食）
        "回民街", "簋街", "户部巷", "城隍庙小吃",
    ),
    "leisure": (
        "度假", "温泉", "spa", "汤泉", "高尔夫", "游乐场", "游乐园", "主题公园",
        "动物园", "海洋馆", "水族馆", "露营", "民宿", "农家乐", "休闲", "康养",
        "酒吧", "club", "ktv", "影院", "剧院", "演出", "滑雪", "潜水", "冲浪",
        "骑行", "徒步", "营地", "度假村", "体育休闲服务", "住宿服务",
        # 整名纠偏：含"公园"（nature）却实为游乐场所的地方
        "欢乐谷", "迪士尼", "方特", "水上乐园",
    ),
}


def is_semantic_tag(value: str | None) -> bool:
    """是否是六类语义标签之一。"""
    return isinstance(value, str) and value in SEMANTIC_TAGS


@dataclass(frozen=True)
class SemanticTagMatch:
    """一次语义匹配的完整结果：胜出标签 + 票数 + 逐类证据。

    为什么不是只返回 `tag`：调用方还需要 `confidence`（胜出票数 / 参与投票的段数）
    与 `matched`（哪几个输入片段触发了它）。让调用方拿 `tag` 再自己算一遍，
    就是第二套算法 —— 阶段 16 之前正是这么分叉的。
    """

    tag: str | None
    scores: dict[str, int]
    longest: dict[str, int]
    matched: dict[str, tuple[str, ...]]
    segment_count: int


def _segments(
    poi_type: str | None,
    ai_tags: Sequence[str] | None,
    name: str | None,
) -> list[str]:
    """展开"参与投票的输入段"：`poi_type` 与 `name` 各一段，`ai_tags` **每项**各一段。

    `ai_tags` 逐项而不是拼成一个大字符串：拼串会让"命中了三类"和"某一类命中三次"
    无法区分，而前者才是有信息量的证据。
    """
    segments: list[str] = [item for item in (poi_type, name) if item]
    segments.extend(item for item in (ai_tags or ()) if item)
    return segments


def match_semantic_tag(
    *,
    poi_type: str | None = None,
    ai_tags: Sequence[str] | None = None,
    name: str | None = None,
) -> SemanticTagMatch:
    """六类标签的**唯一匹配实现**：投票 → 平票两级裁决。

    一段文本对同一类最多投 1 票（否则"山山山"这种噪声会把自己刷成 nature）。
    平票时先比命中词长度、再比 `SEMANTIC_TAGS` 顺序 —— 详见模块 docstring。
    全部无命中时 `tag=None` 且 `scores={}`：调用方决定隐藏语义条还是重试，
    本函数不做兜底猜测（猜错的语义会污染群体模式库）。
    """
    segments = _segments(poi_type, ai_tags, name)
    scores: dict[str, int] = {}
    longest: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for segment in segments:
        haystack = segment.lower()
        for tag, keywords in TAG_KEYWORDS.items():
            hit = max((keyword for keyword in keywords if keyword in haystack), key=len, default="")
            if not hit:
                continue
            scores[tag] = scores.get(tag, 0) + 1
            longest[tag] = max(longest.get(tag, 0), len(hit))
            matched.setdefault(tag, []).append(segment)

    if not scores:
        return SemanticTagMatch(None, {}, {}, {}, len(segments))

    top = max(scores.values())
    tied = [tag for tag in SEMANTIC_TAGS if scores.get(tag, 0) == top]
    winner = max(tied, key=lambda tag: (longest.get(tag, 0), -SEMANTIC_TAGS.index(tag)))
    return SemanticTagMatch(
        tag=winner,
        scores=scores,
        longest=longest,
        matched={tag: tuple(items) for tag, items in matched.items()},
        segment_count=len(segments),
    )


def score_semantic_tags(*texts: str | None) -> dict[str, int]:
    """对若干段文本投票，返回每类的命中数（未命中的类不出现在字典里）。

    保留这个入口是为了让"只想看票数"的调用方不必构造 `ai_tags` 列表；
    它内部走的是同一个 `match_semantic_tag`，不存在第二套计票逻辑。
    """
    return match_semantic_tag(ai_tags=[text for text in texts if text]).scores


def pick_semantic_tag(scores: dict[str, int]) -> str | None:
    """只有票数、没有证据长度时的降级裁决：**平票按 `SEMANTIC_TAGS` 顺序**。

    正常路径应该用 `match_semantic_tag`（它能比"命中词有多具体"）。
    这个函数留给"手上只有一份票数统计"的调用方，语义与旧版一致。
    """
    if not scores:
        return None
    best_tag = None
    best_score = -1
    for tag in SEMANTIC_TAGS:  # 顺序遍历 → 平票时靠前的赢
        score = scores.get(tag, 0)
        if score > best_score:
            best_score = score
            best_tag = tag
    return best_tag if best_score > 0 else None


def classify_semantic_tag(
    poi_type: str | None = None,
    ai_tags: Sequence[str] | None = None,
    name: str | None = None,
) -> str | None:
    """把 `poi_type` / `ai_tags` / 地点名映射到六类语义标签（只要标签、不要证据）。

    三类输入**等权**各计一票（不加权：权重从哪来没有依据，拍脑袋的权重只会让
    "为什么这个石窟被分到 nature"更难解释）。全部无命中返回 `None`。
    需要 `confidence` / `matched` 时用 `match_semantic_tag`。
    """
    return match_semantic_tag(poi_type=poi_type, ai_tags=ai_tags, name=name).tag
