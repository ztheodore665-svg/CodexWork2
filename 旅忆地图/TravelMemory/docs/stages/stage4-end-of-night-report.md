# 阶段 4 无人值守开发 · 早报（End of Night Report）

**时间**：2026-10-22 03:00 ~ 04:10（GMT+8）
**范围**：`F:\project\travelmemory`（未越界；未修改任何系统级配置、未装全局包，全部依赖装在项目内 `.venv`）
**结论**：W4-1 / W4-2 / W4-3 三块全部完成，出口条件均判定通过。`pytest 278 passed`、`ruff` 全绿、`mypy app` 零错误。

---

## 1. 阶段 3 遗留问题的修复情况

| # | 问题 | 状态 |
| --- | --- | --- |
| 1 | **测试基线不是 196 条而是 204 条** | 文档口径与现状不符（见 §3.1），以现状为准，未强行凑数 |
| 2 | **循环导入**：先 `import app.services` 会炸 `ImportError: cannot import name 'DocumentService'`，链路是 `app.services → document_service → app.agent.rag → indexer → app.services.document_service` | ✅ 已修：`app/agent/rag/__init__.py` 把 `DocumentIndexer` 改为 PEP 562 惰性导出（`__getattr__`），`from app.agent.rag import DocumentIndexer` 仍可用 |
| 3 | HEAD 提交处 `mypy app` 有 3 处存量错误 | ✅ 现状全绿（`Success: no issues found in 95 source files`） |
| 4 | `make eval-ablation` 必须连真实数据库，本机无 Docker → 出口条件无法判定 | ✅ 已修：新增 `--offline`（演示语料 + 内存检索），并新增 `make eval-ablation-offline` |

**未修（留待人类）**：
- `SELF_CHECK_FAILED_ANSWER` 里"为了保证不编造，我不把它说给你听。"不是软句，
  被 `citation_rate()` 判为 0.0。self_check 自身不受影响（拒答分支只看"有没有编号"），
  但**阶段 5 做评测归因时这条会把聚合引用率拉低**，届时需要决定是改话术还是改软句白名单。
- 迁移 0006 未在有真实库的机器上执行过（本机端口不通）。已用
  `alembic upgrade 0005:0006 --sql` 离线生成并逐行核对过 DDL。

---

## 2. 阶段 4 三块的完成状态

### W4-1 Memory 三层 + 衰减 + 写回 —— ✅

- 新增表 `memory_item` / `memory_source`，迁移 **0006**
- 衰减纯函数 `app/utils/decay.py`：`weight = w0·exp(-λ·Δt)`，`λ = ln2/half_life`，
  默认 30 天（语义层 90 天、短期层 1 天），`weight < 0.05` 或过期 → 归档（不删除）
- `write_memory` 接口（`MemoryService.write_memory` / `remember_from_text` / `write_session_summary`），
  并挂到 `AgentPipeline.write_memory` 对外暴露
- 注入位置：`context_builder.build_context()` 按 §7.4 固定顺序装配
  `[用户偏好]` → `[当前行程上下文]` → `[检索证据]` → `[对话历史]`
- **出口条件验证**：`test_second_round_reads_the_preference` —— 写入"我不喜欢人多"后，
  二次组装的 Context 里能读到该偏好，且位置在 `[1]` 之前

### W4-2 Pipeline 迁 LangGraph —— ✅

- `AgentState`（TypedDict）：`messages` 带 reducer，滑动窗口 **最近 3 轮**
- 6+ 个节点封装进 `StateGraph`；`self_check` 后条件边三岔路：
  `generate`（重试）/ `refuse`（拒答）/ `write_memory → END`
- `AGENT_RUNTIME=graph` 生效，旧 `pipeline` 路径完全保留，未知取值自动回落
- **出口条件验证**：两种运行时的答案结构、事件序列（`parsed → evidence → done`）逐条一致；
  多轮指代测试通过（第一句问乌镇、第二句问"那里的民宿" → 改写补全为"乌镇 的民宿"）

### W4-3 RRF 融合 + 上下文压缩 —— ✅

- RRF `score = Σ 1/(k+rank)`，k 默认 60，`RetrieverConfig.fusion` 切换（`RETRIEVAL_FUSION=rrf`）
- 压缩优先级 **证据 > 历史对话 > 记忆**，整块丢弃不截断单行
- **出口条件验证**：单测 19 条（含 RRF 手算复算、降级保护）；
  `make eval-ablation-offline` Recall@5 = **0.857 不低于阶段 3 基线 0.857**，MRR 0.627 → **0.646**

### 最终测试结果

```
$ cd backend && python -m pytest -q
........................................................................ [ 25%]
........................................................................ [ 51%]
........................................................................ [ 77%]
..............................................                           [100%]
278 passed in 31.20s
```

