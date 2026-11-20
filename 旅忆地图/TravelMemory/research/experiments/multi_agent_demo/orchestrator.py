"""一个能跑的多 Agent 最小例子：前台 + 三个专员 + 一个校对员。

它不是产品代码，是**教学模型**：把"多 Agent"拆成四个能看懂的动作——
路由、交接、共享黑板、质检。跑一遍只需要 Python，不需要任何依赖。

对应到真实系统（见 README 的映射表）：
    前台      → QueryParseNode + 意图分类
    时间线管家 → L0 本机结构化查询
    总结员    → L1 薄引擎（GenerateNode）
    规划师    → L2 规划类生成
    校对员    → SelfCheckNode + 审计
    黑板      → PipelineState
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 共享黑板
@dataclass
class Board:
    """所有 Agent 读写同一块黑板。

    为什么用黑板而不是让 Agent 互相调用：
    互相调用会形成一张网，出问题无法定位"是谁改了它"；
    黑板是星形结构，每一步都能打印出来，最适合教学与排查。
    """

    query: str
    intent: str = ""
    evidence: list[str] = field(default_factory=list)
    draft: str = ""
    notes: list[str] = field(default_factory=list)
    rounds: int = 0
    #: 求证失败时主动拒答。拒答不是错误，是一种正确输出（见真实系统的 self_check）。
    refused: bool = False


# ---------------------------------------------------------------- Agent 基类
class Agent:
    name = "agent"
    #: 这个 Agent 需不需要"每句带引用"。规划/建议类不需要，事实类必须。
    requires_citation = True

    def can_handle(self, query: str) -> bool:
        raise NotImplementedError

    def handle(self, board: Board) -> None:
        raise NotImplementedError

    def reason(self, query: str) -> str:
        return "命中关键词"


# ---------------------------------------------------------------- 三个专员
class TimelineAgent(Agent):
    """时间线管家：只查本机索引，不做措辞，不调模型。"""

    name = "时间线管家"

    def can_handle(self, query: str) -> bool:
        return any(word in query for word in ("哪些", "哪几个", "几个", "哪年", "去过哪"))

    def reason(self, query: str) -> str:
        return "问的是枚举/计数，本机索引就能答"

    def handle(self, board: Board) -> None:
        # 真实系统里这里是 IndexedDB / SQL 查询；演示用固定数据
        board.evidence = [
            "2024-10-02 乌镇：西栅夜景，江南水乡古镇。",
            "2024-10-03 西塘：烟雨长廊，典型的水乡古镇。",
        ]
        board.draft = "你去年秋天去了乌镇 [1] 和西塘 [2]。两处都是江南水乡古镇 [1][2]。"
        board.intent = "fact"


class SummarizerAgent(Agent):
    """总结员：需要措辞，所以走云端；但只带少量证据。"""

    name = "总结员"

    def can_handle(self, query: str) -> bool:
        return any(word in query for word in ("总结", "像在哪", "风格", "变化"))

    def reason(self, query: str) -> str:
        return "需要归纳和措辞，本机规则写不出来"

    def handle(self, board: Board) -> None:
        board.evidence = [
            "2024-10-02 乌镇：西栅夜景，临水木屋。",
            "2024-10-05 杭州：西湖边散步，灵隐寺上香。",
            "2024-10-06 行程统计：共 2 个城市、5 个景点。",
        ]
        if board.rounds:
            # 第二轮：按校对意见做"句子级手术"——删掉没有证据支撑的那句，
            # 而不是把整段重写（重写会把已经正确的内容也换掉）
            board.draft = (
                "这趟的主线是江南水乡与城市散步 [1]。"
                "你在乌镇住的是临水木屋 [1]，在杭州主要逛了西湖与灵隐寺 [2]。"
            )
            return
        # 第一轮：故意留一句没有引用的话，让校对员抓出来
        board.draft = (
            "这趟的主线是江南水乡与城市散步 [1]。"
            "你在乌镇住的是临水木屋 [1]，在杭州主要逛了西湖与灵隐寺 [2]。"
            "整体节奏很慢，适合放松。"
        )
        board.intent = "fact"


class PlannerAgent(Agent):
    """规划师：输出的是建议，不是事实，所以不要求逐句引用。"""

    name = "规划师"
    requires_citation = False

    def can_handle(self, query: str) -> bool:
        return any(word in query for word in ("规划", "安排", "周末", "路线"))

    def reason(self, query: str) -> str:
        return "问的是未来安排，不是已发生的事实"

    def handle(self, board: Board) -> None:
        board.evidence = ["参考：2024-10 那趟走过乌镇 → 西塘 → 杭州。"]
        board.draft = (
            "可以照着上次的节奏，安排一个两天一夜："
            "第一天下午到水乡，晚上看夜景；第二天上午逛老街，下午返程。"
            "如果你告诉我出发城市，我可以把路程时间也算进去。"
        )
        board.intent = "plan"


class FactCheckAgent(Agent):
    """求证员：专门回答"我真的去过吗"，找不到证据就主动拒答。"""

    name = "求证员"
    requires_citation = False

    def can_handle(self, query: str) -> bool:
        return any(word in query for word in ("吗", "是不是", "有没有", "真的"))

    def reason(self, query: str) -> str:
        return "问的是真伪，需要先在本机找证据，找不到就拒答"

    def handle(self, board: Board) -> None:
        # 演示：库里没有南极的行程，所以找不到证据
        matches = [item for item in [] if "南极" in item]
        if not matches:
            board.refused = True
            board.draft = (
                "抱歉，你的记录里没有南极的行程，所以这个问题我不能替你确认。"
                "如果你指的是别的旅行，可以说个城市或年份。"
            )
            return
        board.evidence = matches
        board.draft = " ".join(f"{text} [{i}]" for i, text in enumerate(matches, start=1))


# ---------------------------------------------------------------- 校对员
class AuditorAgent(Agent):
    """校对员：不生成，只检查。它决定这段话能不能交给用户。"""

    name = "校对员"
    requires_citation = False

    def check(self, board: Board, requires_citation: bool) -> list[str]:
        if board.refused:
            # 拒答的唯一要求：不许出现引用（"没有证据"和"根据 [1]"不能同时成立）
            return ["拒答里出现了引用编号"] if re.search(r"\[\d+\]", board.draft) else []
        issues: list[str] = []
        if not board.draft.strip():
            return ["回答为空"]
        if not requires_citation:
            # 规划类只查"编号不能瞎编"，不要求每句带引用
            return self._check_range(board, issues)
        for sentence in board.draft.replace("\n", "").split("。"):
            if not sentence.strip():
                continue
            if "[" not in sentence:
                issues.append(f"缺少引用：{sentence.strip()}")
        return self._check_range(board, issues)

    @staticmethod
    def _check_range(board: Board, issues: list[str]) -> list[str]:
        for raw in re.findall(r"\[(\d+)\]", board.draft):
            if not 1 <= int(raw) <= len(board.evidence):
                issues.append(f"引用编号越界：[{raw}]（本轮只有 {len(board.evidence)} 条证据）")
        return issues


# ---------------------------------------------------------------- 前台
@dataclass
class RunResult:
    answer: str
    agent: str
    rounds: int
    passed: bool


class Orchestrator:
    """前台：选人、交接、盯着质量、决定什么时候停。"""

    def __init__(self, agents: list[Agent], auditor: AuditorAgent, *, max_rounds: int = 2) -> None:
        self.agents = agents
        self.auditor = auditor
        self.max_rounds = max_rounds

    def route(self, query: str) -> Agent | None:
        for agent in self.agents:
            if agent.can_handle(query):
                return agent
        return None

    def run(self, query: str, *, verbose: bool = True) -> RunResult:
        def say(who: str, text: str) -> None:
            if verbose:
                print(f"[{who}] {text}")

        board = Board(query=query)
        say("前台", f"收到：{query}")

        agent = self.route(query)
        if agent is None:
            say("前台", "没有专员能接，说明这不在能力范围内")
            return RunResult(
                answer="这件事不在我能做的范围里（我只管你自己的旅行记忆），订票请用航司或订票平台。",
                agent="无",
                rounds=0,
                passed=True,
            )

        say("前台", f"路由 → {agent.name}（{agent.reason(query)}）")
        agent.handle(board)
        say(agent.name, f"产出草稿：{board.draft}")
        say("校对员", f"开始检查（{'要求逐句引用' if agent.requires_citation else '只查编号越界'}）")

        if board.refused:
            say("校对员", "通过（这是一次主动拒答，不含引用）")
            say("前台", "输出拒答")
            return RunResult(answer=board.draft, agent=agent.name, rounds=0, passed=True)

        issues = self.auditor.check(board, agent.requires_citation)
        board.rounds = 1
        while issues and board.rounds < self.max_rounds:
            say("校对员", f"不通过：{issues[0]}")
            say("前台", f"打回 {agent.name} 重做（第 {board.rounds + 1} 轮）")
            agent.handle(board)  # 真实系统里是"带着问题重写"，这里用同一次产出模拟
            board.rounds += 1
            issues = self.auditor.check(board, agent.requires_citation)

        if issues:
            say("校对员", f"两轮都没过：{issues[0]}")
            say("前台", "拒答，不把没根据的话交给用户")
            return RunResult(
                answer="抱歉，这次生成的回答里有没能落到证据的内容，我不把它说给你听。",
                agent=agent.name,
                rounds=board.rounds,
                passed=False,
            )

        say("校对员", "通过")
        say("前台", f"输出（共 {board.rounds} 轮）")
        return RunResult(answer=board.draft, agent=agent.name, rounds=board.rounds, passed=True)


def demo() -> None:
    orchestrator = Orchestrator(
        agents=[TimelineAgent(), SummarizerAgent(), PlannerAgent(), FactCheckAgent()],
        auditor=AuditorAgent(),
    )
    questions = [
        "我去年秋天去了哪些古镇？",
        "帮我总结一下这趟旅行的风格。",
        "照着上次那趟，帮我规划一个类似的周末。",
        "我在南极看过企鹅吗？",
        "帮我订一张去巴黎的机票。",
    ]
    for index, question in enumerate(questions, start=1):
        print(f"\n{'=' * 60}\n场景 {index}")
        result = orchestrator.run(question)
        print(f"→ 交给人看的答案：{result.answer}")


if __name__ == "__main__":
    demo()
