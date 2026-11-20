"""群体语义轨迹推荐 —— 阶段 7 W7-2 的后端纯算法部分（设计文档 §9）。

## 为什么"语义泛化"本身就是隐私保护

基于具体地点的协同过滤形如「去过龙门石窟的人接着去了老君山」。它的问题不在精度，
而在**唯一性**：一条记录里的地点组合往往只对应极少数人（龙门石窟 + 洛邑古城 + 老君山
这个三元组，全国范围内可能只有几百人）。攻击者只要知道目标的任一地点，就能用**一条**
记录把候选人群缩到可重识别的规模。把坐标模糊到城市也救不了 —— 因为唯一性来自
**组合的基数**，不是单点的精度。

抽成 `heritage → nature` 之后，同一个模式会被大量行程同时命中，模式库里的每一条
都天然代表**一批人**：这就是 k-匿名，只是匿名集是**自然形成的**，不需要额外的泛化
或抑制步骤。设计文档 §9.1 的两条示例行程：

    洛阳：      heritage(高) → heritage(中) → nature(极高) → heritage(高)
    苏州—上海：  watertown(高) → watertown(中) → metropolis(高) → metropolis(高)

抽象掉地点名之后，"人文之后去看自然"是一条可以在成千上万条行程上统计的规律，
而"龙门石窟之后去老君山"永远只属于少数人。

## 关于"联邦学习"的准确表述（设计文档 §9.3）

本项目**不宣称联邦学习**。联邦学习的前提是参与者规模足够大、且配套安全聚合协议；
这里的参与者规模有限（单用户起步），诚实的表述是：

    各客户端**本地**抽取语义序列并做本地统计，只上传**聚合统计量**
    （pattern_hash + support_delta + score_sum / score_n），
    服务端只做**逻辑聚合器**，全程不接触任何原始序列。

也就是说：服务端能算出的东西，单个参与者只靠自己也都能算出来；服务端做不到
"从聚合结果反推某个人去过哪"。写文档时照抄这一段的措辞，不要升级成"联邦学习"。

## 为什么是内存实现，不连数据库

- 模式挖掘的全部逻辑（前缀生成、unique 去重、评分过滤、k-匿名门禁）都不需要 SQL，
  内存实现让 **k-匿名门禁这条红线能被单测离线钉死**；
- 将来换成真实持久化时，门禁判定仍在本文件里，只有 `_patterns` 的存放位置换掉；
- 数据当前不可达，任何需要真实库的测试都会变成"看当天 Docker 有没有开"的玄学用例。

## 三道防线的分工

| 环节 | 函数 / 常量 | 拦什么 |
| --- | --- | --- |
| 泛化 | `extract_sequences` / `validate_sequence` | 抹掉地点名，只留语义 |
| 截断 | `truncate_sequence` / `MAX_SEQUENCE_STEPS` | 长历史本身就是指纹 |
| 门禁 | `SemanticPattern.published` / `K_ANONYMITY` | 样本量不足的模式不可见 |
| 防刷 | `submit(user_key=...)` | 同一人对同一模式只贡献一次 |

## 为什么本模块不带 feature flag

与 `app/services/trip_cluster.py` 同理：这里没有 IO、没有模型调用，
切与不切只由入参决定。开关放在"要不要调用它"的调用点（`app/api/v1/recommend.py`），
塞进纯函数会让同一个函数在不同开关下行为不同，单测就没法钉死。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.schemas.policy import K_ANONYMITY, MAX_SEQUENCE_STEPS
from app.utils.semantic_tags import SEMANTIC_TAGS, is_semantic_tag

# `K_ANONYMITY` / `MAX_SEQUENCE_STEPS` 的真相源已上移到 `app/schemas/policy.py`：
# schemas 需要它们做 `max_length` 与响应默认值，而 `schemas → services` 是反向依赖。
# 这里保留 import + 同名符号，调用方（api / tests）无需改 import 路径。

#: **评分过滤阈值**：平均分低于 4.0 的行程不贡献评分（设计文档 §9.2）。
#:
#: 为什么评分也要过滤：低分行程意味着"这条路线并不好"，把它统计进 `avg_score`
#: 会让推荐结果赞美一条用户其实不喜欢的路线。注意它只过滤**评分**，
#: 不影响 `support` —— 支持度衡量"有多少人这么走"，与"他们觉得好不好"是两件事。
MIN_AVG_SCORE = 4.0


class _TaggedPlace(Protocol):
    """任何带 `tag` 字段的地点对象（ORM 的 Place、DTO、测试替身都算）。

    用 `Protocol` 而不是 `Any` 或某个具体 ORM 类：`services` 层不该认识 ORM 的业务字段
    （见 `app/services/trip_cluster.py` 里 `ClusterItem` 的同一做法），
    否则 `Place` 加一个字段就要改这里的类型，单测还得造 ORM 实例。
    """

    tag: str | None


@dataclass(frozen=True)
class SemanticPattern:
    """一条群体语义模式：「见过 `prefix` 的人，下一个去的是 `next_tag`」。

    不可变（`frozen=True`）是刻意的：`support` / `score_sum` 的累加通过
    `dataclasses.replace()` 生成新实例，聚合器里持有的永远是快照，
    不存在"半个请求改了一半状态"的中间态。
    """

    #: 历史语义序列，如 `("heritage",)`。长度至少 1（`submit` 不生成空前缀：
    #: 空前缀意味着"所有人的下一步"，那是热门榜不是个性化推荐，且必然达标、没有信息量）。
    prefix: tuple[str, ...]

    #: 前缀之后实际出现的语义标签
    next_tag: str

    #: 该 `(prefix, next_tag)` 的全局出现次数（**精确值，绝不外传**，见 `recommend` 的 docstring）
    support: int

    #: 评分总和（只累加 `>= MIN_AVG_SCORE` 的）
    score_sum: float

    #: 评分样本数
    score_n: int

    @property
    def avg_score(self) -> float:
        """平均评分；没有任何评分样本时为 `0.0`。

        返回 `0.0` 而不是 `None`：这个属性要参与**排序**（`recommend` 的平票裁决），
        `None` 会让排序键变成不可比较类型。要不要"没有评分"这件事，
        由 `SemanticRecommendation.avg_score`（`float | None`）对外表达。
        """
        if self.score_n <= 0:
            return 0.0
        return self.score_sum / self.score_n

    @property
    def published(self) -> bool:
        """**k-匿名门禁**：`support < K_ANONYMITY` 的模式一律不可发布。"""
        return self.support >= K_ANONYMITY


@dataclass(frozen=True)
class SemanticRecommendation:
    """一条对外可见的推荐结果。**刻意不含 `support`**。"""

    tag: str
    confidence: float
    avg_score: float | None


def validate_sequence(tags: Sequence[str]) -> tuple[str, ...]:
    """过滤掉非六类语义标签，只保留合法部分。

    **静默丢弃而不是抛错**：匿名上报里混进未知标签是常态（新词表还没下发、
    旧版本客户端、上游抽取失败写了个空串）。为了一个脏标签拒绝整条上报并不划算 ——
    丢掉的是一条本可以贡献给匿名集的数据，而 k-匿名门禁最缺的就是样本量。
    真正需要报错的是"过滤完什么都不剩"（那是请求非法，由 API 层翻译成 422）。
    """
    return tuple(tag for tag in tags if is_semantic_tag(tag))


def truncate_sequence(tags: Sequence[str], max_steps: int = MAX_SEQUENCE_STEPS) -> tuple[str, ...]:
    """校验 + 截断：**超长时保留最后 `max_steps` 步**。

    为什么保留**后段**而不是前段：最近的行为对"下一步"最有预测力 —— 一个人三天前看人文、
    昨天看自然，推荐该跟"自然"走。反过来保留前段，老用户会永远带着一长串历史开头，
    这个前缀几乎不可能与别人重合，等于把截断做的泛化又赔回去了（而且还更难匹配）。

    非法标签顺手丢弃（复用 `validate_sequence`），所以本函数可以单独作为"归一化入口"：
    上报与查询两侧都过它，前后缀口径才一致。
    """
    if max_steps <= 0:
        return ()
    clean = validate_sequence(tags)
    if len(clean) <= max_steps:
        return clean
    return clean[-max_steps:]


def extract_sequences(places: Sequence[_TaggedPlace]) -> list[tuple[str, ...]]:
    """从一个 Trip 的地点序列抽出语义序列（**合并相邻的相同标签**）。

    `heritage → heritage → nature` 抽成 `("heritage", "nature")`：这是设计文档 §9.1
    里"洛阳"那条示例行程的抽象方式 —— 连着逛两个石窟和逛一个石窟，
    对"下一步去哪"的预测力是一样的，但前者会让模式库多出一条几乎无人命中的
    `(("heritage", "heritage"), "nature")`，白白稀释支持度。

    返回 `list` 而不是单条：抽取**可能一条都得不到**（整趟行程的地点全是未知标签），
    用 `None` 表达会逼调用方到处判空；空列表对
    `for seq in extract_sequences(places): aggregator.submit(seq)` 这种批量写法天然友好，
    将来按天切分成多条也不用改签名。
    """
    merged: list[str] = []
    for place in places:
        tag = place.tag or ""
        if not is_semantic_tag(tag):
            continue
        if merged and merged[-1] == tag:
            continue
        merged.append(tag)
    if not merged:
        return []
    return [truncate_sequence(merged)]


class SemanticAggregator:
    """内存级语义模式聚合器（设计文档 §9.2 的"逻辑聚合器"）。

    全部状态就两个字典 + 一个集合，**没有锁**：单进程单线程地累加，
    将来要并发就在这里加，调用方不用改。
    """

    def __init__(self) -> None:
        # 键 = `(prefix, next_tag)`，值 = 该模式的当前快照
        self._patterns: dict[tuple[tuple[str, ...], str], SemanticPattern] = {}
        # unique 约束的账本：`(user_key, pattern_key)`。只记"谁贡献过什么"，
        # 不记时间、不记原始序列 —— 否则账本本身就成了新的隐私负债。
        self._seen: set[tuple[str, tuple[tuple[str, ...], str]]] = set()
        self._submissions = 0

    def submit(
        self,
        sequence: Sequence[str],
        *,
        score: float | None = None,
        user_key: str | None = None,
    ) -> int:
        """上报一条匿名语义序列，返回**本次新增**的模式条目数。

        内部对序列的每个**真前缀**（长度 1..len-1）各记一条 `(prefix, next_tag)` 模式：
        `a → b → c` 生成 `(a, b)` 与 `((a, b), c)` 两条。为什么只取真前缀：
        最后一个标签后面没有"下一步"，拿它当前缀会记下一条永远不会被查询的死模式。

        - `user_key` 非空时，**同一 `user_key` 对同一 `(prefix, next_tag)` 只计一次**
          （设计文档 §9.2 的 `unique(user_id, round_id, pattern_version)` 防刷约束）。
          没有它，一个人反复上报同一条行程就能单方面把某模式刷过门禁 ——
          门禁防的是"样本太少"，刷票恰好伪造了样本量。
        - `score` 为 `None` 时不参与评分统计；有值时 `>= MIN_AVG_SCORE` 才计入
          `score_sum / score_n`（设计文档的"评分过滤"：低分行程不配推荐给别人，
          但它仍然算一次真实到访，所以照常计入 `support`）。
        - 序列有效长度 `< 2` 直接返回 `0`：没有"下一步"可学。
        """
        tags = truncate_sequence(sequence)
        if len(tags) < 2:
            return 0

        self._submissions += 1
        if score is not None and score >= MIN_AVG_SCORE:
            score_delta = float(score)
            score_step = 1
        else:
            score_delta = 0.0
            score_step = 0
        added = 0

        for step in range(1, len(tags)):
            prefix = tags[:step]
            next_tag = tags[step]
            key = (prefix, next_tag)

            if user_key:
                marker = (user_key, key)
                if marker in self._seen:
                    continue
                self._seen.add(marker)

            current = self._patterns.get(key)
            if current is None:
                self._patterns[key] = SemanticPattern(
                    prefix=prefix,
                    next_tag=next_tag,
                    support=1,
                    score_sum=score_delta,
                    score_n=score_step,
                )
            else:
                self._patterns[key] = replace(
                    current,
                    support=current.support + 1,
                    score_sum=current.score_sum + score_delta,
                    score_n=current.score_n + score_step,
                )
            added += 1
        return added

    def recommend(self, sequence: Sequence[str]) -> SemanticRecommendation | None:
        """查"该序列之后最常出现的语义标签"；**无达标模式时返回 `None`**。

        **k-匿名红线（本模块最重要的一条不变量）**：`support < K_ANONYMITY` 的模式
        一律不参与推荐；所有候选都不达标则返回 `None`。宁可什么都不推荐，
        也不返回一条只由两三个人支撑的"规律" —— 后者等于把这几个人的行为
        当成群体共识公开出去，正是 §9 要防的。

        候选排序（三级，全确定性）：
        1. `support` 降序 —— 最常被走的下一步
        2. `avg_score` 降序 —— 支持度相同时，走得开心的人更多
        3. `SEMANTIC_TAGS` 顺序 —— 前两级都平票时的裁决，与前端 `ALL_TAGS` 一致，
           保证同一份模式库在任何机器上给出同一个答案

        `confidence` = `该模式 support / 该前缀下全部（含未达标）模式 support 之和`，
        上限 1.0。**它是一个相对占比，不代表样本量、也不泄露参与人数**：
        外部拿到 `0.6` 无法反推背后是 3 人还是 3000 人（分子分母都不出网）。
        设计文档 §9.2 要求"前端展示 confidence，不展示精确 support"，本函数从
        **数据结构上**就不把 `support` 带出去，杜绝了上游手滑把它塞进响应的可能。
        """
        prefix = truncate_sequence(sequence)
        candidates = [pattern for (p_prefix, _), pattern in self._patterns.items() if p_prefix == prefix]
        if not candidates:
            return None

        # 分母用**全部**候选（含未达标的）：分子达标而分母含未达标，
        # confidence 就会低于"仅看达标模式"的占比 —— 这是诚实的：
        # 它反映的是"在观测到的全部走向里，这条占多少"，而不是"在幸存者里占多少"。
        total_support = sum(pattern.support for pattern in candidates)
        eligible = [pattern for pattern in candidates if pattern.published]
        if not eligible or total_support <= 0:
            return None

        ranked = sorted(
            eligible,
            key=lambda p: (-p.support, -p.avg_score, SEMANTIC_TAGS.index(p.next_tag)),
        )
        best = ranked[0]
        return SemanticRecommendation(
            tag=best.next_tag,
            confidence=min(best.support / total_support, 1.0),
            avg_score=best.avg_score if best.score_n > 0 else None,
        )

    def stats(self) -> dict[str, int]:
        """聚合器体检指标（排障与测试用）：模式总数 / 已发布数 / 上报次数。

        只回计数、不回任何模式内容：它是给日志和断言看的，不需要知道"哪条模式"。
        """
        return {
            "patterns": len(self._patterns),
            "published": sum(1 for pattern in self._patterns.values() if pattern.published),
            "submissions": self._submissions,
        }

    def clear(self) -> None:
        """清空全部状态（测试用；也是"被遗忘权"在内存实现下的落点）。"""
        self._patterns.clear()
        self._seen.clear()
        self._submissions = 0


_AGGREGATOR: SemanticAggregator | None = None


def build_aggregator() -> SemanticAggregator:
    """模块级单例工厂：API 层用它（一次请求里所有上报汇到同一个匿名集）。

    单测请直接 `SemanticAggregator()`：单例会跨用例泄漏状态，
    而纯函数层的用例本就该各自从零开始。
    """
    global _AGGREGATOR
    if _AGGREGATOR is None:
        _AGGREGATOR = SemanticAggregator()
    return _AGGREGATOR