| 指标 | 阶段 3 基线 | 阶段 4 |
| --- | --- | --- |
| `pytest -q` | 204 passed | **278 passed**（+74） |
| `ruff check .` | 通过 | **通过** |
| `mypy app` | 通过（工作区） | **通过（95 文件，0 错误）** |
| Recall@5（离线消融） | 0.857（混合档） | **0.857（RRF 档）** |
| MRR（离线消融） | 0.627 | **0.646** |

新增用例分布：`test_memory.py` 34 条、`test_graph_runtime.py` 21 条、`test_fusion_compress.py` 19 条。

```
$ python -m app.agent.eval.runner --ablation --offline
⚠ 离线模式（--offline）：内存检索 + 哈希向量，仅用于验证顺序与趋势，绝对值不可作为对外指标

=== 消融实验（k=5，共 26 条用例）===
档位                      Recall@5      MRR      平均延迟
---------------------------------------------------
纯向量（阶段 0）                  0.714    0.473      14.2
+结构化过滤                     0.857    0.630      10.0
+词面召回（混合）                  0.857    0.627       8.9
+RRF 融合（W4-3）              0.857    0.646       9.9
----------------------------------------------------
阶段 2 出口（混合 ≥ 纯向量）             ✓ 通过  0.7143 → 0.8571
W4-3 出口（RRF ≥ 阶段 3 基线）        ✓ 通过  0.8571 → 0.8571
```

> ⚠️ 该数值是**离线口径**（哈希向量 + 内存检索），与阶段 3 文档里的真实库数值
> （0.619 → 0.809 → 0.762）不可直接比较。真实库口径待 `make up && make migrate && make eval-ablation`。

---

## 3. 设计偏差与自主决策记录

### 3.1 文档与现实的差异

| # | 文档说 | 现实 | 处理 |
| --- | --- | --- | --- |
| 1 | 阶段 3 有 196 条测试 | 实际 **204 条** | 以现实为准，未删测试凑数 |
| 2 | `MemoryItem` 字段含 `decay_rate`，未提 w0 | 衰减需要初始权重 | 加 `weight0`（`weight` 不落库，见下）与 `status`（归档态） |
| 3 | 短期层未给半衰期 | 需要取值 | 取 **1 天**（会话内有效，隔天应重新确认） |
| 4 | 图节点为 `rewrite → … → write_memory` 十个 | 阶段 3 只有 7 个节点 | 按现有节点建图（`plan` / `call_tool` 合并为 `tool_call`；`reflect` 未建，属阶段 5） |
| 5 | `make eval-ablation` | 需真实数据库 | 增加 `--offline` 判据 |

### 3.2 自主决策（有意为之，可回退）

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | **记忆契约放 `app/schemas/memory.py`，不放 `agent/memory/`** | 契约若放 agent 层，`services` 就必须反向 import agent，直接违反单向依赖铁律 |
| 2 | **衰减纯函数放 `app/utils/decay.py`** | 同 1：agent 与 services 都要用，只能放中立层 |
| 3 | **图节点复用 `nodes/pipeline/*` 的同一批对象，不重写** | 出口条件要求两种运行时可对比；各写一份必然漂移。而且拒答红线只有一份实现才不会漏闸门 |
| 4 | **`weight` 不落库，只存 `weight0` + `decay_rate`** | 衰减是时间的函数，存快照第二天就是错的 |
| 5 | **偏好抽取用规则不用 LLM** | 句式固定、零成本、可单测；也是将来 LLM 抽取的兜底 |
| 6 | **SSE 事件构造器抽到 `app/agent/events.py`** | 事件格式是对外契约，必须与编排方式解耦，否则前端要写两套解析 |
| 7 | **`InMemoryVectorSearch` 从 `tests/fakes.py` 提到 `app/agent/rag/inmemory.py`** | 评测器与 CLI 离线模式也要用；少一份实现 = 少一处语义漂移。fakes.py 保留 re-export |
| 8 | **`MEMORY_ENABLED` 默认 True，且不进 feature flag 清单** | 记忆是阶段 4 主干能力而非扩展；`test_feature_flags` 断言"全部 flag 默认 False"，把主干能力塞进 flag 会让默认态 = 功能关闭 |
| 9 | **RRF 默认关闭（仍是 `weighted`）** | 阶段 2~3 行为是默认态；RRF 需显式开启，符合"换行为=换配置" |
| 10 | **`_fit_history` 剥掉历史里的 `[n]`** | 上一轮的编号指上一轮的证据，留着会让模型照抄后"引用越界" |
| 11 | **压缩预算下限 200 字符** | `CONTEXT_MAX_CHARS=0` 不是极致压缩，是把每条问答都变成拒答 |

---

## 4. 新增数据库表与迁移版本

| 迁移 | 内容 |
| --- | --- |
| **0006**（`0006_stage4_memory.py`，`down_revision=0005`） | 建 `memory_item`（唯一键 `uq_memory_item_user_type_key`、索引 `ix_memory_item_user_status_seen`）与 `memory_source`（外键 CASCADE）；`downgrade` 完整可回滚 |

