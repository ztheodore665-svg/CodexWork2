"""跑通协议并出对比表：结构检查（现状） vs 规则+审计（提案）。

用法（无需任何依赖、无需 API Key）：
    python research/experiments/multi_agent_audit/run_demo.py

它证明的是**评测骨架与协议可运行**，不是最终效果：
`LexicalBackend` 只是 LLM 审计员的占位实现，数字仅用于说明
"结构检查抓不到引用错配"这件事有多确定。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from audit_proto.contracts import Evidence  # noqa: E402
from audit_proto.council import audit  # noqa: E402
from audit_proto.lexical import LexicalBackend  # noqa: E402
from audit_proto.metrics import leak_rate, score_claims  # noqa: E402
from audit_proto.split import split_claims  # noqa: E402

DATA = Path(__file__).parent / "data" / "demo_cases.jsonl"
_CITATION_RE = re.compile(r"\[(\d+)\]")


def structural_bad(claim_text: str, evidence_ids: set[str]) -> bool:
    """项目当前的 self_check 口径：每句带 [n]，且编号不越界。"""
    indices = [int(i) for i in _CITATION_RE.findall(claim_text)]
    if not indices:
        return True
    return any(str(i) not in evidence_ids for i in indices)


def attach_gold(claims, gold: list[dict]) -> dict[str, bool]:
    """把 gold 的 match 子串对到 claim 上。对不上直接报错，不静默跳过。"""
    mapping: dict[str, bool] = {}
    for item in gold:
        hits = [c for c in claims if item["match"] in c.plain_text]
        if not hits:
            raise SystemExit(f"gold 未匹配到任何 claim：{item['match']!r}")
        if len(hits) > 1:
            raise SystemExit(f"gold 匹配到多条 claim：{item['match']!r}")
        mapping[hits[0].claim_id] = item["label"] != "supported"
    return mapping


async def main() -> None:
    cases = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    backend = LexicalBackend()

    claim_pairs: dict[str, list[tuple[bool, bool]]] = {"structural": [], "council": []}
    leak_rows: dict[str, list[dict]] = {"structural": [], "council": []}
    tn: dict[str, int] = {"structural": 0, "council": 0}
    total_calls = 0
    degraded_cases = 0

    print(f"{'用例':<5}{'claim':<7}{'结构检查':<10}{'审计结论':<14}{'动作':<10}{'依据'}")
    print("-" * 96)

    for case in cases:
        evidence = [Evidence(**item) for item in case["evidence"]]
        claims = split_claims(case["answer"])
        gold_bad = attach_gold(claims, case["gold"])

        result = await audit(
            case["question"],
            case["answer"],
            evidence,
            backend=backend,
            time_window=tuple(case["time_window"]) if case.get("time_window") else None,
            evidence_scores={item.evidence_id: 0.8 for item in evidence},
        )
        total_calls += result.calls
        degraded_cases += 1 if result.degraded else 0

        decisions = {d.claim_id: d for d in result.decisions}
        evidence_ids = {item.evidence_id for item in evidence}
        case_leak = False
        for claim in claims:
            decision = decisions[claim.claim_id]
            is_gold_bad = gold_bad.get(claim.claim_id, False)
            struct_bad = structural_bad(claim.text, evidence_ids)
            # 产品口径：只有"原样交给用户"才算没被发现；标存疑/标错都算已标记
            pred_bad = decision.action != "keep"
            leaked = is_gold_bad and decision.action == "keep"
            case_leak = case_leak or leaked

            claim_pairs["structural"].append((is_gold_bad, struct_bad))
            claim_pairs["council"].append((is_gold_bad, pred_bad))
            for policy, flag in (("structural", struct_bad), ("council", pred_bad)):
                if not is_gold_bad and not flag:
                    tn[policy] += 1

            mark = "✓" if is_gold_bad == pred_bad else "✗"
            print(
                f"{case['id']:<5}{claim.claim_id:<7}{('坏' if struct_bad else '过'):<11}"
                f"{decision.verdict.value:<14}{decision.action:<10}"
                f"{mark} {decision.note or decision.span[:24]}"
            )

        has_bad = any(gold_bad.values())
        leak_rows["structural"].append({"has_bad": has_bad, "leaked": has_bad})
        leak_rows["council"].append({"has_bad": has_bad, "leaked": case_leak})
        print()

    print("=" * 92)
    print(f"{'策略':<14}{'Precision':>10}{'Recall':>9}{'F1':>8}{'误伤率':>9}{'未标记错误率':>13}")
    for policy in ("structural", "council"):
        score = score_claims(claim_pairs[policy])
        false_flag = score.fp / (score.fp + tn[policy]) if (score.fp + tn[policy]) else 0.0
        print(
            f"{policy:<14}{score.precision:>10.3f}{score.recall:>9.3f}{score.f1:>8.3f}"
            f"{false_flag:>9.3f}{leak_rate(leak_rows[policy]):>13.3f}"
        )
    print("-" * 92)
    print(f"审计调用次数合计 {total_calls}（批量 2~3 次/用例）；降级用例 {degraded_cases}")
    print("注：council 行为 LexicalBackend 占位实现，数字只用于验证评测口径，不代表接入 LLM 后的效果。")


if __name__ == "__main__":
    asyncio.run(main())
