# 阶段 3 增量 PRD：工具调用 + Reranker + 缓存

> **本文是增量 PRD，不是全新 PRD。** 只描述「阶段 3 相对阶段 2 的变更」与「本轮范围边界」。
> 未在本文中提及的行为，一律沿用阶段 2 现状，不得顺手改动。

| 项 | 内容 |
| --- | --- |
| 项目 | 旅忆地图 TravelMemory（`F:/project/travelmemory`） |
| Language | 中文 |
| 阶段 | 阶段 3（W3-1 / W3-2 / W3-3 / W3-4） |
| 规格来源 | 设计文档[第十一章](../旅忆地图%20TravelMemory.md)（阶段 3，第 935~946 行）、[§10.1 工程指标](../旅忆地图%20TravelMemory.md)（855~875 行）、[§7.6 工具](../旅忆地图%20TravelMemory.md)（655~667 行）、[§3.2 Redis 四级用途](../旅忆地图%20TravelMemory.md)（191~200 行） |
| 红线来源 | [PROJECT.md](../PROJECT.md) 第 2 / 3 / 4 / 5 / 8 条 |
| 当前基线 | 阶段 0~2 已跑通：`make eval-time` 22/22；消融 Recall@5 0.714 → 0.857；`pytest` 99 passed / 9 skipped；ruff + mypy 全绿；`npm run build` 通过 |
| 当前 prompt_version | `pv2026.10-stage2.1`（`backend/app/agent/prompts/loader.py:16`） |
| 文档状态 | 待评审（评审人：主理人 → 架构师 → 工程师） |

### 读法

- **§4 需求池**和**§6 仓库排错范围界定**是本文的核心，工程师照着做即可。
- §6 存在的唯一目的是**防止"优化"无限蔓延吃掉阶段 3 的交付**。看到 §6.3 延后清单里的东西，请先做完阶段 3 再说。
- §7 是待确认问题，每条都给了默认建议。**没有共识时按默认建议执行，不要停下来等。**

---

## 1. 产品目标

> **一句话**：阶段 3 交付后，Agent 从"只靠你的照片记忆回答"变成"**能动手查地图**"——
> 问"乌镇附近还有什么古镇""从西湖到灵隐怎么走"时，它会真的去查高德并把查到的事实连同照片证据一起**可引用地**答给你；
> 同时因为缓存与重排的引入，**重复问题明显变快、排序明显更准**。

三个正交的产品目标：

| # | 目标 | 用户可感知的变化 | 失败判据 |
| --- | --- | --- | --- |
| G1 | **能动手** | 涉及地图事实（POI / 路线 / 距离）的问题不再靠记忆编造，而是调用地图工具拿到真实 POI ID 后作答 | 涉及未来 / 实时信息仍用记忆作答（违反设计文档 §7.4 拒答策略第 4 行） |
| G2 | **更快** | 同一个 POI / 路线问题第二次问，明显更快；重复索引不重复付费 | POI / 路线类工具缓存命中率 < 60%（§10.1） |
| G3 | **更准** | 检索结果的排序质量提升（MRR ≥ 0.6），且不会因为引入 HNSW / Reranker 而丢掉正确答案 | 建索引后 Recall@5 下降（W3-4 出口条件） |

**非目标（本轮不做）**：不做地图渲染、不做行程编辑、不做记忆（阶段 4）、不做隐私分享（阶段 6）、不做语义推荐与 ZK（阶段 7）。

---

## 2. 用户故事

> 格式：`作为……，我希望……，以便……`。每条的「验收要点」是**可测量的**，与 §4 需求池的验收标准一一对应。

| # | 用户故事 | 验收要点 |
| --- | --- | --- |
| **US-1** | 作为**想找附近景点**的用户，我希望问"乌镇附近还有什么古镇"时 Agent 真的去查地图，以便我拿到真实存在、可导航的地点 | 回答中每个 POI 带**高德 POI ID**；无 Key / 服务失败时明确降级提示"地图服务暂不可用"，**不编造地点名** |
| **US-2** | 作为**要规划路线**的用户，我希望问"从西湖到灵隐寺怎么走"时 Agent 算出距离与时长，以便我判断值不值得去 | 回答含 distance / duration，且二者**来自工具返回值**（trace 中 `tool_call.output` 可核）；人工验收清单 A-3 能拿到真实值 |
| **US-3** | 作为**担心被编造**的用户，我希望地图工具查到的事实**也能点开出处**，以便我知道这句话是查来的还是我照片里的 | 工具结果参与**统一编号的证据序列**，`[n]` 可点开；引用率仍为 **100%**（§10.1）；UI 上明确标注"来自高德地图，非你的照片记录" |
| **US-4** | 作为**重复提问**的用户，我希望第二次问同一个 POI / 路线时立刻出结果，以便我不必等一次外网请求 | 第二次调用 `ToolCallLog.cache_hit = True`，`latency_ms` 显著低于首次；批量序列缓存命中率 ≥ **60%** |
| **US-5** | 作为**被检索质量困扰**的用户，我希望最相关的记忆排在最前面，以便我第一眼看到的就是对的那条 | MRR ≥ **0.6**（§10.1）；`make eval-ablation` 的 Recall@5 **不低于**阶段 2 基线 |
| **US-6** | 作为**没有外网/没有 Key**的用户，我希望关掉所有新功能后系统行为和阶段 2 一模一样，以便离线也能正常问答 | `make test-degradation` 通过（三个新 flag 全关 → 阶段 2 指标不退化）；`LLM_PROVIDER=mock` + `EMBEDDING_PROVIDER=hashing` 全链路可跑 |
| **US-7** | 作为**看得到过程**的用户，我希望在答案出来之前就看到"正在查地图：search_poi(...)"，以便我知道它在干活而不是卡住 | SSE 新增 `tool_start` / `tool_result` 事件；前端在首个 `delta` 之前渲染工具状态条；P95 首字延迟仍 **< 3s**（§10.1） |

