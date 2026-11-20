# 引用审计协议原型

> 这不是产品实现，是**协议与评测骨架**的可运行验证。
> 目的只有一个：证明"结构检查（每句带 `[n]`）抓不到引用错配"这件事有多确定，
> 以及 claim 级审计在什么口径下可以被量化。

## 跑起来

```bash
python research/experiments/multi_agent_audit/run_demo.py
```

无第三方依赖、无需 API Key、无数据库。Python 3.11+。

## 它做了什么

```
回答 → 拆 claim → 确定性规则（数字/时间/地点）→ 规则判不了 → Verifier + Falsifier → 冲突 → Judge
```

- `audit_proto/contracts.py` 角色之间唯一的数据契约（生产应换 pydantic）
- `audit_proto/split.py` 句子拆分、引用抽取、风险标签与排序
- `audit_proto/checks.py` 三条零成本规则 + 字面覆盖率
- `audit_proto/council.py` 级联编排、并发、超时、预算、`span` 校验、降级
- `audit_proto/lexical.py` **占位**审计员（字面启发式），生产替换为 LLM
- `audit_proto/metrics.py` claim 级 P/R/F1、误伤率、未标记错误率

## 实测结果（8 条构造用例，18 条 claim）

| 策略 | Precision | Recall | F1 | 误伤率 | 未标记错误率 |
| --- | --- | --- | --- | --- | --- |
| 结构检查（现状） | 0.000 | 0.000 | 0.000 | 0.000 | **1.000** |
| 规则 + 审计（原型） | 1.000 | 0.778 | 0.875 | 0.000 | **0.167** |

结构检查的 0 命中不是实现 bug：8 条用例里每条坏句都带了合法引用编号，
它按定义就看不见这类错误。这正是 `self_check` 的能力边界。

## 原型唯一漏检：否定极性

`c4`（"你去年没能上老君山 [1]"，证据写的是"我们上去了"）两条 claim 都被判
`supported`。原因是字面覆盖率看不见否定词——去掉功能字之后，"上老君山"
和"老君山……上去了"几乎一样。

这条不是"再调调阈值"能修的，它是**方法本身的边界**，也是必须引入语义审计员的
直接证据。写进报告时应该原样保留，而不是把它从 demo 集里删掉。

## 它不能证明什么

- 不能证明接入 LLM 后指标会更好——`lexical.py` 只是下界；
- 8 条用例是**按错误类型构造**的，不是真实分布，不能当基准集；
- 没有覆盖多轮对话、记忆注入、工具证据（POI/路线）这几类来源。

## 接到主链路要改什么

| 原型 | 生产 |
| --- | --- |
| `LexicalBackend` | `core/llm.py` + `agent/prompts/audit/{verify,falsify,judge}.md` |
| `dataclass` | pydantic v2（需要 JSON Schema 约束输出） |
| `Evidence` | `PipelineState.blocks` 的适配层 |
| `print` 表格 | `agent/eval/runner.py --audit` + dashboard 卡片 |
| 无记忆 | `time_window` 来自 `ParsedQuery.time_range`，地点来自 `Place` |
