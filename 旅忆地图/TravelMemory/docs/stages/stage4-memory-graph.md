# 阶段 4 · Memory + LangGraph（W4-1 / W4-2 / W4-3）

> 这一阶段要解决什么：**让 Agent 记住你**，并把"线性管道"换成"可回滚、可分支的状态机"。
> 出口条件见文末第 6 节。

设计文档依据：[`旅忆地图 TravelMemory.md`](../../旅忆地图%20TravelMemory.md) §4.1（数据模型）、
§7.1（管线）、§7.4（上下文融合与拒答）、§7.5（Memory 分层）、第十一章「阶段 4」。

---

## 1. 三块工作的关系

```
W4-1 记忆（数据 + 策略）  ──┐
                            ├──► W4-3 RRF + 上下文压缩
W4-2 LangGraph（编排）    ──┘
```

W4-1 与 W4-2 互不依赖（一个管数据、一个管编排），W4-3 需要 W4-2 先把"证据"这条路跑通。
单人按顺序做：**先 W4-1，再 W4-2，最后 W4-3**。

---

## 2. W4-1：三层记忆

### 2.1 三层分别装什么

| 层 | 常量 | 内容 | 默认半衰期 |
| --- | --- | --- | --- |
| 短期 | `short_term` | 当前会话 / 当前行程上下文 | 1 天 |
| 情景 | `episodic` | 某次旅行的事件摘要 | 30 天 |
| 语义 | `semantic` | 用户偏好、长期兴趣 | 90 天 |

设计文档 §7.5 原话："默认 `half_life_days = 30`（偏好类 90）"。短期层文档没给值，
本阶段取 **1 天** —— 会话内有效的东西，隔天再问就该重新确认。

### 2.2 衰减公式

```
weight(t) = w0 · exp(-λ · Δt_days)，λ = ln2 / half_life_days
weight < 0.05 或已过期 → 归档（**不物理删除**）
```

代码：`app/utils/decay.py`（纯函数，单测直接钉死）。

**三个容易做错的地方**，本阶段都踩过并有对应用例：

1. **Δt 以 `last_seen_at` 为起点，不是 `created_at`。**
   "不喜欢人多"是长期成立的偏好，按写入时间衰减的话，用户天天在用、
   却因为"三个月前说的"被归档，明显不合理。每次记忆真的进了上下文就 `touch` 一下，
   于是衰减衡量的是"多久没被用上"。
2. **Δt 必须允许小数。** 用 `int(days)` 的话，一天内的记忆 Δt 全是 0，
   短期层永远不会归档。用 `(now - t).total_seconds() / 86400`。
3. **时钟回拨要截断。** `exp(-λ · 负数)` 会算出 >1 的权重，
   等于"未来的记忆比刚说的更可信"。`days_since()` 里 `max(0.0, ...)`。

半衰期 → λ 的换算放在 `half_life_to_lambda()`：半衰期是人类能直觉判断的量
（"90 天后还剩一半"），λ = 0.0077 这种数字没人有感觉，调参只能瞎试。

### 2.3 表结构（迁移 0006）

```
MemoryItem     id, user_id, type, key, value, weight0, confidence,
               decay_rate, last_seen_at, expires_at, status,
               embedding, embedding_model, chain_commitment, created_at, updated_at
               UNIQUE(user_id, type, key)      ← upsert 的依据
MemorySource   id, memory_id → memory_item.id (CASCADE), source_type, source_id
```

两个设计选择：

- **`weight` 不落库**，只存 `weight0` 与 `decay_rate`。
  衰减是时间的函数，存快照第二天就是错的；实时权重由 `current_weight()` 现算。
- **`MemorySource` 是关联表不是数组列**。
  数组无法建外键、无法反查"这条记忆是哪张照片支撑的"，
  而引用率 100% 这条红线要求每条结论都能追到源。

`status` 只有 `active` / `archived` 两态，归档不删除 —— 用户重新提起旧偏好时
`touch` 一下就能复活，删掉就只能等他再说一遍。

### 2.4 写回策略（克制）

设计文档给了三条触发时机，本阶段的实现：