---

## 3. 范围总览

### 3.1 本轮做

| 块 | 内容 | 依赖 | 出口条件（设计文档原文） |
| --- | --- | --- | --- |
| **W3-1** | `tools/` 框架：base、registry、Pydantic schema、超时、重试、日志 | 阶段 2 | 工具选择准确率 ≥ 0.9 |
| **W3-2** | `search_poi` / `calculate_route` + 高德接入 + 坐标转换 | W3-1 | 返回高德 POI ID；转换有缓存与分批 |
| **W3-3** | Redis 四级用途落地（API 缓存 / Embedding 缓存 / 会话态 / 幂等锁与预算计数） | 阶段 2 | 缓存命中率 ≥ 60%；预算计数原子生效 |
| **W3-4** | Reranker 接入 + HNSW 索引与"结果不足回退精确扫描" | W3-1 | 建索引后 Recall@5 不下降 |

推进顺序：**W3-1 → W3-2 → W3-3 → W3-4**（单人开发按块号；W3-1 是共同依赖，必须先完成）。
块内严格遵循「**出口条件优先于完成度**」：一个块没满足出口条件，不允许开下一块。

### 3.2 本轮明确不做（写在这里是为了防止蔓延）

- 前端地图渲染 / 图层栈 / MapPage / SharePage / `components/ui` / `components/business` / `workers/`（阶段 6）
- Memory 三层、`nodes/graph/`、LangGraph 迁移、RRF 融合（阶段 4）
- `Place` / `Trip` / `TripItem` / `ChatSession` / `Message` / `MemoryItem` 表及对应 service（阶段 4/6）
- 隐私分享、语义推荐、ZK、链（阶段 7，feature flag 全关）
- Capacitor 跨端、真机验证（阶段 6）
- 任何"顺手重构""顺手统一风格""顺手补类型注解"（见 §6.3）

---

## 4. 需求池（P0 / P1 / P2）

> 优先级定义：**P0 = 不做则阶段 3 不成立**（出口条件或硬指标无法判定）；
> **P1 = 做则本阶段更完整，不做不阻塞出口条件**；**P2 = 加分项，可整块放弃**。
> 验收标准全部可测量：给出**阈值 / 命令 / 断言**三者之一或组合。

### 4.1 W3-1 · `tools/` 框架（共同依赖，最先完成）

| ID | 需求 | 优先级 | 验收标准（可测量） |
| --- | --- | --- | --- |
| **R3-1.1** | `tools/base.py`：`BaseTool` 抽象 + 统一 `ToolResult`。字段：`name` / `description` / `args_schema`(Pydantic v2) / `timeout_sec` / `max_retries` / `degradable` / `run()`；`ToolResult` 含 `ok` / `data` / `error` / `degraded` / `latency_ms` / `cache_hit` | P0 | 单测断言：① 入参 schema 校验失败 → `ok=False` 且**不抛异常**；② 超时 → `degraded=True` 且**不抛异常**；③ 重试次数 ≤ `max_retries`；④ `degradable=True` 的工具失败后重试 ≤ 1 次（设计文档 §7.6） |
| **R3-1.2** | `tools/registry.py`：注册 / 按名发现 / 统一调用入口 / 导出给 LLM 的 JSON Schema 列表 | P0 | 单测断言：① `list_specs()` 覆盖全部已注册工具的 `name + description + json_schema`；② 调用未注册工具名 → 返回明确错误，**不得 KeyError**；③ 同一名字重复注册 → 明确报错 |
| **R3-1.3** | 工具选择：新增 `nodes/pipeline/tool_call.py`，由 `query + ParsedQuery` 决定「调哪个工具 / 不调」 | P0 | `make eval-tools`（新增命令，离线可跑）在 `backend/data/tool_cases.jsonl` ≥ 20 条用例上**工具选择准确率 ≥ 0.9**；其中 ≥ 5 条"不该调工具"的负例（如"我去年秋天去过哪些古镇？"）必须 100% 不触发工具 |
| **R3-1.4** | 统一调用日志：每次工具调用经 `trace.record_tool_call(...)`（该方法已存在于 `observability.py:146`）落 `ToolCallLog` + JSONL | P0 | 断言：一次工具调用 → JSONL 中恰好一行 `type=tool_call`；`cache_hit` 字段**必须出现在每一行**（缓存命中率的验收依赖它，`models/traces.py:65` 已注明"缓存命中率 ≥ 60% 是验收指标"） |
| **R3-1.5** | `core/feature_flags.py`：阶段 3 三个新开关的统一入口（`TOOLS_ENABLED` / `RERANK_ENABLED` / `HNSW_ENABLED`），**默认全关** | P0 | ① `is_enabled("tools")` 默认 `False`；② 三个开关全关时 `make test-degradation` 通过；③ 开关值可从环境变量覆盖（走 `core/config.py`，禁止其他模块直接 `os.getenv`） |
| **R3-1.6** | 工具选择的 mock 可测性：mock LLM 下工具选择走**规则兜底**（关键词：附近/周边/怎么走/多远/路线/距离 → 对应工具），保证无 Key 也能跑评测 | P0 | `LLM_PROVIDER=mock` 下 `make eval-tools` 仍可跑出准确率数值（不依赖真实 LLM） |

