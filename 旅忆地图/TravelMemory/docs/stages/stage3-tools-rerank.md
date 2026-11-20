# 阶段 3：工具调用 + Reranker + 缓存

> 对应工作块：**W3-1 工具框架 / W3-2 地图工具 / W3-3 Redis 四级用途 / W3-4 Reranker + HNSW**
> 前置：阶段 0~2 已跑通

## 0. 读法

阶段 2 结束时，Agent 只能靠你的照片记忆回答。问"乌镇附近有什么景点"，它要么答不上来，
要么**用记忆编一段**——这违反"无证据必须拒答"的红线。

阶段 3 给它补上三件事：**能动手查地图**、**查过的结果能缓存**、**排序能再精修一次**。

建议这样读：

1. 第 1 节看出口条件的**真实判定结果**（都已在真实库上跑过，不是估算）
2. 第 2 节讲四个原理，其中"引用率红线怎么覆盖工具事实"是本章最核心的设计
3. 第 3~6 节逐块讲实现
4. 第 8 节有本轮踩到的真实 bug，每一个都带报错原文

---

## 1. 目标与出口条件（真实判定）

| 块 | 出口条件 | 判定方式 | 结果 |
| --- | --- | --- | --- |
| **W3-1** 工具框架 | 工具选择准确率 ≥ 0.9 | `make eval-tools`，22 条用例（17 正例 + 5 负例） | ✅ **22/22 = 1.0000** |
| **W3-2** 地图工具 | 返回高德 POI ID；转换有缓存与分批 | 单测断言 | ✅ POI ID 非空；120 点 → 3 批；二次转换命中缓存 |
| **W3-3** Redis 四级用途 | 缓存命中率 ≥ 60%；预算计数原子生效 | 单测（10 协程并发 INCR） | ✅ 原子性已验证；**真实命中率 ≥60% 待有真实工具调用量后统计** |
| **W3-4** Reranker + HNSW | 建索引后 Recall@5 不下降 | `make eval-ablation`（真实库） | ✅ 见下表 |

### 真实库消融结果（本人在真实 PostgreSQL 上实测，k=5，26 条用例）

```
档位                    Recall@5      MRR      平均延迟
-------------------------------------------------
纯向量（阶段 0）            0.619    0.410      81.4
+结构化过滤                0.809    0.532      70.6
+词面召回（混合）           0.762    0.531     104.5
```

**"不低于纯向量"达成**：混合档 0.762 > 纯向量 0.619。

但有一个必须说清的点：**混合档（0.762）比仅过滤档（0.809）低了 0.047**。
这不是笔误，也不是退化——它和阶段 2 复盘文档第 9 节记录的是同一个现象：
词面路的覆盖率只有 1.0 / 0.5 / 0.0 三档，归一化后大量文档被压到同一分数，
把向量路排好的顺序搅动了一下。Recall 没丢（证据还在），只是顺序变了。
真要修，方向是调 `retrieval_lexical_weight`（0.35 → 0.2）或等阶段 4 的 RRF（W4-3）。

HNSW 迁移 `0004 → 0005` 已在真实库执行成功。几十条数据量下**建索引后平均延迟反而变高**，
这符合 HNSW 特性：小数据量下图遍历的常数开销盖过了它省下的距离计算，
万级数据才会有收益。阶段 3 建它主要是为了让回退逻辑有真实对象可验证。

---

## 2. 必须先懂的四个原理

### 2.1 工具查到的事实，也必须满足引用率 100%

这是本章最重要的设计。PROJECT.md §二、[3] 要求事实性回答引用率 100%，
工具返回的事实同样是事实，**不能因为是"查来的"就豁免**。

做法是把工具结果包装成一条普通的 `SearchHit`，只是来源类型不同：

```python
SearchHit(
    ref=DocumentRef(source_type="map_tool", source_id=<poi_id>),
    content="乌镇东栅景区，位于嘉兴…；POI ID：B0FFH…。",
    metadata={"source": "map_tool", "coord_system": "GCJ-02"},
)
```

然后**追加到 `state.hits` 尾部**，`build_context` 照常连续编号：
记忆 `[1][2]` + 工具 `[3][4]`。

这样一来：

