"""阶段 5 W5-1：eval_result.faithfulness（LLM-as-a-Judge 忠实度）。

## 列用途

`EvalResult.faithfulness` 记录 LLM-as-a-Judge 对**未拒答**答案的 0/1 判定：
回答里每句话是否都能在检索证据中找到支撑。它是引用率之外的第二个生成质量口径 ——
引用率只查"标了 [n] 没有"，忠实度查"标了 [n] 的那句话是不是证据真的说了"。

## 为什么默认 None（而不是 NOT NULL DEFAULT 0）

judge 默认关闭（`EVAL_LLM_JUDGE=false`，见 `app/core/config.py`）：
一次全量评测会多出与用例等量的 LLM 调用，而输出只有 1 bit。
默认关闭意味着**存量行与新跑的行都大面积没有这个值**；
存成 0 会把"没评过"混进"评为幻觉"，忠实度均值直接失真。
None 的语义是"未评"，聚合时跳过（见 `eval/metrics.py::_mean`）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("eval_result", sa.Column("faithfulness", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_result", "faithfulness")