### 4.2 W3-2 · `search_poi` / `calculate_route` + 高德接入

| ID | 需求 | 优先级 | 验收标准（可测量） |
| --- | --- | --- | --- |
| **R3-2.1** | `services/map_service.py`：高德 Web 服务 API 封装（POI 搜索 / 路径规划 / 坐标转换），统一走 `httpx`，带超时与重试；**无 Key / 调用失败 → 降级不抛错** | P0 | 单测断言：`AMAP_API_KEY` 为空时返回 `degraded` 结果，**不抛异常、不中断管道**；有 Key 时按人工验收清单 A-2 / A-3 拿到真实数据 |
| **R3-2.2** | `tools/poi_tool.py` `search_poi`：入参 `keyword / city / location`；**返回必须含高德 POI ID**（字段 `poi_id`，落库时对应 `Place.amap_poi_id`） | P0 | 单测断言：mock provider 下返回的每条 POI `poi_id` **非空**（用固定 fixture 也必须带 id）；人工验收 A-2：真实调用返回的 id 能在高德控制台反查 |
| **R3-2.3** | `tools/route_tool.py` `calculate_route`：入参 `origin / destination / mode`；返回 `distance_m / duration_sec / steps` | P0 | 单测断言：mock 下返回结构完整且 `distance_m > 0`；人工验收 A-3：真实调用与高德官方示例量级一致 |
| **R3-2.4** | 坐标转换 WGS84 ↔ GCJ-02：**分批（≤ 40 点/次，见 §13.1）+ 缓存** | P0 | 单测断言：① 120 点 → 批次数 == `ceil(120/40) == 3`；② 同一批坐标第二次转换 `cache_hit=True`；③ 所有位置字段**显式带 `coord_system`**（PROJECT.md 第 2 条，违反即 CI 拦截） |
| **R3-2.5** | 工具返回事实的可引用化：工具结果转成 `EvidenceBlock`（`source="map_tool"`）并**并入统一 `[n]` 编号序列** | P0 | 断言：含工具证据时 `citation_rate` 与 `self_check` 口径**完全不变**（`utils/citation.py` 不改）；人工验收 A-5：回答中地图事实句带 `[n]` 且可点开 |
| **R3-2.6** | 限流：单用户单位时间内工具调用上限（保护高德配额） | P1 | 单测：超限返回 `degraded` 而非抛错；上限值可通过 `core/config.py` 配置 |

### 4.3 W3-3 · Redis 四级用途落地

| ID | 需求 | 优先级 | 验收标准（可测量） |
| --- | --- | --- | --- |
| **R3-3.1** | `core/cache.py`：定义 `CacheClient` Protocol + `RedisCache` 实现 + `InMemoryCache` 实现（同一 Protocol） | P0 | ① **不装 redis 包也能 import 并通过全部测试**（环境约束 E1）；② `redis` 依赖标为可选，`requirements.txt` 注明"未安装时自动降级为内存实现"；③ 两个实现对同一组单测（get/set/ttl/delete/incr/setnx）行为一致 |
| **R3-3.2** | ① 外部 API 结果缓存：POI **7d** / 路线 **1d** / 逆地编 **30d**（§3.2） | P0 | 单测断言三类 TTL 的数值；断言 key 结构含 `用途前缀 + 归一化入参哈希`；每次读取把 `cache_hit` 写进 `ToolCallLog` |
| **R3-3.3** | ② Embedding 结果缓存：key = `sha256(content + model)`，永久（LRU） | P0 | 断言：同一批文档连续跑两次 `make index-batch`，第二次 embedding provider **调用次数 = 0**（用计数型 mock 断言） |
| **R3-3.4** | ③ 短期会话 + 流式状态：List（滑动窗口）+ Hash，TTL 24h | P1 | 单测：窗口长度 ≤ 配置值；TTL = 24h；**阶段 3 无消费方**（短期记忆在阶段 4），故只落地存储层与单测，不接入管道 |
| **R3-3.5** | ④ 幂等锁 + 预算计数原子化：`SETNX` + `INCR/EXPIRE`，替换 `core/llm.py` 中进程内 `BudgetGuard`（`config.py:51` 已注明"阶段 3 换成 Redis 原子计数"） | P0 | 单测：① 10 个协程并发 `INCR` → 最终计数 **== 10**（原子性）；② 达到 `LLM_DAILY_BUDGET_CENTS` 后调用被拒（`LLMBudgetExceeded` 已存在于 `core/exceptions.py`）并被降级处理，不 500 |
| **R3-3.6** | 缓存命中率可观测：`make cache-stats` 输出「POI / 路线类工具缓存命中率」 | P0 | 构造"10 次查询中 8 次重复"的序列 → 输出命中率 **≥ 0.6**（§10.1 阈值 60%）；统计口径 = `ToolCallLog.cache_hit / 总调用` |
| **R3-3.7** | 缓存边界：只缓存外部调用与会话态，**不缓存用户数据本体**（§3.2 注） | P0 | 单测/评审：断言写入缓存的 key 只落在 4 类前缀内（api / emb / sess / lock）；无用户 `content` 进入缓存值 |