- `app/utils/citation.py`（引用正则）**不用改**
- `self_check`（越界/缺失校验）**不用改**
- `rag/context_builder.py`（编号与预算）**不用改**

引用率的硬校验天然覆盖了工具事实。被否掉的备选方案是给工具证据单独编 `[T1]` ——
那要同时改动上面三处和评测口径，风险高得多。

### 2.2 上下文预算：工具证据必须活下来

`build_context` 按列表顺序截断。工具证据在末尾 = 先被丢。
而模型如果仍然引用了 `[3]`，`self_check` 会判**越界** → 整段回答作废。

所以 `_append_with_budget()` 做了一件反直觉的事：超预算时**从记忆证据的尾部往回删**，
把位置让给工具证据。尾部本来就是分数最低的记忆证据，删它伤害最小。

### 2.3 mock LLM 下工具选择必须走规则

`MockLLM` 不会真的填 JSON。如果让"选哪个工具"也走 LLM，
20 条用例里 5 条负例会全部判错，准确率永远卡在 0.25，出口条件不可能达成。

所以规则选择不是"降级方案"，它是**一条必须存在的独立路径**：
真实 LLM 的输出要能被规则兜底（JSON 解析失败时落规则），规则自己也要可解释
（返回"命中了'多远'"而不只是返回工具名）。

### 2.4 HNSW 的风险要写成代码，不能写在注释里

pgvector 的 HNSW 是"先遍历图、后套 WHERE"，带过滤时可能返回 **0 条**。

所以 `ann_search` 被做成**独立方法**而不是 `similar_search` 的一个开关参数——
"这次走了 ANN 路径"在调用方代码里必须肉眼可见。而且候选数写成
`top_k × oversample`（乘数），不能写成 `max(top_k, top_k × oversample)`：
后者会让 `oversample=1` 失去意义，也就永远暴露不出"候选里没有符合条件的文档"这个真实风险。

---

## 3. W3-1：工具框架

`backend/app/tools/base.py` 定义 `BaseTool` / `ToolResult` / `ToolContext`。

框架把每个工具都要重复一遍的五件事收在一处：

| 必做项 | 漏掉之后的症状 |
| --- | --- |
| 入参 Pydantic 校验 | LLM 填的脏参数直接打给外部 API |
| 超时 | 外部 API 卡住 → 整条问答超时（P95 首字 < 3s 破线） |
| 有限重试 | 一次网络抖动就让工具"不可用" |
| 缓存 | 同一批坐标反复转换，配额烧光 |
| 调用日志 | "这个数字哪来的"变成悬案，命中率无法统计 |

**工具失败一律降级，绝不抛错**（PROJECT.md §二、[8]）。`ToolResult` 区分三种状态：

- `ok=True`：有结果（可能来自缓存）
- `ok=False, degraded=True`：工具没帮上忙，流程继续
- `ok=False, retryable=False`：入参不合法，**不重试**（同样的脏参数重试只是多烧一次配额）

重试只针对网络类异常（`TimeoutError` / `ConnectionError` / `OSError`）——
业务异常重试没有意义。

### 工具选择节点

`app/agent/nodes/pipeline/tool_call.py`，位置在 `retrieve` 之后、`build_context` 之前
（编号是在 `build_context` 里定的，之后再加证据就会出现"引用了不存在的 [n]"）。

`rule_select()` 优先级：**路线 → POI → 不调**。
"从这里怎么走到最近的停车场"判为路线，因为问句的核心是路径而不是停车场。

写负例时注意：**负例文本里不能出现触发词**。我第一版写了"帮我照着上次那趟再规划一个类似的**路线**"，
它命中了路线词，直接把准确率拉下一格——这种错误只有真跑评测才会发现。

---

## 4. W3-2：地图工具

`app/services/map_service.py` 垫了一层 service，而不是让工具直接调高德。换来三件事：

1. **provider 可替换**：没有高德 Key（本项目的默认状态）时走 `MockMapProvider`，
   返回结构完全一致的假数据，**包括 POI ID 的形状**（`B0FFH<hex>`，由城市+关键词+序号哈希生成，
   保证同一查询永远返回同一批 id，命中率统计才稳定）
2. **坐标系在这里收口**：高德返回 GCJ-02，库里存 WGS84。转换只在这个文件发生
3. **错误码翻译**：`10001` → "高德 key 无效"，而不是把 `{"errcode": ...}` 透传给 LLM