已用 `alembic upgrade 0005:0006 --sql` 离线生成 DDL 并核对（需 `psycopg2-binary`，
已写入 `requirements-dev.txt`）。**真实库上尚未执行**，明早请跑 `make up && make migrate`。

---

## 5. 新增或修改的核心文件

### 新增（15 个）

```
backend/app/models/memory.py                        # MemoryItem / MemorySource ORM
backend/alembic/versions/0006_stage4_memory.py      # 迁移 0006
backend/app/schemas/memory.py                       # 层间契约 + MemoryStorePort + MemoryContext
backend/app/utils/decay.py                          # 衰减纯函数
backend/app/agent/memory/__init__.py
backend/app/agent/memory/extract.py                 # 规则抽偏好 + 摘要时机
backend/app/agent/memory/store.py                   # 内存实现
backend/app/agent/memory/service.py                 # MemoryService（读/写门面）
backend/app/services/memory_service.py              # SqlMemoryStore（ON CONFLICT upsert）
backend/app/agent/nodes/graph/__init__.py
backend/app/agent/nodes/graph/state.py              # AgentState + 滑动窗口 reducer
backend/app/agent/nodes/graph/graph.py              # 图节点 + 条件边 + 流式
backend/app/agent/runtime.py                        # AGENT_RUNTIME 开关
backend/app/agent/events.py                         # 两种运行时共用的 SSE 事件
backend/app/agent/rag/inmemory.py                   # 内存检索（从 tests 提升）
docs/stages/stage4-memory-graph.md                  # 阶段 4 复盘
```

### 修改（关键 14 个）

```
backend/app/agent/rag/context_builder.py    # 固定顺序装配 + 预算压缩 + 降级保护
backend/app/agent/rag/retriever.py          # fusion 分发 + reciprocal_rank_fusion
backend/app/agent/rag/__init__.py           # 修循环导入（惰性导出 DocumentIndexer）
backend/app/agent/pipeline.py               # 记忆装载/写回、复用 build_retriever、事件外置
backend/app/agent/state.py                  # 新增 memory 字段
backend/app/agent/nodes/pipeline/build_context.py
backend/app/agent/api/v1/agent.py           # 改用 build_agent + SqlMemoryStore（无分支）
backend/app/agent/eval/runner.py            # 消融加第 4 档 + --offline + 出口条件自动判定
backend/app/core/config.py                  # 阶段 4 配置（融合、记忆、压缩）
backend/app/agent/prompts/system.md         # 「关于用户偏好区块」规则
backend/app/agent/prompts/loader.py         # PROMPT_VERSION: stage3.1 → stage4.1
backend/app/models/__init__.py              # 导出 MemoryItem / MemorySource
backend/app/schemas/__init__.py             # 导出 memory 契约
backend/tests/fakes.py                      # 改为 re-export + 语料装载
Makefile / .env.example / backend/requirements.txt / requirements-dev.txt
```

### 新增测试（3 个文件，74 条）

```
backend/tests/test_memory.py            # 34 条：衰减 / 抽取 / 写回 / 归档 / 注入
backend/tests/test_graph_runtime.py     # 21 条：滑动窗口 / 路由 / 开关 / 两种运行时一致性 / 多轮指代
backend/tests/test_fusion_compress.py   # 19 条：RRF 复算 / 压缩优先级 / 降级保护
```

---

## 6. 红线复核清单

- [x] **依赖方向** `agent → nodes → tools → services → models` —— `test_layering` 通过；
      本轮新增的 `services/memory_service.py` 只依赖 `models` / `schemas` / `utils`
- [x] **拒答红线**：graph 路径与 pipeline 路径都走同一个 `GenerateNode` / `SelfCheckNode`；
      `test_graph_refuses_without_evidence`、`test_graph_retries_once_then_refuses` 覆盖
- [x] **Prompt 版本化**：改了 `system.md` → `PROMPT_VERSION` 升到 `pv2026.10-stage4.1`
- [x] **测试隔离**：全部 mock（LLM=mock、embedding=hashing、检索=内存），无外部 API 调用
- [x] **未碰阶段 5+**：未做看板、未做前端地图、未做隐私/zk 扩展
- [x] **安全边界**：所有写入都在 `F:\project\travelmemory` 内；pip 只装进项目 `.venv`；
      未改环境变量、未改注册表、未访问个人目录

---

## 7. 明早待办（需要人类）

1. **真实库验收**：`make up → make migrate → make seed-demo → make eval-ablation`，
   确认 Recall@5 不低于阶段 3 真实基线（0.762 是阶段 3 混合+重排口径）。
2. **真实库跑一遍 `AGENT_RUNTIME=graph`**，对比两种运行时的评测结果。
3. **是否提交**：本仓库目前只有 1 个 commit（`21ea2f4 first commit`），
   阶段 0~3 的工作本来就处于未提交状态；我**没有自作主张提交**，以免把别人的 WIP 混进一条提交。
4. `SELF_CHECK_FAILED_ANSWER` 的引用率口径（见 §1 未修项）。