### 4.4 W3-4 · Reranker + HNSW + 回退

| ID | 需求 | 优先级 | 验收标准（可测量） |
| --- | --- | --- | --- |
| **R3-4.1** | `rag/reranker.py`：定义 `Reranker` Protocol + 远程/本地实现 + **no-op 实现** | P0 | ① `RERANK_ENABLED=false` 时排序结果与阶段 2 **逐条一致**（对阶段 2 快照做对比断言）；② 无 Key / 无本地模型时自动 no-op，**不抛错** |
| **R3-4.2** | 接入 `rag/retriever.py`：reranker 接管 `_fuse` 的输出作为**候选集**并重排（阶段 2 文档 §11 已明确该分工） | P0 | `make eval-ablation`：Recall@5 **不低于**阶段 2 基线（内存替身 0.857；真实库取基线值），MRR **≥ 0.6**（§10.1） |
| **R3-4.3** | HNSW 索引（新增 alembic `0005_stage3_hnsw.py`）+ **"结果不足回退精确扫描"** | P0 | ① 迁移脚本可在**不连库**情况下编译通过（沿用 `test_document_service_sql.py` 的编译断言风格）；② 回退逻辑单测：过滤后返回 0 条 → 触发精确扫描 → 结果非空（这是 §3.5 "可能返回 0 条"风险的直接对策）；③ 建索引后 `make eval-ablation` Recall@5 **不下降** |
| **R3-4.4** | 延迟与成本守护：rerank 调用 ≤ 1 次/次问答，候选 ≤ 20 条；成本计入 `token_usage` | P1 | 断言：单次问答 rerank 调用次数 == 1；`make eval --full` 输出单次成本 **< ¥0.05**、P95 首字 **< 3s**（§10.1） |
| **R3-4.5** | HNSW 建索引的可观测：`retrieve_debug` 记录本次走了「HNSW」还是「回退精确扫描」 | P2 | trace 中 `retrieve_debug.index_path ∈ {hnsw, exact_fallback}`；排查"为什么搜不到"时可直接看该字段 |

---

## 5. 约束与假设（会直接决定架构，请先读这一节）

| # | 约束 | 来源 | 对实现的硬要求 |
| --- | --- | --- | --- |
| **E1** | **开发环境没有 Docker / Redis / PostgreSQL**（Docker Desktop 起不来，5432 / 6379 不通） | 现状 | 阶段 3 的**所有新代码必须能在无 Redis / 无真实 LLM Key / 无高德 Key 的情况下跑通测试**：Redis 用 `InMemoryCache` 替身（或 fakeredis 思路），外部服务用 mock provider。真实环境验证**单列为人工验收清单（§8.3）**，不进 CI |
| **E2** | **离线占位模式必须保住**：`LLM_PROVIDER=mock` + `EMBEDDING_PROVIDER=hashing` 时整条链路可跑 | `tests/conftest.py:17-28`（已全局设置）+ README §4 | 新增任何节点/工具都不得破坏该组合；`pytest` 全绿即视为保住 |
| **E3** | **扩展模块走 feature flag，默认全关；失败必须降级而非抛错** | PROJECT.md 第 8 条 | `TOOLS_ENABLED` / `RERANK_ENABLED` / `HNSW_ENABLED` 默认 `false`；关闭时行为**逐条等同阶段 2**（由 `make test-degradation` 守护，见 O-1）。缓存层同理加 `CACHE_ENABLED`（关闭时走内存/直通） |
| **E4** | **依赖方向单向** `agent → nodes → tools → services → models` | PROJECT.md 第 4 条 | `tools/` **不得反向 import** `agent/` 与 `api/`；CI 加 import 检查（若已有则不重复建） |
| **E5** | **坐标系统一**：WGS84 存储 / GCJ-02 展示，字段显式带 `coord_system` | PROJECT.md 第 2 条 | 工具返回值、缓存值、前端展示值都必须带该字段 |
| **E6** | **引用率 100% / 无证据拒答 100%** | PROJECT.md 第 3 条 + §10.1 | 工具返回的**事实也必须可引用**（R3-2.5），且不得绕开 `utils/citation.py` 的既有口径 |
| **E7** | **改 prompt 必须同时改 `agent/prompts/` 文件与 `PROMPT_VERSION`** | PROJECT.md §五 | 本阶段新增/修改 `tool_select.md`、`generate.md`（工具证据段）时，版本从 `pv2026.10-stage2.1` 升到 **`pv2026.10-stage3.1`**（建议值，见 §7-Q4） |
| **E8** | **可观测统一由 `observability.py` 写**，节点只调 `trace.step(...)` / `trace.record_tool_call(...)` | PROJECT.md 第 5 条 | 工具层不得自行 new `ToolCallLog` |
| **E9** | 高德 API 有配额与 **40 点/次**的转换上限 | §13.1 | 坐标转换必须分批 + 缓存（R3-2.4）；Web 服务 Key 只走后端，不落前端 |
| **E10** | 阶段严格串行，上一阶段出口条件未满足不得进入下一阶段 | PROJECT.md 第 7 条 | 阶段 2 的「真实库 `make eval-ablation`」待办因 E1 无法执行，**明确列为延后项（§6.3 D-1）**，不阻塞阶段 3 |