坐标转换的**分批是硬约束**：高德单次上限 40 点，120 点必须切成 3 批。
超了不是"变慢"，是**接口直接报错**。

缓存粒度是**整批**而不是单点：坐标转换按批收费，按点缓存会让"同一批 40 个点"
产生 40 次 miss，命中率好看但配额没省。

WGS84 ↔ GCJ-02 的数学在 `app/utils/coords.py`（纯函数，可单测）。
GCJ-02 → WGS84 没有解析形式的逆，用迭代逼近，3 次迭代误差已远小于 GPS 本身误差。

**当前状态：无高德 Key，停在 MockMapProvider。结构已对齐，阶段 6 接入时不需要改代码。**

---

## 5. W3-3：Redis 四级用途

`app/core/cache.py` 定义 `CacheClient` Protocol + 三个实现：

| 实现 | 用途 |
| --- | --- |
| `InMemoryCache` | 默认。TTL/原子自增/LRU 语义都能在无 Redis 的机器上完整验证 |
| `RedisCache` | 真实 Redis。`import redis.asyncio` 包在 `try/except ImportError` 里，**不装包也能 import 本模块** |
| `NullCache` | "缓存完全不参与"的基线，`make test-degradation` 要的就是它 |

四级用途与 TTL（唯一定义处，其他模块不许写字面量）：

| # | 用途 | TTL | 前缀 |
| --- | --- | --- | --- |
| ① | 外部 API 结果 | POI 7d / 路线 1d / 逆地编 30d | `api` |
| ② | Embedding 结果 | 永久（靠 LRU） | `emb` |
| ③ | 短期会话 | 24h（滑窗） | `sess` |
| ④ | 幂等锁 / 预算计数 | 48h | `lock` |

### 预算计数（④）

`BudgetGuard` 从进程内计数改成 Redis `INCR`。**为什么必须搬**：
起第二个实例（或 uvicorn 多 worker）时，每个进程各有一份计数，
实际花费可以是上限的 N 倍——而预算的作用恰恰是"在最坏情况下兜住成本"。

Redis 的 `INCR` 只认整数，成本却是小数，所以存**毫分**（cents × 1000）。

一个容易写错的点：`incr()` 在降级实现里返回 0，含义是"没有计数依据"，
**不是"花了 0 分"**。所以 `check()` 先 `ping()`，不通就退回本地计数——
宁可计数保守，也不能把算不清当成没花钱直接放行。

### Embedding 缓存（②）

`DocumentIndexer._embed_with_cache()`，key = `embedding_cache_key(内容, model_version)`。
**模型版本必须进 key**：只按内容哈希的话，换模型后会读到旧模型的向量——
那就是"脏向量"，表现是检索质量莫名变差，极难归因。

实测：第二次跑批的 provider 调用数 = **0**。

### 会话态（③）

`SessionStore` 只做**存储**（滑窗 20 条），不做策略。
短期记忆的保留策略属于阶段 4 W4-1——策略没定就先写策略，等于把未验证的假设固化进代码。

---

## 6. W3-4：Reranker + HNSW

### Reranker

`app/agent/rag/reranker.py`：`NoOpReranker`（默认）+ `RemoteReranker`。

融合决定"哪些进来"，重排决定"谁在最前面"。重排把 (query, 文档) 拼在一起送进模型，
能捕捉融合捕捉不到的东西：否定词、条件状语、问题与证据的类型错配。

**`NoOpReranker` 必须让顺序与阶段 2 逐条一致**——这是 `make test-degradation` 的核心断言。
如果"关掉重排"会改变顺序，那这个开关就不是降级开关，而是行为开关。

远程重排失败返回 `None` 表示"没做成"，调用方保留原顺序且**不丢证据**。

### HNSW 与回退

`DocumentService.ann_search()`：内层纯 ANN 子查询取 `top_k × oversample` 个候选，
外层才套结构化过滤。回退决策在 `Retriever`（命中 < `filter_min_hits` → 重新走
`similar_search` 精确扫描），并在 `retrieve_debug.index_path` 留下 `exact_fallback`。

**回退逻辑能在没有数据库的情况下被单测覆盖**，因为 `tests/fakes.py` 的
`InMemoryVectorSearch.ann_search()` 用的是同样的两步顺序。