| 时机 | 方法 | 落哪层 |
| --- | --- | --- |
| 用户明确表达偏好 | `MemoryService.remember_from_text()` | semantic |
| 每 8 轮会话 | `MemoryService.write_session_summary()` | episodic |
| 当前行程上下文 | `MemoryService.write_memory(layer=short_term)` | short_term |

偏好抽取**用规则不用 LLM**（`app/agent/memory/extract.py`），三个理由：

1. 句式高度固定（"我不喜欢人多""我偏好自驾"），规则覆盖率足够
2. 写回发生在每一轮之后，用 LLM 等于每轮多烧一次调用
3. 确定性 —— 单测能钉死，不必 mock 大模型

克制原则写在代码里：**宁可漏抽，不可错抽**。
把"我今天心情不错"记成"用户喜欢心情不错"是灾难性噪声，所以疑问句、
代词、泛词一律返回 `None`。置信度 < 0.6 的记忆注入时带「（待确认）」标记。

### 2.5 注入上下文的位置（关键）

设计文档 §7.4 的固定顺序：

```
[系统指令]
[用户偏好 / 语义记忆]     ← 新增
[当前行程上下文]          ← 新增
[检索证据]                ← 带 [n] 编号
[群体语义先验]            ← 阶段 7
[对话历史] / [用户问题]
```

顺序**不能动**：把记忆放在证据之后，模型会误以为偏好也是可引用材料，
从而产出指向不存在证据的 `[n]`。

实现在 `app/agent/rag/context_builder.py`，`build_context(hits, memory=..., history=...)`。
`system.md` 同步加了一段「关于用户偏好区块」，明确"偏好只能用来调整建议口径，
不能当事实陈述、不要给它编号引用" —— 这是保护引用率 100% 的 prompt 侧防线。

---

## 3. W4-2：迁 LangGraph

### 3.1 核心决策：节点复用，不重写

`app/agent/nodes/graph/graph.py` 里的每个节点，都是对 `nodes/pipeline/*`
**同一批节点对象**的适配调用，参数在 `AgentState` ↔ `PipelineState` 间转换一次。

为什么不各写一份：

1. 出口条件要求两种运行时**评测结果可对比**。各写一份的话任何改动都会造成漂移，
   而漂移的表现是"指标悄悄变了"，极难归因。
2. **拒答红线只有一份实现**。引用率 100% 的硬校验在 `SelfCheckNode`，
   走同一份代码才能保证 graph 路径不会漏掉这道闸门。

代价是多一层转换（`_to_pipeline` / `_to_state_update`），换来的是"改一处两边都变"。

### 3.2 状态与滑动窗口

```python
class AgentState(TypedDict, total=False):
    user_id, session_id, query
    messages: Annotated[list[dict], _keep_last_turns]   # ← reducer 自动截断
    rewritten_query, parsed_query, evidences, blocks, context_text, answer, violations
    memory_preferences, memory_trip_context, memory_used_ids
    refused, refuse_reason, attempts, restarted, latency_ms, token_usage
```

`messages` 的 reducer 是重点：LangGraph 默认的 messages reducer **只增不减**，
聊 50 轮第 50 轮的 prompt 就带着前 49 轮。这里显式截断到最近 3 轮
（`SLIDING_WINDOW_TURNS = 3`），让窗口大小成为**状态定义的一部分**，
而不是散落在各节点里的约定。

3 轮的依据：指代消解（"那里的民宿"）需要的上下文几乎从不超过上一轮；
再长就是线性烧 token，还会让早期跑偏的对话持续污染后面。

### 3.3 条件边

```
rewrite → query_parse → retrieve → load_memory → tool_call → build_context
        → generate → self_check ─┬─ 无违规 ──────────────→ write_memory → END
                                 ├─ 有违规 且 还能重试 ──→ generate（再来一次）
                                 └─ 有违规 且 次数用尽 ──→ refuse → write_memory → END
```

`refuse` 是**显式节点**而不是"在 self_check 里改 state 后直接 END"：
拒答是一次有语义的状态变更（换话术、清空引用），它该在 trace 里留下独立一步，
否则排障时只看到"self_check 之后就没了"。

路由函数是纯函数 `route_after_self_check(state, max_retries)`，三条分支单测直接钉死。

### 3.4 运行时开关