---

## 6. 仓库优化与排错的范围界定 ★

> 本节回答一个问题：**"优化仓库并排除 BUG"这件事，做到哪里为止？**

### 6.1 判定标准（四问，按顺序过一遍）

遇到一个疑似问题，依次问：

1. **是否影响正确性？** —— 会不会让答案/引用/拒答出错？→ 是：**纳入**
2. **是否影响阶段 3 的可测性？** —— 会不会让某个出口条件无法被验证（本地跑不出数值）？→ 是：**纳入**
3. **是否违反 PROJECT.md 红线？** —— 依赖方向、feature flag + 降级、引用率、坐标系统一、trace 单一写入口？→ 是：**纳入**
4. **是否只是风格 / 偏好 / 顺手？** —— 命名、格式、类型注解补全、目录整理、"看起来不优雅"？→ 是：**延后**

补充判据：**不能追溯到一个 §10.1 指标、一个出口条件、或一条 PROJECT.md 红线的改动，一律进延后清单。**

### 6.2 纳入范围（本轮要修的，共 5 项，做完即止）

| 编号 | 现象（现状证据） | 四问判定 | 归属 | 处理 |
| --- | --- | --- | --- | --- |
| **O-1** | `Makefile` 有 `test-degradation` 目标，但 `backend/tests/test_degradation.py` **不存在** → 该命令必然失败；而它是 PROJECT.md 第 8 条"扩展合入 main 的前提"的唯一守卫 | 违反红线(3) + 影响可测性(2) | W3-1 收口（随 R3-1.5 一起交） | 新建 `tests/test_degradation.py`：在 `TOOLS/RERANK/HNSW/CACHE` 全关下断言阶段 2 的既有指标（Recall@5 / MRR / 引用率 / 拒答率）不退化；`make test-degradation` 可通过 |
| **O-2** | `backend/app/core/` 缺 `feature_flags.py`（设计文档 §5 目录列出了它），阶段 3 的 3 个新开关没有统一入口 | 违反红线(3) | W3-1（= R3-1.5） | 建 `core/feature_flags.py`；已有 4 个阶段 7 开关（`privacy_enabled` 等）一并纳入统一管理 |
| **O-3** | `Makefile` 缺阶段 3 的验证命令（`eval-tools` / `cache-stats`），出口条件无法一键复现 | 影响可测性(2) | 随 W3-1 / W3-3 交付 | 新增 `make eval-tools`、`make cache-stats`；`eval-tools` 复用 `app/agent/eval/runner.py` 的 argparse 风格（加 `--tools` 开关） |
| **O-4** | `backend/requirements.txt` 无 Redis 相关条目，且未说明"未安装时降级" | 影响可测性(2) | W3-3（= R3-3.1） | `redis` 标为**可选**并注明降级路径；**测试不引入新依赖**（`InMemoryCache` 自实现同 Protocol）；如确需验证 TTL/原子性语义，可选引入 `fakeredis` 并放进 test 依赖区（默认不引） |
| **O-5** | 阶段 3 新增测试若依赖真实 Redis/Postgres 会直接违反 E1，导致本机跑不了 | 影响可测性(2) | 全程 | **新规**：阶段 3 新增测试**一律不得新增 skip**；pytest 结论从「99 passed / 9 skipped」变为「≥ 99 passed / **skipped ≤ 9**」 |

### 6.3 明确延后清单（本轮不碰，写完阶段 3 再说）