迁移 `0005_stage3_hnsw_index.py`：`hnsw`、`m=16`、`ef_construction=64`、`vector_cosine_ops`，
**带 downgrade**（索引可以安全丢弃，丢掉后退化为精确扫描）。

---

## 7. 文件清单

| 文件 | 作用 | 块 |
| --- | --- | --- |
| `app/tools/base.py` | `BaseTool` / `ToolResult` / `ToolContext`：超时、重试、缓存、降级 | W3-1 |
| `app/tools/registry.py` | 注册表（重复注册报错，避免行为依赖 import 顺序） | W3-1 |
| `app/agent/nodes/pipeline/tool_call.py` | 工具选择 + 执行 + 证据并入 + 预算预留 | W3-1 |
| `app/agent/prompts/tool_select.md` | LLM 选工具的 prompt（不确定就填 none） | W3-1 |
| `data/tool_cases.jsonl` | 22 条工具选择用例（17 正例 + 5 负例） | W3-1 |
| `app/services/map_service.py` | 高德封装 + MockMapProvider + 坐标分批转换 | W3-2 |
| `app/utils/coords.py` | WGS84 ↔ GCJ-02（纯函数） | W3-2 |
| `app/tools/poi_tool.py` / `route_tool.py` | 两个具体工具 | W3-2 |
| `app/core/cache.py` | CacheClient 三实现 + TTL 常量 + SessionStore | W3-3 |
| `app/core/llm.py`（`BudgetGuard`） | 预算计数原子化（毫分 + Redis INCR） | W3-3 |
| `app/agent/rag/indexer.py` | Embedding 缓存（model_version 进 key） | W3-3 |
| `app/agent/rag/reranker.py` | NoOp / Remote 两种重排 | W3-4 |
| `app/agent/rag/ports.py` | 新增 `ann_search` 端口 | W3-4 |
| `app/services/document_service.py` | `ann_search`：内层 ANN、外层过滤 | W3-4 |
| `alembic/versions/0005_stage3_hnsw_index.py` | HNSW 索引（带 downgrade） | W3-4 |
| `frontend/src/pages/AgentPage/ToolCallBar.tsx` | "正在查地点…"状态条 | 前端 |
| `frontend/src/pages/AgentPage/ToolResultCard.tsx` | 结果卡片（标注来源 + 缓存命中徽章） | 前端 |
| `tests/test_tools.py` 等 5 个新测试文件 | 见第 9 节 | 全部 |

---

## 8. 怎么跑起来验证

### 离线（不需要任何外部服务）

```bash
make eval-tools          # 期望：22/22 = 1.0000
make test-degradation    # 期望：通过（flag 全关 = 阶段 2 行为）
make eval-time           # 期望：22/22（阶段 2 回归哨兵）
make test                # 期望：全部通过
make lint                # 期望：ruff + mypy 全绿
make web-typecheck       # 期望：前端构建通过
```

### 需要真实环境

```bash
docker compose --profile stage3 up -d   # db + redis
make migrate                            # 0005：HNSW 索引
make seed-demo
make eval-ablation                      # Recall@5 / MRR
make cache-stats                        # 缓存命中率（需先有真实工具调用）
```

实测基线（真实库）：**204 passed / 0 skipped**，约 37 秒。

---

## 9. 踩过的坑

### 9.1 `make test` 在 Windows 上找不到 `app` 包

```text
ModuleNotFoundError: No module named 'app'
```

Makefile 原本写 `cd backend && pytest`。`python -m pytest` 会把**当前目录**加进 `sys.path`，
于是 `app` 包和 `tests/` 下的 `conftest`、`fakes` 都能导入；直接敲 `pytest` 在
Windows（PowerShell / Git Bash）下就会炸。Linux 靠 rootdir 约定侥幸能跑，
**所以这个坑只在 Windows 上暴露**。已改成 `python -m pytest`。

### 9.2 全量测试 559 秒 → 20 秒

9 个标 `db` 的用例在**被 skip 之前**先连数据库，asyncpg 默认连接超时 60 秒，
而端口被丢弃时不回 RST → 9 × 60s 全花在"注定跳过"的用例上。