```
AGENT_RUNTIME=pipeline   # 默认，阶段 0~3 的纯 Python 管道
AGENT_RUNTIME=graph      # 阶段 4 的 LangGraph
```

统一入口 `app/agent/runtime.py::build_agent()`。三条保证：

- 未知取值**回落 pipeline** 而不是报错（`resolve_runtime`）
- langgraph 没装 → `ImportError` 被捕获，记警告后回退 pipeline
- 两种运行时返回的对象都提供 `run()` / `run_stream()`，事件序列逐条一致，
  所以 `api/v1/agent.py` 里**没有任何分支**

为了让两条路径的事件完全一致，本阶段把 SSE 事件构造器从 `pipeline.py`
抽到了 `app/agent/events.py` —— 事件格式属于**对外契约**，不该跟着编排走。

### 3.5 流式：三种 stream_mode 各司其职

```python
graph.astream(initial, config, stream_mode=["updates", "custom", "values"])
```

- `updates`：知道**哪个节点**跑完了（在正确时机发 `parsed` / `evidence`）
- `custom`：generate 节点用 `get_stream_writer()` 逐段推 token（首字可见）
- `values`：拿到**完整状态**（更新只是增量，终态只能从这里拿）

---

## 4. W4-3：RRF 融合 + 上下文压缩

### 4.1 RRF

```
score(d) = Σ w_i / (k + rank_i)，rank 从 1 开始，k 默认 60，w_i 为各路权重
```

代码：`app/agent/rag/retriever.py::reciprocal_rank_fusion()`。

**为什么阶段 4 才上**：阶段 2 的归一化加权求和有个隐含前提 —— 两路分数可比。
这个前提很脆：只有 1 条结果时归一化返回 1.0（"它就是最好的"），
于是这一条的加权分恒等于权重上限，跟它到底有多像没关系。
阶段 3 加了 reranker、阶段 4 又加了记忆检索，三路以上量纲各不相同，
归一化口径会越补越乱。RRF 用**排名**而不是分数，天然对量纲免疫。

代价是丢掉了"领先多少"的信息。

切换方式：`RETRIEVAL_FUSION=rrf`（配置，不是代码分支）。
**默认是 `weighted`** —— 这个默认值不是保守，是被真实数据打出来的，见 4.3。

### 4.2 上下文压缩

优先级 **证据 > 历史对话 > 记忆**：

- 证据是引用率 100% 的物质基础，砍它等于直接制造拒答
- 历史对话提供指代消解线索，砍掉只会让回答略微"断片"
- 记忆是调味，砍掉回答依然成立，只是没那么个性化

三条硬约束：

1. **整块丢弃，绝不截断单行**。半句话的偏好（"用户不喜欢人"）比没有更糟。
2. **预算下限 `MIN_CONTEXT_CHARS = 200`**。配成 0 不是"极致压缩"，
   是把每条问答都变成拒答。
3. **历史里的 `[n]` 必须剥掉**。上一轮的编号指的是**上一轮的证据**，
   放进本轮上下文后模型照抄 `[3]`，而本轮可能只有 2 条证据 ——
   于是 self_check 报"引用越界"，用户看到一次莫名其妙的拒答。

降级保护：`_fit_memory` 整体 try/except，记忆装配炸了也只是"本次不带记忆作答"，
绝不把整条问答带崩（PROJECT.md §二、[8]）。

### 4.3 真实数据：RRF 未必优于 weighted（重要）

接入真实大模型（qwen3.7-plus + qwen3.7-text-embedding-flash）后的**全量消融**：

| 档位 | Recall@5 | MRR |
| --- | --- | --- |
| 纯向量 | 0.833 | 0.718 |
| +结构化过滤 | 0.857 | 0.722 |
| **+词面召回（weighted）** | **0.857** | **0.778** |
| +RRF 融合（等权） | 0.833 | 0.679 |

**结论：等权 RRF 在两个指标上都不敌 weighted，MRR 掉了 0.1。**

这不是实现 bug，是机制问题。真实语义向量的分数是**连续且区分度高**的
（0.83 vs 0.79 就是实打实的差距）；而词面覆盖率只有 1.0 / 0.5 / 0.0 **三档**。
RRF 只看排名，于是：