| 编号 | 事项 | 为什么延后 | 重新评估的触发条件 |
| --- | --- | --- | --- |
| **D-1** | 在**真实数据库**上跑 `make eval-ablation`（阶段 2 遗留待办） | E1：本机无 Docker，5432 不通；阶段 3 出口条件可用内存替身判定 | 拿到可用的 Docker/Postgres 环境；或阶段 5（W5-1）要求全部指标一键跑出 |
| **D-2** | 9 个 `pytest` skip 用例转真实库覆盖 | 同上；且它们都是已通过的阶段 0~2 逻辑，不影响阶段 3 正确性 | 同上；阶段 6（W6-1）导入链路需要真实库时 |
| **D-3** | 填充空目录：`agent/memory/`、`nodes/graph/`、`frontend/src/components/{ui,business}`、`pages/MapPage`、`pages/SharePage`、`workers/` | 它们是阶段 4 / 6 的**预留位**，不是缺陷；提前填充等于跨阶段并行（违反 PROJECT.md 第 7 条） | 进入阶段 4 / 6 |
| **D-4** | `Place` / `Trip` / `TripItem` / `ChatSession` / `Message` / `MemoryItem` 表与对应 service | 阶段 4（Memory）/ 阶段 6（地图相册）才需要；阶段 3 只用 `Photo` / `Document` / `ToolCallLog` | 阶段 4 / 6 |
| **D-5** | 隐私 / 语义 / ZK / 链相关（含 `privacy/`、`zk/`、`data/semantic_tags.yaml` 的接入） | 阶段 7，flag 全关；`Makefile` 里的 `semantic-batch` / `anchor-batch` / `exp-*` 同样不动（阶段 16 已删除这些指向空模块的 target） | 阶段 7 |
| **D-6** | 检索阈值/权重调优（`retrieval_min_score`、融合权重） | **除非**阶段 3 出口条件判定为退化，否则属于"效果调优"，不在排错范围 | `make eval-ablation` 显示 Recall@5 相对阶段 2 下降时 |
| **D-7** | 代码风格类：命名统一、日志格式统一、类型注解补全、目录微调、历史注释润色 | 判定标准第 4 问 → 只是风格 | 无（除非 CI 首次接入 lint 规则） |
| **D-8** | 修复 Docker / WSL 被安全策略拦截 | **不在代码仓库范围**，属环境问题 | 用户自行解决环境 |
| **D-9** | 前端组件库（shadcn/ui）、Capacitor、真机验证 | 阶段 6 | 阶段 6 |
| **D-10** | 阶段 2 文档 §11 提到的"浏览器实测四条验收动作" | 属 D-1/D-9 同类（需真实环境），且阶段 3 会在同一页面上迭代 UI | 阶段 3 前端改完后一次性实测（§8.3 A-6） |

### 6.4 常见误判（避免把"预留位"当 bug 修）

- `backend/app/tools/` 目前是**空目录**（连 `__init__.py` 都没有）。这不是缺陷，是设计文档 §5 为阶段 3 预留的位置 —— 本阶段要**填**它（W3-1），而不是"修复"它。
- `agent/memory/`、`nodes/graph/`、前端空目录同理（D-3）。
- 阶段 7 的 4 个开关在 `config.py` 里已声明但无消费方 —— 不是 bug，是 flag 默认全关的正常状态。

### 6.5 止血规则（防止蔓延）

1. **时间盒**：O-1 ~ O-5 是排错的全部内容，**共 5 项，不做第 6 项**。
2. **停止条件**：任何修复如果需要修改阶段 0~2 已通过的 99 个测试的**断言逻辑**（而非扩展断言），立即停下来上报主理人，不得自行放宽断言。
3. **提交粒度**：排错单独立 `fix` / `chore` 提交，与 `feat(tools)` / `feat(rag)` 等阶段 3 提交分开，便于回滚。
4. **新增延后项**：本轮新发现的问题一律先写进 §6.3 延后清单并在阶段文档里登记，不在本轮处理。

---

## 7. UI / 交互变更（前端对话页）

> 范围仅限 `frontend/src/pages/AgentPage/` + `types/agent.ts` + `services/api/agent.ts`。
> **不碰** MapPage / SharePage / components（D-9）。

### 7.1 后端事件协议扩展（SSE）

在阶段 2 的 `meta / parsed / evidence / delta / restart / done / error` 之上新增两个事件：

| 事件 | payload | 说明 |
| --- | --- | --- |
| `tool_start` | `{tool_name, args, attempt}` | 工具开始调用。必须在**首个 `delta` 之前**发出，否则用户看不到"在干活" |
| `tool_result` | `{tool_name, ok, degraded, cache_hit, latency_ms, summary}` | 工具结束。`summary` 是给前端直接展示的简短摘要（POI 名称列表 / 距离与时长），**前端不做二次解析** |

`evidence` 事件的 `EvidenceBlock` 增加 `source` 字段：`"memory"`（照片/文档证据）| `"map_tool"`（地图工具返回）。

### 7.2 前端新增元素

| # | 元素 | 位置 | 行为 | 对应需求 |
| --- | --- | --- | --- | --- |
| 1 | **工具状态条** `ToolCallBar` | 气泡上方，流式期间 | 收到 `tool_start` 显示「正在查地图：search_poi(西湖, 杭州)」+ spinner；收到 `tool_result` 后收起 | US-7 / R3-1.3 |
| 2 | **工具结果卡片** `ToolResultCard` | 气泡下方，证据列表之前 | 展示 POI 名称 + 地址 + **POI ID（可折叠）**，或路线的距离/时长；**顶部固定标注"来自高德地图，不是你的照片记录"** | US-1 / US-2 / R3-2.2 |
| 3 | **缓存命中徽章** | 工具结果卡片右上角 | `cache_hit=true` 时显示「缓存命中」小徽章；hover 提示"省了一次 API 调用" | US-4 / R3-3.6 |
| 4 | **证据来源标注** | `EvidenceList` 每条 | 按 `source` 分色/加标签：`memory` → 「你的记录」；`map_tool` → 「地图工具」。**两者共用同一套 `[n]` 编号**，点击行为一致 | US-3 / R3-2.5 |
| 5 | **降级提示** | 工具结果卡片 / 气泡底部 | `degraded=true` 时显示「地图服务暂不可用，以下内容仅基于你的照片记录」 | E3 / US-6 |