修法（`tests/conftest.py` 的 `db_reachable()`）：先 0.5 秒裸 TCP 探端口 →
通了才 `ping_database()` 且外包 2 秒超时 → 结果进程内记忆化。该段 540s → 3.9s。

### 9.3 端口 OPEN ≠ 数据库可用

Docker Desktop 被手动暂停时，5432 仍然显示 OPEN（有进程占着），但握手永远超时。
**别用"端口通不通"判断数据库是否就绪**，要看 `docker compose ps`。

### 9.4 工具证据被静默丢弃

`_hits_from_result` 要求每个 item 自带 `text`，而 `PoiItem.to_dict()` 没有这个字段 →
证据全被跳过，表现为"工具明明调用成功了却没有任何证据"。
修法是两道保险：poi_tool 每条带 text + 节点侧 `_describe_poi()` 兜底。

### 9.5 负例里混进了触发词

"帮我照着上次那趟再规划一个类似的**路线**"——"路线"是路线类触发词。
负例文本必须逐条检查，这种错误只有真跑评测才会暴露。

### 9.6 过采样写成 `max()` 会失效

`candidate_k = max(top_k, top_k × oversample)` 让 `oversample=1` 失去意义，
"候选里没有符合条件的文档"这条风险路径就永远测不到。必须是纯乘数。

### 9.7 mypy 的 Liskov 报错与 ruff UP046

`BaseTool._run(validated: BaseModel)` 的子类收窄参数触发 Liskov 报错 → 基类改用类型变量
`InputT`；`Generic[InputT]` 又触发 UP046（target py312）→ 改用 PEP 695 `class BaseTool[InputT]`。

### 9.8 假绿的"默认态"断言

`feature_flags.clean_settings()` 原用 `Settings(_env_file=None)` 断言"开关默认关闭"，
但它只跳过 `.env` 文件，**进程环境变量照读**（实测 `TOOLS_ENABLED=true` 下仍返回 True）。
改成 `Settings.model_construct()` 才是真正的出厂设置。

---

## 10. 动手练习

**练习 1**：把 `retrieval_lexical_weight` 从 `0.35` 改成 `0.2`，跑 `make eval-ablation`。
观察混合档的 Recall@5 是否向过滤档（0.809）靠拢——这是第 1 节那个 0.047 差距的直接验证。

**练习 2**：在 `data/tool_cases.jsonl` 里加一条负例"我上次那趟去了哪些地方？"，
跑 `make eval-tools` 确认仍达标；再加一条**含触发词**的负例，看准确率怎么掉，
体会"负例必须真的不含触发词"。

**练习 3**：把 `filter_min_hits` 改成 `999`（等于永远触发兜底）跑消融，
看 `+结构化过滤` 档是否退化回纯向量（0.619）。

**练习 4**：在 `.env` 里设 `TOOLS_ENABLED=true` 后问"乌镇附近有什么景点？"，
看前端是否出现"正在查地点…"→ 结果卡片（带"来自高德地图，不是你的照片记录"标注）。

---

## 11. 已知边界

三条如实说明，不粉饰：

1. **W3-2 停在 MockMapProvider**（无高德 Key）。结构已对齐，但**没有验证过真实 POI ID 的形状**
   与真实配额行为，阶段 6 接入时需补一次真实验收。
2. **W3-3 的"缓存命中率 ≥60%"未做真实统计**。原子性、TTL、降级都已单测覆盖，
   但真实命中率需要真实工具调用量才有意义，目前 `make cache-stats` 只能给出合成序列的演示值。
3. **混合档 Recall@5（0.762）低于仅过滤档（0.809）**。这是词面路搅动排序的老问题，
   阶段 4 的 RRF（W4-3）是正解，阶段 3 内建议先调权重缓解。

---

## 12. 下一阶段

阶段 4（W4-1 ~ W4-3）：Memory + LangGraph。与本阶段直接相关的三件事：

1. **W4-1 短期记忆策略**：`SessionStore` 已把存储做好，策略（保留几轮、衰减、冲突处理）在这里补
2. **W4-2 LangGraph 迁移**：`ToolCallNode` 的设计已经考虑到搬移，可原样成为 `call_tool` 节点
3. **W4-3 RRF 融合**：替换 `_fuse` 的加权求和，顺带解决第 11.3 条那个 0.047 的差距