- 一条覆盖率 0.5 的弱匹配与一条 1.0 的强匹配，名次贡献几乎相同；
- 更糟的是**同分文档名次乱排**：1234 式排名让同一档内的文档仅仅因为
  SQL 返回顺序拿到不同名次。极端情况下
  "向量第 1 + 词面第 2" 与 "向量第 2 + 词面第 1" 的 RRF 分**完全相等**
  （`1/(k+1)+1/(k+2)` 交换律），谁在前取决于列表稳定性，也就是随机。

于是正确文档被挤下去，MRR 塌掉。

#### 因此做了两处修正

1. **加权 RRF**：`score(d) = Σ w_i/(k+rank_i)`，权重复用
   `vector_weight`（0.65）/ `lexical_weight`（0.35）—— 让"语义强、词面弱"
   这个事实体现在分数里，而不是被 RRF 的对称性抹平。
2. **竞争排名**（同分同名次，1224 式）：同一档的文档名次相同，
   谁前谁后交给有细粒度分数的那一路决定。

#### 为什么不把 k 从 60 调到 100/120

k 越大，名次差异越被抹平。提 k 确实能让上面的等式不再那么致命，
但它**同时把好的区分度也抹平**：头部与尾部的差距一起变小，MRR 该掉还是掉。
它治的是症状（分数太接近），不是病因（词面路的权重过大）。
所以 k 保持 RRF 原论文（Cormack et al. 2009）的 60，
需要更平滑时用 `RETRIEVAL_RRF_K` 显式调，并在消融里对比后再定。

#### 落地结论

- **`RETRIEVAL_FUSION` 默认 `weighted`**，真实数据下它就是当前最优；
- RRF 保留为**可选策略**：等三路以上（+ 记忆检索）量纲差异更大时，
  加权 + 竞争排名的 RRF 才更可能反超 —— 届时用 `make eval-ablation` 对比后再切；
- 别把"RRF 是更先进的算法"当成切换理由。**融合策略要用数据选，不能用新意选。**

---

## 5. 文件清单

| 文件 | 作用 | 对应知识点 |
| --- | --- | --- |
| `app/models/memory.py` | `MemoryItem` / `MemorySource` ORM | §4.1 数据模型 |
| `alembic/versions/0006_stage4_memory.py` | 迁移 0006 | 唯一约束 = upsert 前提 |
| `app/schemas/memory.py` | 层间契约 + `MemoryStorePort` | 依赖方向（契约放底层） |
| `app/utils/decay.py` | 衰减纯函数 | §7.5 衰减公式 |
| `app/agent/memory/extract.py` | 规则抽偏好 + 摘要时机 | 写回策略的克制 |
| `app/agent/memory/store.py` | 内存实现（无 DB 也能跑） | 端口与适配器 |
| `app/agent/memory/service.py` | `MemoryService` 读写门面 | 三层记忆 |
| `app/services/memory_service.py` | `SqlMemoryStore`（`ON CONFLICT` upsert） | 幂等写入 |
| `app/agent/rag/context_builder.py` | 固定顺序装配 + 预算压缩 | §7.4 上下文融合 |
| `app/agent/nodes/graph/state.py` | `AgentState` + 滑动窗口 reducer | LangGraph 状态 |
| `app/agent/nodes/graph/graph.py` | 图节点 + 条件边 + 流式 | §7.1 管线 |
| `app/agent/runtime.py` | `AGENT_RUNTIME` 开关 | 可回滚设计 |
| `app/agent/events.py` | 两种运行时共用的 SSE 事件 | 对外契约与编排解耦 |
| `app/agent/rag/retriever.py` | `fusion` 分发 + 加权/竞争排名 RRF | §W4-3 融合 |
| `tests/test_rrf_weighted.py` | 加权与分档排名的行为测试 | §4.3 |
| `app/agent/rag/inmemory.py` | 内存检索（从 tests 提到 app） | 离线可验收 |
| `app/agent/eval/runner.py` | 消融加第 4 档 + `--offline` | 出口条件自动判定 |

---

## 6. 怎么跑起来验证