### 7.3 引用率红线如何处理"工具返回的事实"（关键设计）

**默认方案：工具结果并入统一 `[n]` 编号序列。**

- 即：工具返回的每条事实在 `build_context` 阶段被追加为 `EvidenceBlock(source="map_tool")`，编号接在记忆证据之后继续递增（如记忆 `[1][2]` + 工具 `[3][4]`）。
- **收益**：`utils/citation.py`、`self_check`、`metrics.citation_rate` 的口径**完全不变**，引用率 100% 的硬校验天然覆盖工具事实，不需要为"工具引用"单开一套规则。
- **成本**：`max_index` 变大，`self_check` 越界检查照常生效。
- **替代方案**（不推荐）：给工具结果单独一套编号（如 `[T1]`）。这会让 `CITATION_RE`、越界检查、评测口径全部需要改，风险高，且容易在阶段 5 埋雷。

prompt 侧配套（E7）：`prompts/generate.md` 增加一句约束「地图工具返回的事实同样必须带 `[n]`，编号指向对应的工具证据块」。

### 7.4 前端验收

- `npm run build` 通过（沿用阶段 2 的 `make web-typecheck`）。
- `types/agent.ts` 新增 `ToolStartPayload` / `ToolResultPayload`，`EvidenceBlock` 增加 `source` —— 后端改事件结构时在编译期暴露。
- 人工验收 A-6：浏览器实测 US-7 的"先看到正在查地图，再看到答案"。

---

## 8. 验收矩阵

### 8.1 离线可跑（本机无 Docker / 无 Key，CI 可执行）

| 命令 | 期望 | 对应 |
| --- | --- | --- |
| `cd backend && pytest -q` | **≥ 99 passed，skipped ≤ 9**（只增不减） | O-5 |
| `cd backend && ruff check . && mypy app` | 全绿 | 基线 |
| `make eval-tools` | 工具选择准确率 **≥ 0.9**（≥20 条用例，含 ≥5 条负例） | W3-1 出口 |
| `make eval-ablation` | Recall@5 **不下降**（基线：内存替身 0.857）；MRR **≥ 0.6** | W3-4 出口 / §10.1 |
| `make eval --full` | 引用率 **1.00**、拒答正确 **1.00** | §10.1 |
| `make test-degradation` | 通过（三/四个新 flag 全关 → 阶段 2 指标不退化） | O-1 / E3 |
| `make cache-stats` | 构造序列命中率 **≥ 0.6** | W3-3 出口 / §10.1 |
| `make eval-time` | 22/22 | 不退化 |
| `make web-typecheck` | 通过 | §7.4 |

### 8.2 阶段 3 出口条件对照表

| 块 | 出口条件（设计文档原文） | 本 PRD 的判定方式 |
| --- | --- | --- |
| W3-1 | 工具选择准确率 ≥ 0.9 | `make eval-tools` |
| W3-2 | 返回高德 POI ID；转换有缓存与分批 | 单测（POI ID 非空、批次 == 3、二次转换 cache_hit）+ A-2 |
| W3-3 | 缓存命中率 ≥ 60%；预算计数原子生效 | `make cache-stats` + 并发 INCR 单测 |
| W3-4 | 建索引后 Recall@5 不下降 | `make eval-ablation` + 回退扫描单测 |

### 8.3 人工验收清单（需要真实环境，不进 CI）

| # | 动作 | 期望 | 前置 |
| --- | --- | --- | --- |
| **A-1** | `make up` + `make migrate` + `make eval-ablation` | 拿真实库 Recall@5 数值，替换内存替身基线（补 D-1） | Docker 可用 |
| **A-2** | 配 `AMAP_API_KEY` 后调用 `search_poi("古镇", "嘉兴")` | 返回真实高德 POI ID，可在高德控制台反查 | Key |
| **A-3** | 配 Key 后调用 `calculate_route(西湖 → 灵隐寺)` | distance/duration 与高德官方量级一致 | Key |
| **A-4** | `docker compose --profile stage3 up -d` 后重跑 `make cache-stats` | 真实 Redis 下命中率 ≥ 60%；TTL 符合 7d/1d/30d | Redis |
| **A-5** | 浏览器问"乌镇附近还有什么古镇" | 出现工具状态条 → POI 卡片（标注"来自高德地图"）→ 回答中地图事实句带 `[n]` 可点开 | Key |
| **A-6** | 浏览器实测阶段 2 遗留的四条流式验收动作 + US-7 | 首字可见、可中断、FilterChips 正常、工具状态条先于答案出现 | 无 |
| **A-7** | `make eval --full` | 单次成本 **< ¥0.05**、P95 首字 **< 3s** | 真实 LLM Key |

---

## 9. 待确认问题（附默认建议，按建议执行，不停等）

| # | 问题 | 默认建议 |
| --- | --- | --- |
| **Q1** | 阶段 3 仍是纯 Python 管道（阶段 4 才迁 LangGraph），"工具选择"该放在管道的哪个位置？ | **在 `retrieve` 之后、`build_context` 之前新增 `nodes/pipeline/tool_call.py`**；由 `TOOLS_ENABLED` 控制，关闭时节点直接跳过（行为等同阶段 2）。这样阶段 4 迁 graph 时可原样搬成 `call_tool` 节点 |
| **Q2** | Redis 不可用时的降级形态：`InMemoryCache` 还是"直接不缓存"？ | **默认 `InMemoryCache`（进程内字典）**：行为一致、可测 TTL 语义、不新增依赖；生产环境 `CACHE_ENABLED=true` + 真实 Redis 时才走 Redis 实现。多进程部署时内存缓存会失效，但本项目为单用户单进程部署，可接受（在阶段文档中写明） |
| **Q3** | 是否引入 `fakeredis`？ | **默认不引入**。自写 `InMemoryCache` 即可覆盖 TTL / INCR / SETNX 语义的单测。若实现中发现 fakeredis 能显著降低替身成本，可作为 **test 依赖**引入（需评估网络可安装性） |
| **Q4** | `PROMPT_VERSION` 改成什么？ | `pv2026.10-stage2.1` → **`pv2026.10-stage3.1`**。规则沿用现有 `stage<N>.<小版本>` 形态；本阶段若多次改 prompt，递增小版本（`stage3.2`…），保证 `make eval-compare` 能归因 |
| **Q5** | 是否新建 `Place` 表落高德 POI ID？ | **本阶段不建表**。POI ID 只存在于工具返回值、证据块与 `ToolCallLog` 中；`Place` 表随阶段 6 地图相册一起建（D-4）。理由：阶段 3 的出口条件只要求"返回 POI ID"，建表会引入阶段 6 的数据模型决策 |
| **Q6** | 缓存命中率的统计范围：只算 POI/路线，还是算全部工具？ | **严格按 §10.1 原文："POI / 路线类工具"**。`make cache-stats` 只统计 `search_poi` 与 `calculate_route`；其他工具调用不计入分母，避免用高频低价值调用把命中率刷高 |
| **Q7** | 工具失败时，是"降级继续（用记忆作答）"还是"拒答"？ | **默认降级继续**：工具结果为空 → 正常走记忆检索 → 生成时 prompt 声明"工具不可用，仅依据你的记录"。仅当**记忆检索也为空**时才拒答（沿用 §7.4 拒答策略）。这符合 PROJECT.md 第 8 条"失败降级而非抛错" |
| **Q8** | O-1（`test_degradation.py`）是否算"排错"还是"阶段 3 需求"？ | **两者都是**：它是阶段 2 遗留的断裂命令（排错），同时是 E3 的守卫（阶段 3 需求）。**归入 W3-1 交付**，不单独开一个工作块 |

---

## 10. 交付物清单

| # | 交付物 | 说明 |
| --- | --- | --- |
| 1 | `backend/app/tools/{__init__,base,registry,poi_tool,route_tool}.py` | W3-1 / W3-2 |
| 2 | `backend/app/services/map_service.py` | W3-2（高德 Web 服务封装 + 坐标转换） |
| 3 | `backend/app/core/{cache,feature_flags}.py` | W3-3 / O-2（含 `InMemoryCache` 与 `RedisCache` 同 Protocol） |
| 4 | `backend/app/agent/rag/reranker.py` + `retriever.py` 接入 | W3-4 |
| 5 | `backend/alembic/versions/0005_stage3_hnsw.py` | W3-4（HNSW 索引） |
| 6 | `backend/app/agent/nodes/pipeline/tool_call.py` + `prompts/tool_select.md` | W3-1（Q1）+ E7 版本 bump |
| 7 | `backend/data/tool_cases.jsonl`（≥20 条） | W3-1 评测集（含 ≥5 条负例） |
| 8 | `backend/tests/test_degradation.py` + 各块新增单测 | O-1 / O-5 |
| 9 | `frontend/src/pages/AgentPage/{ToolCallBar,ToolResultCard}.tsx` + `types/agent.ts` 扩展 | §7 |
| 10 | `Makefile` 新增 `eval-tools` / `cache-stats` | O-3 |
| 11 | `docs/stages/stage3-tools-rerank.md` | **阶段复盘文档**（按 `docs/stages/README.md` 的固定 7 节结构：目标与出口条件 / 核心原理 / 逐块讲解 / 文件清单 / 怎么跑 / 踩过的坑 / 动手练习） |
| 12 | `README.md` 阶段 3 段落 + `docs/stages/README.md` 表格状态更新 | 沿用惯例 |

---

## 11. 不做的事（再强调一次）

- 不做地图渲染、行程编辑、照片导入（阶段 6）
- 不做 Memory、LangGraph、RRF（阶段 4）
- 不做隐私 / 语义 / ZK / 链（阶段 7）
- **不做 §6.3 延后清单里的任何一项**
- 不为"看起来更好"而改阶段 0~2 已通过的代码