```bash
# 全量测试
cd backend && python -m pytest -q          # 期望：全部通过（约 300 条）

# W4-3 出口条件：真实数据下的消融（需要数据库 + 已 make reindex）
python -m app.agent.eval.runner --ablation
# 真实大模型下的实测输出（2026-10-22，qwen3.7-plus + text-embedding-flash）：
#   纯向量（阶段 0）        0.833     MRR 0.718
#   +结构化过滤             0.857     MRR 0.722
#   +词面召回（混合）        0.857     MRR 0.778   ← 阶段 3 基线，当前最优
#   +RRF 融合（W4-3）      0.833     MRR 0.679
#   阶段 2 出口（混合 ≥ 纯向量）        ✓ 通过
#   W4-3 出口（RRF ≥ 阶段 3 基线）     ✗ 未达标 —— 见 §4.3，默认改回 weighted

# 离线占位模式下的消融（哈希向量，只用于验证链路，不代表质量）
python -m app.agent.eval.runner --ablation --offline

# 迁移（需要 Docker）
make up && make migrate     # 0006 会建 memory_item / memory_source

# 切到图运行时
AGENT_RUNTIME=graph make dev
```

> ⚠ 跑消融前必须先 `make check`：库里若混用多个 embedding_model，
> 纯向量档会莫名归零（症状极像检索坏了，实际是向量不同源）。

### 常见报错排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ImportError: cannot import name 'DocumentService'` | 先 import `app.services` 触发的循环导入 | 已修：`app/agent/rag/__init__.py` 把 `DocumentIndexer` 改成 PEP 562 惰性导出 |
| `langgraph 不可用，回退纯 Python 管道` | 依赖没装 | `pip install -r requirements.txt`；不装也能跑（默认就是 pipeline） |
| 记忆没进上下文 | 先看 trace 的 `build_context.dropped_parts` | 有 `[用户偏好]` 说明被预算挤掉了；调大 `CONTEXT_MAX_CHARS` 或 `MEMORY_MAX_ITEMS_PER_SECTION` |
| 答案莫名拒答 | 引用越界 | 检查历史里是否残留 `[n]`（`_fit_history` 已剥除，自定义 history 时留意） |

---

## 7. 踩过的坑 / 易错点

1. **契约放错层会直接违反依赖方向。** 记忆契约最初写在 `app/agent/memory/`，
   结果 `services` 要反向 import `agent`。改放 `app/schemas/memory.py` 后
   两边都向下依赖，方向就顺了 —— 和 `app/schemas/document.py` 一个道理。
   同理，衰减纯函数从 `agent/memory/decay.py` 挪到了 `app/utils/decay.py`。
2. **`TypedDict(total=False)` 不等于"键一定存在"。** `AblationFlags["fusion"]`
   在没声明的档位上直接 `KeyError`。要用 `.get()`。
3. **LangGraph 的 `stream_mode` 传 list 时类型很别扭**（三种 mode 返回类型不同，
   靠重载区分）。一次要三种只能按 `Any` 处理再自行判别。
4. **mypy 对 `Result.rowcount` 报错**：静态类型是 `Result`，运行时才是 `CursorResult`。
   用 `typing.cast(CursorResult[Any], result).rowcount`（沿用 `document_service` 的写法）。

---

## 8. 动手练习

1. 把 `MEMORY_HALF_LIFE_SEMANTIC` 改成 7 天，跑 `tests/test_memory.py`，
   看 `test_stale_memory_is_archived_and_excluded` 会怎么变 —— 想清楚为什么。
2. 把 `SLIDING_WINDOW_TURNS` 改成 1，跑多轮指代测试，看第几轮开始"忘记"上文。
3. 把 `RETRIEVAL_FUSION` 切成 `rrf` 再切回 `weighted`，各跑一次消融，
   对比 MRR —— 然后用 `RETRIEVAL_RRF_K` 把 k 从 60 改成 1 再改成 1000，
   体会 k 的平滑作用，以及为什么**调 k 治不了词面噪声**（§4.3）。
4. 把 `RETRIEVAL_LEXICAL_WEIGHT` 从 0.35 降到 0.15 跑消融，看词面路的
   影响力变化 —— 这是在为"将来三路（+记忆）时该给词面多少权重"做预演。
5. 把 `CONTEXT_MAX_CHARS` 调到 300，看 trace 里 `dropped_parts` 出现了哪些区块。
